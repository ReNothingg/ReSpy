from __future__ import annotations

import asyncio
import hashlib
import html
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from aiogram.enums import ParseMode
from aiogram.types import BufferedInputFile
from telethon import TelegramClient, events, functions, types, utils

from app.telegram_bot import TelegramArchive, telegram_user_link, utcnow


logger = logging.getLogger(__name__)
VIEW_ONCE_TTL = 0x7FFFFFFF
INITIAL_QTS_REWIND = 300
DIFFERENCE_HEARTBEAT_SECONDS = 10


class MtprotoBusinessArchive:
    def __init__(self, archive: TelegramArchive):
        self.archive = archive
        self.settings = archive.settings
        self.db = archive.db
        self._catch_up_event = asyncio.Event()
        self._difference_lock = asyncio.Lock()
        self._difference_state: types.updates.State | None = None

    def request_catch_up(self) -> None:
        self._catch_up_event.set()

    async def run(self) -> None:
        if not self.settings.telegram_api_id or not self.settings.telegram_api_hash:
            logger.info("MTProto archive is disabled")
            return
        session_path = (
            self.settings.mtproto_session_path
            or self.settings.database_path.parent / "respy_mtproto"
        )
        session_path.parent.mkdir(parents=True, exist_ok=True)

        while True:
            client = TelegramClient(
                str(session_path),
                self.settings.telegram_api_id,
                self.settings.telegram_api_hash,
            )

            async def raw_handler(update: Any) -> None:
                await self.handle_update(client, update)

            client.add_event_handler(
                raw_handler,
                events.Raw(types.UpdateBotNewBusinessMessage),
            )
            catch_up_task: asyncio.Task[None] | None = None
            try:
                await client.start(bot_token=self.settings.bot_token)
                me = await client.get_me()
                logger.info(
                    "mtproto_business_archive_started bot=%s",
                    getattr(me, "id", None),
                )
                state = await client(functions.updates.GetStateRequest())
                self._difference_state = types.updates.State(
                    pts=state.pts,
                    qts=max(1, state.qts - INITIAL_QTS_REWIND),
                    date=state.date,
                    seq=state.seq,
                    unread_count=state.unread_count,
                )
                await self._recover_missed_updates(client)
                catch_up_task = asyncio.create_task(
                    self._catch_up_loop(client),
                    name="mtproto-business-catch-up",
                )
                await client.run_until_disconnected()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("MTProto business archive stopped; retrying")
                await asyncio.sleep(10)
            finally:
                if catch_up_task:
                    catch_up_task.cancel()
                    await asyncio.gather(catch_up_task, return_exceptions=True)
                self._difference_state = None
                await client.disconnect()

    async def _catch_up_loop(self, client: TelegramClient) -> None:
        while True:
            try:
                await asyncio.wait_for(
                    self._catch_up_event.wait(),
                    timeout=DIFFERENCE_HEARTBEAT_SECONDS,
                )
            except TimeoutError:
                pass
            self._catch_up_event.clear()
            try:
                await self._recover_missed_updates(client)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Failed to recover missed MTProto updates")

    async def _recover_missed_updates(self, client: TelegramClient) -> None:
        async with self._difference_lock:
            state = self._difference_state
            if state is None:
                return

            recovered = 0
            relevant = 0
            for _ in range(20):
                difference = await client(
                    functions.updates.GetDifferenceRequest(
                        pts=state.pts,
                        date=state.date,
                        qts=state.qts,
                        qts_limit=100,
                    )
                )
                if isinstance(difference, types.updates.DifferenceEmpty):
                    state = types.updates.State(
                        pts=state.pts,
                        qts=state.qts,
                        date=difference.date,
                        seq=difference.seq,
                        unread_count=state.unread_count,
                    )
                    break
                if isinstance(difference, types.updates.DifferenceTooLong):
                    logger.warning(
                        "MTProto difference is too long at pts=%s; resetting state",
                        difference.pts,
                    )
                    state = await client(functions.updates.GetStateRequest())
                    break

                updates = difference.other_updates
                recovered += len(updates)
                for update in updates:
                    if self._is_relevant_update(update):
                        relevant += 1
                    await self.handle_update(client, update)

                if isinstance(difference, types.updates.DifferenceSlice):
                    state = difference.intermediate_state
                    continue
                state = difference.state
                break
            else:
                logger.warning("MTProto difference pagination limit reached")

            self._difference_state = state
            if recovered or relevant:
                logger.info(
                    "mtproto_updates_recovered updates=%s relevant=%s qts=%s",
                    recovered,
                    relevant,
                    state.qts,
                )

    @staticmethod
    def _is_relevant_update(update: Any) -> bool:
        if not isinstance(update, types.UpdateBotNewBusinessMessage):
            return False
        message = getattr(update, "message", None)
        if isinstance(
            getattr(message, "action", None),
            types.MessageActionScreenshotTaken,
        ):
            return True
        media = getattr(message, "media", None)
        return bool(
            isinstance(media, types.MessageMediaPhoto)
            and media.ttl_seconds
            and media.photo
            and not bool(getattr(message, "out", False))
        )

    async def handle_update(
        self, client: TelegramClient, update: Any
    ) -> None:
        message = getattr(update, "message", None)
        connection_id = getattr(update, "connection_id", None)
        if not connection_id or message is None:
            return
        try:
            if isinstance(
                getattr(message, "action", None),
                types.MessageActionScreenshotTaken,
            ):
                await self._handle_screenshot(connection_id, message)
                return

            media = getattr(message, "media", None)
            if (
                not isinstance(media, types.MessageMediaPhoto)
                or not media.ttl_seconds
                or not media.photo
                or bool(getattr(message, "out", False))
            ):
                return
            await self._handle_expiring_photo(
                client, connection_id, message, media
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Failed to process MTProto business update connection=%s message=%s",
                connection_id,
                getattr(message, "id", None),
            )

    async def _ensure_connection_and_chat(
        self, connection_id: str, chat_id: int, sent_at: str
    ) -> dict[str, Any]:
        if await self.db.connection_owner(connection_id) is None:
            await self.db.upsert_connection(
                {
                    "id": connection_id,
                    "owner_user_id": self.settings.owner_telegram_id,
                    "owner_name": str(self.settings.owner_telegram_id),
                    "is_enabled": True,
                    "rights": {},
                    "updated_at": utcnow(),
                }
            )
        chat = await self.db.get_chat(connection_id, chat_id)
        if chat:
            return chat
        await self.db.upsert_chat(
            connection_id,
            {
                "id": chat_id,
                "type": "private" if chat_id > 0 else "group",
                "first_name": str(chat_id),
            },
            sent_at,
        )
        return await self.db.get_chat(connection_id, chat_id) or {
            "chat_id": chat_id,
            "title": str(chat_id),
        }

    @staticmethod
    def _sent_at(message: Any) -> str:
        sent_at = getattr(message, "date", None) or datetime.now(UTC)
        if sent_at.tzinfo is None:
            sent_at = sent_at.replace(tzinfo=UTC)
        return sent_at.astimezone(UTC).isoformat(timespec="seconds")

    @staticmethod
    def _peer_id(peer: Any) -> int | None:
        if peer is None:
            return None
        try:
            return int(utils.get_peer_id(peer))
        except (TypeError, ValueError):
            return None

    async def _handle_expiring_photo(
        self,
        client: TelegramClient,
        connection_id: str,
        message: Any,
        media: types.MessageMediaPhoto,
    ) -> None:
        chat_id = self._peer_id(message.peer_id)
        if chat_id is None:
            return
        sent_at = self._sent_at(message)
        chat = await self._ensure_connection_and_chat(
            connection_id, chat_id, sent_at
        )
        sender_id = self._peer_id(getattr(message, "from_id", None)) or chat_id
        title = str(chat.get("title") or chat_id)
        ttl = int(media.ttl_seconds or 0)
        row_id, inserted = await self.db.store_message(
            {
                "connection_id": connection_id,
                "chat_id": chat_id,
                "message_id": int(message.id),
                "sender_id": sender_id,
                "sender_name": title,
                "sender_username": chat.get("username"),
                "is_outgoing": False,
                "sent_at": sent_at,
                "received_at": utcnow(),
                "text": None,
                "caption": getattr(message, "message", None) or None,
                "content_type": "photo",
                "media_name": f"view_once_{message.id}.jpg",
                "media_mime": "image/jpeg",
                "raw": {
                    "source": "mtproto",
                    "message_id": int(message.id),
                    "ttl_seconds": ttl,
                    "view_once": ttl == VIEW_ONCE_TTL,
                },
            }
        )
        if not inserted:
            stored = await self.db.get_message(row_id)
            if stored and stored.get("media_path"):
                return

        payload = await client.download_media(message, file=bytes)
        if not isinstance(payload, bytes) or not payload:
            await self.db.update_media(
                row_id, None, None, "Telegram не отдал одноразовое фото"
            )
            return
        if len(payload) > self.settings.max_media_bytes:
            await self.db.update_media(
                row_id,
                None,
                len(payload),
                "Файл превышает настроенный лимит",
            )
            return

        connection_slug = hashlib.sha256(connection_id.encode()).hexdigest()[:12]
        folder = self.settings.media_dir / connection_slug / str(chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"{message.id}_view_once.jpg"
        destination.write_bytes(payload)
        await self.db.update_media(row_id, str(destination), len(payload), None)

        kind = "один просмотр" if ttl == VIEW_ONCE_TTL else f"{ttl} сек."
        sender_link = telegram_user_link(title, sender_id)
        caption = (
            "🔥 <b>Одноразовое фото сохранено</b>\n\n"
            f"👤 {sender_link}\n"
            f"⏳ Режим: <b>{html.escape(kind)}</b>"
        )
        try:
            await self.archive.bot.send_photo(
                self.settings.owner_telegram_id,
                BufferedInputFile(
                    payload,
                    filename=f"view_once_{message.id}.jpg",
                ),
                caption=caption,
                parse_mode=ParseMode.HTML,
                reply_markup=self.archive._chat_keyboard(
                    connection_id,
                    chat_id,
                    await self.db.chat_archive_stats(connection_id, chat_id),
                ),
            )
        except Exception:
            logger.exception("Failed to send saved one-time photo")
            await self.archive._notify(
                self.settings.owner_telegram_id,
                caption + "\n\nФото сохранено в веб-архиве.",
                reply_markup=self.archive._chat_keyboard(
                    connection_id,
                    chat_id,
                    await self.db.chat_archive_stats(connection_id, chat_id),
                ),
            )
        logger.info(
            "mtproto_expiring_photo_saved connection=%s chat=%s message=%s ttl=%s",
            connection_id,
            chat_id,
            message.id,
            ttl,
        )

    async def _handle_screenshot(
        self, connection_id: str, message: Any
    ) -> None:
        chat_id = self._peer_id(message.peer_id)
        if chat_id is None:
            return
        sent_at = self._sent_at(message)
        chat = await self._ensure_connection_and_chat(
            connection_id, chat_id, sent_at
        )
        actor_id = self._peer_id(getattr(message, "from_id", None))
        actor_name = (
            "Вы"
            if actor_id == self.settings.owner_telegram_id
            else str(chat.get("title") or chat_id)
        )
        _, inserted = await self.db.store_message(
            {
                "connection_id": connection_id,
                "chat_id": chat_id,
                "message_id": int(message.id),
                "sender_id": actor_id,
                "sender_name": actor_name,
                "sender_username": chat.get("username"),
                "is_outgoing": actor_id == self.settings.owner_telegram_id,
                "sent_at": sent_at,
                "received_at": utcnow(),
                "text": "Сделан снимок экрана",
                "caption": None,
                "content_type": "service",
                "raw": {
                    "source": "mtproto",
                    "message_id": int(message.id),
                    "action": "screenshot_taken",
                },
            }
        )
        if not inserted:
            return
        await self.archive._notify(
            self.settings.owner_telegram_id,
            "📸 <b>Сделан снимок экрана</b>\n\n"
            f"👤 {telegram_user_link(actor_name, actor_id)}",
            reply_markup=self.archive._chat_keyboard(
                connection_id,
                chat_id,
                await self.db.chat_archive_stats(connection_id, chat_id),
            ),
        )
        logger.info(
            "mtproto_screenshot connection=%s chat=%s message=%s actor=%s",
            connection_id,
            chat_id,
            message.id,
            actor_id,
        )

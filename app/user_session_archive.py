from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, functions, types

from app.mtproto_archive import MtprotoBusinessArchive
from app.telegram_bot import TelegramArchive


logger = logging.getLogger(__name__)


def session_file(path: Path) -> Path:
    return path if path.suffix == ".session" else Path(f"{path}.session")


class UserSessionArchive:
    def __init__(
        self,
        archive: TelegramArchive,
        business_archive: MtprotoBusinessArchive,
    ):
        self.archive = archive
        self.business_archive = business_archive
        self.settings = archive.settings
        self.db = archive.db
        self.client: TelegramClient | None = None
        self._notification_lock = asyncio.Lock()

    async def run(self) -> None:
        path = self.settings.mtproto_user_session_path
        if (
            path is None
            or not self.settings.telegram_api_id
            or not self.settings.telegram_api_hash
        ):
            raise RuntimeError("MTProto user session is not configured")

        client = TelegramClient(
            str(path),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
        )

        async def raw_handler(update: Any) -> None:
            await self.handle_update(client, update)

        client.add_event_handler(
            raw_handler,
            events.Raw(types.UpdateNewMessage),
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise RuntimeError(
                    "MTProto user session is not authorized; "
                    "run python -m app.auth_user_session"
                )
            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    "MTPROTO_USER_SESSION_PATH contains a bot session, not a user session"
                )
            if int(me.id) != self.settings.owner_telegram_id:
                raise RuntimeError(
                    "MTProto user session belongs to a different Telegram account"
                )
            logger.info(
                "mtproto_user_archive_started user=%s",
                me.id,
            )
            self.client = client
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        finally:
            self.client = None
            await client.disconnect()

    async def set_chat_muted(
        self,
        connection_id: str,
        chat_id: int,
        muted: bool,
    ) -> bool:
        client = self.client
        if client is None or not client.is_connected():
            return False

        async with self._notification_lock:
            try:
                peer = await client.get_input_entity(chat_id)
                notify_peer = types.InputNotifyPeer(peer=peer)
                override = await self.db.chat_notification_override(
                    connection_id,
                    chat_id,
                )
                if muted:
                    created_override = False
                    if override is None:
                        current = await client(
                            functions.account.GetNotifySettingsRequest(
                                peer=notify_peer
                            )
                        )
                        previous = getattr(current, "mute_until", None)
                        if previous is not None and previous.tzinfo is None:
                            previous = previous.replace(tzinfo=UTC)
                        created_override = (
                            await self.db.save_chat_notification_override(
                                connection_id,
                                chat_id,
                                previous.isoformat() if previous else None,
                                datetime.now(UTC).isoformat(timespec="seconds"),
                            )
                        )
                    try:
                        await client(
                            functions.account.UpdateNotifySettingsRequest(
                                peer=notify_peer,
                                settings=types.InputPeerNotifySettings(
                                    mute_until=datetime.fromtimestamp(
                                        2_147_483_647,
                                        UTC,
                                    )
                                ),
                            )
                        )
                    except Exception:
                        if created_override:
                            await self.db.clear_chat_notification_override(
                                connection_id,
                                chat_id,
                            )
                        raise
                else:
                    previous_mute_until = (
                        override.get("previous_mute_until")
                        if override
                        else None
                    )
                    restore_until = (
                        datetime.fromisoformat(previous_mute_until)
                        if previous_mute_until
                        else datetime.fromtimestamp(0, UTC)
                    )
                    if restore_until.tzinfo is None:
                        restore_until = restore_until.replace(tzinfo=UTC)
                    await client(
                        functions.account.UpdateNotifySettingsRequest(
                            peer=notify_peer,
                            settings=types.InputPeerNotifySettings(
                                mute_until=restore_until
                            ),
                        )
                    )
                    if override:
                        await self.db.clear_chat_notification_override(
                            connection_id,
                            chat_id,
                        )
                logger.info(
                    "chat_notifications_updated chat=%s muted=%s",
                    chat_id,
                    muted,
                )
                return True
            except Exception:
                logger.warning(
                    "Could not update chat notifications chat=%s muted=%s",
                    chat_id,
                    muted,
                    exc_info=True,
                )
                return False

    async def handle_update(
        self,
        client: TelegramClient,
        update: Any,
    ) -> None:
        if not isinstance(update, types.UpdateNewMessage):
            return
        message = update.message
        media = getattr(message, "media", None)
        if (
            not isinstance(media, types.MessageMediaPhoto)
            or not media.ttl_seconds
            or not media.photo
            or bool(getattr(message, "out", False))
        ):
            return
        connection_id = await self.db.enabled_connection_for_owner(
            self.settings.owner_telegram_id
        )
        if not connection_id:
            logger.warning(
                "Ignored user-session ephemeral photo without enabled "
                "Business connection message=%s",
                getattr(message, "id", None),
            )
            return
        logger.info(
            "mtproto_user_expiring_photo_received connection=%s message=%s ttl=%s",
            connection_id,
            getattr(message, "id", None),
            media.ttl_seconds,
        )
        await self.business_archive._handle_expiring_photo(
            client,
            connection_id,
            message,
            media,
        )

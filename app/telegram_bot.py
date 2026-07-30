from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import mimetypes
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.filters import Command
from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    FSInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from app.config import Settings
from app.database import Database


logger = logging.getLogger(__name__)
router = Router(name="respy")


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def user_name(user: Any | None) -> str:
    if user is None:
        return "Неизвестный отправитель"
    name = " ".join(
        part for part in (getattr(user, "first_name", None), getattr(user, "last_name", None))
        if part
    )
    return name or getattr(user, "username", None) or str(getattr(user, "id", ""))


def telegram_user_link(name: str, user_id: Any | None) -> str:
    escaped_name = html.escape(name)
    try:
        numeric_id = int(user_id)
    except (TypeError, ValueError):
        return f"<b>{escaped_name}</b>"
    if numeric_id <= 0:
        return f"<b>{escaped_name}</b>"
    return f'<a href="tg://user?id={numeric_id}"><b>{escaped_name}</b></a>'


def content_type(message: Message) -> str:
    for field in (
        "gift_upgrade_sent",
        "unique_gift",
        "gift",
        "photo",
        "video",
        "animation",
        "document",
        "audio",
        "voice",
        "video_note",
        "sticker",
        "contact",
        "location",
        "venue",
        "poll",
        "dice",
    ):
        if getattr(message, field, None):
            return field
    return "text" if message.text is not None else "service"


@dataclass(slots=True)
class MediaInfo:
    file_id: str
    file_unique_id: str
    name: str
    mime: str | None
    size: int | None
    extension: str


def media_info(message: Message) -> MediaInfo | None:
    kind = content_type(message)
    media: Any | None = None
    if kind == "photo" and message.photo:
        media = message.photo[-1]
    elif kind in {
        "video",
        "animation",
        "document",
        "audio",
        "voice",
        "video_note",
        "sticker",
    }:
        media = getattr(message, kind, None)
    if media is None:
        return None

    file_name = getattr(media, "file_name", None)
    mime = getattr(media, "mime_type", None)
    extension = Path(file_name).suffix if file_name else ""
    if not extension and mime:
        extension = mimetypes.guess_extension(mime) or ""
    if not extension:
        extension = {
            "photo": ".jpg",
            "video": ".mp4",
            "animation": ".mp4",
            "voice": ".ogg",
            "video_note": ".mp4",
            "sticker": ".webp",
        }.get(kind, ".bin")
    name = file_name or f"{kind}{extension}"
    return MediaInfo(
        file_id=media.file_id,
        file_unique_id=media.file_unique_id,
        name=name,
        mime=mime,
        size=getattr(media, "file_size", None),
        extension=extension[:10],
    )


def message_data(message: Message, owner_id: int) -> dict[str, Any]:
    sender = message.from_user
    raw = message.model_dump(mode="json", exclude_none=True)
    return {
        "connection_id": message.business_connection_id,
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "sender_id": sender.id if sender else None,
        "sender_name": user_name(sender),
        "sender_username": sender.username if sender else None,
        "is_outgoing": bool(sender and sender.id == owner_id),
        "sent_at": message.date.astimezone(UTC).isoformat(timespec="seconds"),
        "received_at": utcnow(),
        "text": message.text,
        "caption": message.caption,
        "content_type": content_type(message),
        "raw": raw,
    }


def display_body(message: dict[str, Any] | Message) -> str:
    if isinstance(message, Message):
        body = message.text or message.caption
        kind = content_type(message)
    else:
        body = message.get("text") or message.get("caption")
        kind = message.get("content_type", "message")
    if body:
        return body
    labels = {
        "gift": "🎁 Telegram-подарок",
        "gift_upgrade_sent": "💎 Оплачен апгрейд подарка",
        "unique_gift": "💎 Уникальный Telegram-подарок",
        "photo": "📷 Фото",
        "video": "🎬 Видео",
        "animation": "🎞 GIF",
        "document": "📎 Файл",
        "audio": "🎵 Аудио",
        "voice": "🎤 Голосовое сообщение",
        "video_note": "⭕ Видеосообщение",
        "sticker": "🏷 Стикер",
        "contact": "👤 Контакт",
        "location": "📍 Геопозиция",
        "poll": "📊 Опрос",
    }
    return labels.get(kind, f"Сообщение ({kind})")


def gift_data(message: Message, owner_id: int) -> dict[str, Any] | None:
    connection_id = message.business_connection_id
    if not connection_id:
        return None
    receiver = message.receiver_user
    sender = message.from_user
    if receiver:
        direction = "received" if receiver.id == owner_id else "sent"
        counterparty = sender if direction == "received" else receiver
    else:
        direction = "sent" if sender and sender.id == owner_id else "received"
        counterparty = None if direction == "sent" else sender

    if counterparty is None:
        counterparty_id = message.chat.id
        counterparty_name = (
            message.chat.full_name or message.chat.title or str(message.chat.id)
        )
        counterparty_username = message.chat.username
    else:
        counterparty_id = counterparty.id
        counterparty_name = user_name(counterparty)
        counterparty_username = counterparty.username

    raw = message.model_dump(mode="json", exclude_none=True)
    common = {
        "connection_id": connection_id,
        "chat_id": message.chat.id,
        "message_id": message.message_id,
        "direction": direction,
        "counterparty_id": counterparty_id,
        "counterparty_name": counterparty_name,
        "counterparty_username": counterparty_username,
        "occurred_at": message.date.astimezone(UTC).isoformat(timespec="seconds"),
        "raw": raw,
        "confidence": "confirmed",
    }

    info = message.gift_upgrade_sent or message.gift
    if info:
        status = (
            "upgrade_paid"
            if message.gift_upgrade_sent is not None
            else ("sent" if direction == "sent" else "received")
        )
        event_type = (
            "gift_upgrade_paid"
            if message.gift_upgrade_sent is not None
            else f"gift_{status}"
        )
        return {
            **common,
            "gift_id": info.gift.id,
            "gift_kind": "regular",
            "title": "Telegram Gift",
            "star_count": info.gift.star_count,
            "convert_star_count": info.convert_star_count,
            "upgrade_star_count": info.gift.upgrade_star_count,
            "prepaid_upgrade_star_count": info.prepaid_upgrade_star_count,
            "owned_gift_id": info.owned_gift_id,
            "unique_number": info.unique_gift_number,
            "status": status,
            "event_type": event_type,
            "is_private": bool(info.is_private),
            "event_details": {
                "convert_star_count": info.convert_star_count,
                "prepaid_upgrade_star_count": info.prepaid_upgrade_star_count,
            },
        }

    unique = message.unique_gift
    if unique:
        status_by_origin = {
            "upgrade": "upgraded",
            "gifted_upgrade": "upgraded",
            "transfer": "transferred",
            "resale": "resold",
            "offer": "offer",
        }
        status = status_by_origin.get(unique.origin, "upgraded")
        gift = unique.gift
        return {
            **common,
            "gift_id": gift.gift_id,
            "gift_kind": "unique",
            "title": gift.base_name,
            "unique_name": gift.name,
            "unique_number": gift.number,
            "model_name": gift.model.name,
            "model_rarity": gift.model.rarity_per_mille,
            "symbol_name": gift.symbol.name,
            "symbol_rarity": gift.symbol.rarity_per_mille,
            "backdrop_name": gift.backdrop.name,
            "backdrop_rarity": gift.backdrop.rarity_per_mille,
            "owned_gift_id": unique.owned_gift_id,
            "origin": unique.origin,
            "status": status,
            "event_type": f"gift_{status}",
            "event_details": {
                "origin": unique.origin,
                "last_resale_currency": unique.last_resale_currency,
                "last_resale_amount": unique.last_resale_amount,
            },
        }
    return None


class TelegramArchive:
    def __init__(self, bot: Bot, db: Database, settings: Settings):
        self.bot = bot
        self.db = db
        self.settings = settings

    async def notify_unauthorized_start(self, user: Any) -> None:
        name = html.escape(user_name(user))
        username = (
            f"@{html.escape(user.username)}"
            if getattr(user, "username", None)
            else "не указан"
        )
        await self._notify(
            self.settings.owner_telegram_id,
            "🚨 <b>Попытка доступа к боту</b>\n\n"
            "Пользователь без доступа отправил <code>/start</code>.\n\n"
            f"Имя: <b>{name}</b>\n"
            f"Username: <b>{username}</b>\n"
            f"Telegram ID: <code>{user.id}</code>",
        )
        logger.warning("unauthorized_start user=%s username=%s", user.id, user.username)

    async def handle_connection(self, connection: BusinessConnection) -> None:
        if connection.user.id != self.settings.owner_telegram_id:
            logger.warning(
                "Rejected business connection %s from unexpected user %s",
                connection.id,
                connection.user.id,
            )
            try:
                await self.bot.send_message(
                    connection.user.id,
                    "⛔ Этот экземпляр бота настроен для другого владельца.",
                )
            except Exception:
                logger.exception("Could not warn unexpected business user")
            return

        await self.db.upsert_connection(
            {
                "id": connection.id,
                "owner_user_id": connection.user.id,
                "owner_name": user_name(connection.user),
                "owner_username": connection.user.username,
                "is_enabled": connection.is_enabled,
                "rights": (
                    connection.rights.model_dump(mode="json", exclude_none=True)
                    if connection.rights
                    else {}
                ),
                "updated_at": utcnow(),
            }
        )
        state = "подключён" if connection.is_enabled else "отключён"
        await self._notify(
            connection.user.id,
            f"🛡 <b>Business-архив {state}</b>\n\n"
            "Новые сообщения, изменения и удаления теперь фиксируются локально.",
        )

    async def handle_message(self, message: Message) -> None:
        connection_id = message.business_connection_id
        if not connection_id:
            return
        owner_id = await self.db.connection_owner(connection_id)
        if owner_id != self.settings.owner_telegram_id:
            logger.warning("Ignored message for unknown business connection %s", connection_id)
            return

        data = message_data(message, owner_id)
        chat = message.chat.model_dump(mode="json", exclude_none=True)
        await self.db.upsert_chat(connection_id, chat, data["sent_at"])
        await self._refresh_chat_avatar(connection_id, message.chat.id)
        media = media_info(message)
        if media:
            data.update(
                {
                    "file_id": media.file_id,
                    "file_unique_id": media.file_unique_id,
                    "media_name": media.name,
                    "media_mime": media.mime,
                    "media_size": media.size,
                }
            )
        row_id, inserted = await self.db.store_message(data)
        if inserted and media:
            await self._download_media(
                row_id=row_id,
                connection_id=connection_id,
                chat_id=message.chat.id,
                message_id=message.message_id,
                media=media,
            )
        if message.gift or message.gift_upgrade_sent or message.unique_gift:
            await self._handle_gift(message, owner_id)
        logger.info(
            "business_message connection=%s chat=%s message=%s type=%s outgoing=%s",
            connection_id,
            message.chat.id,
            message.message_id,
            data["content_type"],
            data["is_outgoing"],
        )

    async def _download_media(
        self,
        row_id: int,
        connection_id: str,
        chat_id: int,
        message_id: int,
        media: MediaInfo,
    ) -> None:
        if media.size and media.size > self.settings.max_media_bytes:
            await self.db.update_media(
                row_id, None, media.size, "Файл превышает настроенный лимит"
            )
            return

        connection_slug = hashlib.sha256(connection_id.encode()).hexdigest()[:12]
        folder = self.settings.media_dir / connection_slug / str(chat_id)
        folder.mkdir(parents=True, exist_ok=True)
        filename = f"{message_id}_{media.file_unique_id[:24]}{media.extension}"
        destination = folder / filename
        try:
            await self.bot.download(media.file_id, destination=destination)
            size = destination.stat().st_size
            await self.db.update_media(row_id, str(destination), size, None)
        except Exception as exc:
            logger.exception("Failed to archive Telegram media")
            destination.unlink(missing_ok=True)
            await self.db.update_media(row_id, None, media.size, str(exc)[:300])

    async def _refresh_chat_avatar(self, connection_id: str, chat_id: int) -> None:
        state = await self.db.chat_avatar_state(connection_id, chat_id)
        if state is None:
            return
        now = datetime.now(UTC)
        checked_at = state.get("avatar_checked_at")
        if checked_at:
            try:
                if now - datetime.fromisoformat(checked_at) < timedelta(days=1):
                    return
            except ValueError:
                pass

        checked = now.isoformat(timespec="seconds")
        try:
            if state.get("chat_type") == "private":
                profile_photos = await self.bot.get_user_profile_photos(
                    chat_id, limit=1
                )
                photo = (
                    profile_photos.photos[0][-1]
                    if profile_photos.photos
                    else None
                )
                file_id = getattr(photo, "file_id", None)
                file_unique_id = getattr(photo, "file_unique_id", None)
            else:
                full_chat = await self.bot.get_chat(chat_id)
                photo = getattr(full_chat, "photo", None)
                file_id = getattr(photo, "small_file_id", None)
                file_unique_id = getattr(photo, "small_file_unique_id", None)
            if not photo:
                await self.db.update_chat_avatar(
                    connection_id, chat_id, None, None, checked
                )
                return

            await self.db.update_chat_avatar(
                connection_id,
                chat_id,
                file_id,
                file_unique_id,
                checked,
            )
            logger.info(
                "chat_avatar_reference_updated connection=%s chat=%s",
                connection_id,
                chat_id,
            )
        except Exception:
            logger.warning(
                "Could not refresh chat avatar connection=%s chat=%s",
                connection_id,
                chat_id,
                exc_info=True,
            )
            await self.db.update_chat_avatar(
                connection_id,
                chat_id,
                state.get("avatar_file_id"),
                state.get("avatar_file_unique_id"),
                checked,
            )

    async def cleanup_expired_media_once(self) -> int:
        cutoff = (
            datetime.now(UTC)
            - timedelta(hours=self.settings.media_retention_hours)
        ).isoformat(timespec="seconds")
        media_root = self.settings.media_dir.resolve()
        removed = 0
        while True:
            rows = await self.db.expired_media(cutoff)
            if not rows:
                break
            expired_ids: list[int] = []
            for row in rows:
                path = Path(row["media_path"]).resolve()
                if media_root in path.parents:
                    path.unlink(missing_ok=True)
                else:
                    logger.error("Refused to remove media outside storage: %s", path)
                expired_ids.append(int(row["id"]))
            await self.db.mark_media_expired(
                expired_ids,
                f"Медиа удалено по политике хранения "
                f"({self.settings.media_retention_hours} ч.)",
            )
            removed += len(expired_ids)
            if len(rows) < 500:
                break
        if removed:
            logger.info("expired_media_removed count=%s cutoff=%s", removed, cutoff)
        return removed

    async def media_cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup_expired_media_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Media cleanup cycle failed")
            await asyncio.sleep(self.settings.media_cleanup_interval_seconds)

    async def handle_edit(self, message: Message) -> None:
        connection_id = message.business_connection_id
        if not connection_id:
            return
        owner_id = await self.db.connection_owner(connection_id)
        if owner_id != self.settings.owner_telegram_id:
            return

        data = message_data(message, owner_id)
        data["edited_at"] = utcnow()
        old, changed = await self.db.apply_edit(data)
        is_gift = bool(
            message.gift or message.gift_upgrade_sent or message.unique_gift
        )
        if is_gift:
            await self._handle_gift(message, owner_id)
        logger.info(
            "edited_business_message connection=%s chat=%s message=%s type=%s changed=%s",
            connection_id,
            message.chat.id,
            message.message_id,
            data["content_type"],
            changed,
        )
        if not changed or not old:
            return
        if is_gift:
            return

        chat_name = message.chat.full_name or message.chat.title or str(message.chat.id)
        old_body = html.escape(display_body(old))
        new_body = html.escape(display_body(message))
        text = (
            "🚨 <b>Сообщение изменено</b>\n\n"
            f"👤 <b>{html.escape(chat_name)}</b>\n"
            f"📝 <b>Было:</b>\n<blockquote>{old_body[:3000]}</blockquote>\n"
            f"✨ <b>Стало:</b>\n<blockquote>{new_body[:3000]}</blockquote>"
        )
        stats = await self.db.chat_archive_stats(
            connection_id, message.chat.id
        )
        await self._notify(
            owner_id,
            text,
            reply_markup=self._chat_keyboard(
                connection_id, message.chat.id, stats
            ),
        )

    async def handle_deleted(self, event: BusinessMessagesDeleted) -> None:
        owner_id = await self.db.connection_owner(event.business_connection_id)
        if owner_id != self.settings.owner_telegram_id:
            return

        chat_name = event.chat.full_name or event.chat.title or str(event.chat.id)
        processed: list[tuple[int, dict[str, Any] | None]] = []
        for message_id in event.message_ids:
            stored, changed = await self.db.mark_deleted(
                event.business_connection_id,
                event.chat.id,
                message_id,
                utcnow(),
            )
            if not changed:
                continue
            processed.append((message_id, stored))

        if not processed:
            return
        stats = await self.db.chat_archive_stats(
            event.business_connection_id, event.chat.id
        )
        keyboard = self._chat_keyboard(
            event.business_connection_id, event.chat.id, stats
        )
        logger.info(
            "deleted_business_messages connection=%s chat=%s ids=%s known=%s",
            event.business_connection_id,
            event.chat.id,
            list(event.message_ids),
            sum(1 for _, stored in processed if stored),
        )

        if len(processed) > 1:
            await self._notify(
                owner_id,
                "🧹 <b>В диалоге удалена пачка сообщений</b>\n\n"
                f"👤 {telegram_user_link(chat_name, event.chat.id)}\n"
                f"Удалено сейчас: <b>{len(processed)}</b>",
                reply_markup=keyboard,
            )

        for message_id, stored in processed:
            if stored:
                gift, gift_alert = await self.db.mark_gift_message_deleted(
                    event.business_connection_id,
                    event.chat.id,
                    message_id,
                    utcnow(),
                )
                if gift:
                    if gift_alert:
                        await self._notify(
                            owner_id,
                            "⭐️ <b>Подарок исчез из диалога</b>\n\n"
                            f"👤 <b>{html.escape(gift['counterparty_name'])}</b>\n"
                            f"🎁 {html.escape(gift['unique_name'] or gift['title'])}\n"
                            "⚠️ Telegram не сообщает точную причину. Подарок мог "
                            "быть обменян на Stars, возвращён, улучшен или перенесён.",
                            reply_markup=keyboard,
                        )
                    continue
                body = html.escape(display_body(stored))
                sender_link = telegram_user_link(
                    stored.get("sender_name") or chat_name,
                    stored.get("sender_id") or event.chat.id,
                )
                text = (
                    "🚨 <b>Сообщение удалено</b>\n\n"
                    f"👤 {sender_link}\n"
                    f"<blockquote>{body[:3200]}</blockquote>"
                )
                await self._notify_with_media(
                    owner_id, text, stored, reply_markup=keyboard
                )
            else:
                await self._notify(
                    owner_id,
                    "🚨 <b>Удалено неизвестное сообщение</b>\n\n"
                    f"👤 {telegram_user_link(chat_name, event.chat.id)}\n"
                    f"ID: <code>{message_id}</code>\n\n"
                    "Telegram прислал только ID удаления, но не передал "
                    "исходное сообщение. Так бывает с медиа «один просмотр», "
                    "пропущенными обновлениями или сообщениями до подключения.",
                    reply_markup=keyboard,
                )

    async def _handle_gift(self, message: Message, owner_id: int) -> None:
        data = gift_data(message, owner_id)
        if not data:
            return
        gift, changed, matched_existing = await self.db.record_gift(data)
        if not changed:
            return

        counterparty = html.escape(gift["counterparty_name"] or str(gift["chat_id"]))
        gift_title = html.escape(gift["unique_name"] or gift["title"])
        status = gift["status"]
        if status == "sent":
            text = (
                "🎁 <b>Подарок отправлен — слежение включено</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"⭐️ Стоимость: <b>{gift['star_count'] or '—'} Stars</b>\n"
                f"💱 Можно обменять: <b>{gift['convert_star_count'] or '—'} Stars</b>"
            )
        elif status == "received":
            text = (
                "🎁 <b>Получен Telegram-подарок</b>\n\n"
                f"👤 От: <b>{counterparty}</b>\n"
                f"⭐️ Стоимость: <b>{gift['star_count'] or '—'} Stars</b>"
            )
        elif status == "upgrade_paid":
            text = (
                "💎 <b>Апгрейд подарка оплачен</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"🎁 {gift_title}\n"
                f"⭐️ Предоплата: <b>{gift['prepaid_upgrade_star_count'] or '—'} Stars</b>"
            )
        elif status == "upgraded":
            prefix = (
                "Подаренный вами подарок вскрыли"
                if gift["direction"] == "sent" and matched_existing
                else "Подарок превращён в уникальный"
            )
            text = (
                f"💎 <b>{prefix}</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"🎁 <b>{gift_title} #{gift['unique_number']}</b>\n"
                f"🎨 Модель: {html.escape(gift['model_name'] or '—')} "
                f"({gift['model_rarity'] or '—'}/1000)\n"
                f"✨ Символ: {html.escape(gift['symbol_name'] or '—')} "
                f"({gift['symbol_rarity'] or '—'}/1000)\n"
                f"🌌 Фон: {html.escape(gift['backdrop_name'] or '—')} "
                f"({gift['backdrop_rarity'] or '—'}/1000)"
            )
        else:
            labels = {
                "transferred": "Подарок передан другому владельцу",
                "resold": "Подарок перепродан",
                "offer": "По подарку сработало предложение",
            }
            text = (
                f"🎁 <b>{labels.get(status, 'Событие по подарку')}</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"💎 {gift_title}"
            )
        stats = await self.db.chat_archive_stats(
            gift["connection_id"], gift["chat_id"]
        )
        await self._notify(
            owner_id,
            text,
            reply_markup=self._chat_keyboard(
                gift["connection_id"], gift["chat_id"], stats
            ),
        )

    async def poll_gift_profiles_once(self) -> int:
        targets = await self.db.gift_profile_targets(
            self.settings.gift_poll_batch_size
        )
        checked = 0
        for target in targets:
            recipient_id = int(target["counterparty_id"])
            try:
                public = await self.bot.get_user_gifts(recipient_id, limit=100)
            except TelegramRetryAfter as exc:
                logger.warning(
                    "Gift profile polling rate-limited for %ss", exc.retry_after
                )
                break
            except TelegramBadRequest as exc:
                logger.warning(
                    "Gift profile unavailable user=%s error=%s",
                    recipient_id,
                    exc.message,
                )
                continue

            tracked = await self.db.tracked_gifts_for_recipient(
                target["connection_id"], recipient_id
            )
            available: list[dict[str, Any]] = []
            for item in public.gifts:
                item_gift = item.gift
                available.append(
                    {
                        "gift_id": (
                            getattr(item_gift, "id", None)
                            or getattr(item_gift, "gift_id", None)
                        ),
                        "sender_id": getattr(
                            getattr(item, "sender_user", None), "id", None
                        ),
                        "send_date": int(item.send_date),
                    }
                )

            used: set[int] = set()
            for gift in tracked:
                sent_ts = int(datetime.fromisoformat(gift["first_seen_at"]).timestamp())
                candidates: list[tuple[int, int]] = []
                for index, item in enumerate(available):
                    if index in used or item["gift_id"] != gift["gift_id"]:
                        continue
                    if item["sender_id"] not in {
                        None,
                        self.settings.owner_telegram_id,
                    }:
                        continue
                    distance = abs(item["send_date"] - sent_ts)
                    if distance <= 3600:
                        candidates.append((distance, index))
                if candidates:
                    _, match_index = min(candidates)
                    used.add(match_index)
                    present = True
                else:
                    if public.total_count > len(public.gifts):
                        continue
                    present = False
                gift_row, inventory_event = (
                    await self.db.update_gift_inventory_presence(
                        gift["id"], present, utcnow()
                    )
                )
                if inventory_event:
                    await self._notify_gift_inventory(
                        self.settings.owner_telegram_id,
                        gift_row,
                        inventory_event,
                    )
            checked += 1
            logger.info(
                "gift_inventory_checked user=%s available=%s tracked=%s profile_flags=%s",
                recipient_id,
                public.total_count,
                len(tracked),
                sum(
                    1
                    for item in public.gifts
                    if getattr(item, "is_saved", None) is not None
                ),
            )
        return checked

    async def gift_profile_monitor(self) -> None:
        while True:
            try:
                await self.poll_gift_profiles_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Gift profile monitor cycle failed")
            await asyncio.sleep(self.settings.gift_poll_interval_seconds)

    async def _notify_gift_inventory(
        self, owner_id: int, gift: dict[str, Any], event_type: str
    ) -> None:
        present = event_type == "gift_inventory_present"
        title = html.escape(gift["unique_name"] or gift["title"])
        counterparty = html.escape(gift["counterparty_name"])
        if present:
            text = (
                "↩️ <b>Подарок снова появился в инвентаре</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"🎁 {title}\n"
                "Telegram снова возвращает этот подарок в доступном списке."
            )
        else:
            text = (
                "⭐️ <b>Подарок исчез из инвентаря</b>\n\n"
                f"👤 <b>{counterparty}</b>\n"
                f"🎁 {title}\n"
                "⚠️ Вероятные причины: обмен на Stars, возврат, апгрейд или "
                "перенос. Обычное скрытие с профиля Bot API не показывает."
            )
        stats = await self.db.chat_archive_stats(
            gift["connection_id"], gift["chat_id"]
        )
        await self._notify(
            owner_id,
            text,
            reply_markup=self._chat_keyboard(
                gift["connection_id"], gift["chat_id"], stats
            ),
        )

    async def _notify_with_media(
        self,
        owner_id: int,
        text: str,
        message: dict[str, Any],
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        media_path = message.get("media_path")
        if not media_path or not Path(media_path).is_file():
            await self._notify(owner_id, text, reply_markup=reply_markup)
            return
        file = FSInputFile(media_path, filename=message.get("media_name") or None)
        try:
            kind = message.get("content_type")
            caption = text[:1024]
            if kind == "photo":
                sent = await self.bot.send_photo(
                    owner_id,
                    photo=file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            elif kind == "video":
                sent = await self.bot.send_video(
                    owner_id,
                    video=file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            else:
                sent = await self.bot.send_document(
                    owner_id,
                    document=file,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup,
                )
            logger.info(
                "notification_sent owner=%s message=%s kind=%s markup=%s",
                owner_id,
                getattr(sent, "message_id", None),
                kind,
                reply_markup is not None,
            )
        except Exception:
            logger.exception("Failed to send deleted media copy")
            await self._notify(
                owner_id,
                text + "\n\n⚠️ Медиа сохранено только в панели.",
                reply_markup=reply_markup,
            )

    async def _notify(
        self,
        owner_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        try:
            sent = await self.bot.send_message(
                owner_id,
                text,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
            )
            logger.info(
                "notification_sent owner=%s message=%s kind=text markup=%s",
                owner_id,
                getattr(sent, "message_id", None),
                reply_markup is not None,
            )
        except TelegramBadRequest as exc:
            if reply_markup is None:
                logger.exception("Failed to notify owner")
                return
            logger.warning(
                "Notification markup rejected; retrying without button: %s",
                exc.message,
            )
            try:
                sent = await self.bot.send_message(
                    owner_id,
                    text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info(
                    "notification_sent owner=%s message=%s kind=text markup=false retry=true",
                    owner_id,
                    getattr(sent, "message_id", None),
                )
            except Exception:
                logger.exception("Failed to notify owner after markup retry")
        except Exception:
            logger.exception("Failed to notify owner")

    def _chat_keyboard(
        self,
        connection_id: str,
        chat_id: int,
        stats: dict[str, Any] | None,
    ) -> InlineKeyboardMarkup | None:
        if not self.settings.panel_public_url:
            return None
        count = int((stats or {}).get("total_messages", 0))
        url = (
            f"{self.settings.panel_public_url}/chats/"
            f"{quote(connection_id, safe='')}/{chat_id}"
        )
        button_kwargs: dict[str, Any]
        if url.startswith("https://"):
            button_kwargs = {"web_app": WebAppInfo(url=url)}
        else:
            button_kwargs = {"url": url}
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"📚 Открыть архив чата · {count}",
                        **button_kwargs,
                    )
                ]
            ]
        )

def build_dispatcher(
    archive: TelegramArchive,
    request_mtproto_catch_up: Callable[[], None] | None = None,
) -> Dispatcher:
    dp = Dispatcher()

    if request_mtproto_catch_up:
        @dp.update.outer_middleware()
        async def mtproto_catch_up_middleware(
            handler: Any,
            event: Any,
            data: dict[str, Any],
        ) -> Any:
            request_mtproto_catch_up()
            return await handler(event, data)

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        if not message.from_user:
            return
        if message.from_user.id != archive.settings.owner_telegram_id:
            await archive.notify_unauthorized_start(message.from_user)
            await message.answer("⛔ Этот бот является личным архивом.")
            return
        await message.answer(
            "🛡 <b>ReSpy готов</b>\n\n"
            "Подключи бота в Telegram → Настройки → Telegram Business → Чат-боты.\n"
            "После подключения я буду сохранять сообщения и присылать копии "
            "изменённых и удалённых сообщений. Подарки, апгрейды и исчезновения "
            "подарков тоже отслеживаются.\n\n"
            f"Твой Telegram ID: <code>{message.from_user.id}</code>",
            parse_mode=ParseMode.HTML,
        )

    @router.message(Command("id"))
    async def show_id(message: Message) -> None:
        if message.from_user:
            await message.answer(
                f"Твой Telegram ID: <code>{message.from_user.id}</code>",
                parse_mode=ParseMode.HTML,
            )

    @router.message(Command("gifts"))
    async def gifts_summary(message: Message) -> None:
        if not message.from_user:
            return
        if message.from_user.id != archive.settings.owner_telegram_id:
            await message.answer("⛔ Этот бот является личным архивом.")
            return
        stats = await archive.db.gift_stats()
        await message.answer(
            "🎁 <b>Центр подарков</b>\n\n"
            f"Всего: <b>{stats['total']}</b>\n"
            f"Отправлено: <b>{stats['sent']}</b>\n"
            f"Получено: <b>{stats['received']}</b>\n"
            f"Вскрыто/улучшено: <b>{stats['upgraded']}</b>\n"
            f"Исчезло (причина не подтверждена): <b>{stats['missing']}</b>",
            parse_mode=ParseMode.HTML,
        )

    @router.message(Command("archive"))
    async def open_archive(message: Message) -> None:
        if not message.from_user:
            return
        if message.from_user.id != archive.settings.owner_telegram_id:
            await message.answer("⛔ Этот бот является личным архивом.")
            return
        archive_button: dict[str, Any]
        if archive.settings.panel_public_url.startswith("https://"):
            archive_button = {
                "web_app": WebAppInfo(url=archive.settings.panel_public_url)
            }
        else:
            archive_button = {"url": archive.settings.panel_public_url}
        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="📚 Открыть весь архив",
                        **archive_button,
                    )
                ]
            ]
        )
        stats = await archive.db.dashboard_stats()
        await message.answer(
            "📚 <b>Веб-архив ReSpy</b>\n\n"
            f"Чатов: <b>{stats['chats']}</b>\n"
            f"Сообщений: <b>{stats['messages']}</b>\n"
            f"Удалено: <b>{stats['deleted']}</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=markup,
        )

    @router.business_connection()
    async def business_connection(connection: BusinessConnection) -> None:
        await archive.handle_connection(connection)

    @router.business_message()
    async def business_message(message: Message) -> None:
        await archive.handle_message(message)

    @router.edited_business_message()
    async def edited_business_message(message: Message) -> None:
        await archive.handle_edit(message)

    @router.deleted_business_messages()
    async def deleted_business_messages(event: BusinessMessagesDeleted) -> None:
        await archive.handle_deleted(event)

    dp.include_router(router)
    return dp

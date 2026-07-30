from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

from telethon import TelegramClient, events, types

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
            await client.run_until_disconnected()
        except asyncio.CancelledError:
            raise
        finally:
            await client.disconnect()

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

from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient

from app.config import Settings
from app.user_session_archive import session_file


async def authorize() -> None:
    settings = Settings.from_env()
    path = settings.mtproto_user_session_path
    if path is None or not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError(
            "TELEGRAM_API_ID, TELEGRAM_API_HASH and "
            "MTPROTO_USER_SESSION_PATH are required"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.start(
        phone=lambda: input("Номер Telegram в международном формате: ").strip(),
        code_callback=lambda: input("Код подтверждения Telegram: ").strip(),
        password=lambda: getpass.getpass("Пароль двухэтапной аутентификации: "),
    )
    try:
        me = await client.get_me()
        if getattr(me, "bot", False):
            raise RuntimeError("Авторизован бот, а нужна пользовательская учётная запись")
        if int(me.id) != settings.owner_telegram_id:
            raise RuntimeError(
                f"Сессия принадлежит Telegram ID {me.id}, "
                f"ожидался OWNER_TELEGRAM_ID={settings.owner_telegram_id}"
            )
        file_path = session_file(path)
        if file_path.exists():
            os.chmod(file_path, 0o600)
        print(f"Пользовательская MTProto-сессия готова: {file_path}")
    finally:
        await client.disconnect()


def run() -> None:
    asyncio.run(authorize())


if __name__ == "__main__":
    run()

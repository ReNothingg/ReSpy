from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

import uvicorn

from app.config import Settings
from app.database import Database

if TYPE_CHECKING:
    from aiogram import Bot

    from app.user_session_archive import UserSessionArchive


logger = logging.getLogger(__name__)
RuntimeMode = Literal["all", "bot", "web"]
DATABASE_STARTUP_ATTEMPTS = 6

MTPROTO_DISABLED_MESSAGE = (
    "⚠️ <b>Скрытые фото отключены</b>\n\n"
    "Бот запущен, остальные функции работают. Но скрытые фото "
    "(«один просмотр») сохраняться не будут.\n\n"
    "Чтобы включить их, авторизуй MTProto-сессию:\n"
    "<code>python -m app.auth_user_session</code>"
)


async def notify_mtproto_disabled(
    bot: Bot,
    owner_telegram_id: int,
) -> None:
    try:
        await bot.send_message(
            owner_telegram_id,
            MTPROTO_DISABLED_MESSAGE,
            parse_mode="HTML",
        )
    except Exception:
        logger.exception("Could not notify owner that MTProto is disabled")


async def run_optional_user_archive(
    user_session_archive: UserSessionArchive,
    bot: Bot,
    owner_telegram_id: int,
) -> None:
    try:
        await user_session_archive.run()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception(
            "MTProto user archive is unavailable; continuing without hidden photos"
        )
    else:
        logger.warning(
            "MTProto user archive stopped; continuing without hidden photos"
        )
    await notify_mtproto_disabled(bot, owner_telegram_id)
    await asyncio.Event().wait()


async def serve(mode: RuntimeMode = "all") -> None:
    settings = Settings.from_env()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.log_path:
        settings.log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                settings.log_path,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=handlers,
        force=True,
    )
    settings.media_dir.mkdir(parents=True, exist_ok=True)
    db = Database(settings.database_path)
    if mode in {"all", "bot"}:
        for attempt in range(1, DATABASE_STARTUP_ATTEMPTS + 1):
            try:
                await db.initialize()
                legacy_avatar_root = (
                    settings.database_path.parent / "avatars"
                ).resolve()
                for raw_path in await db.clear_legacy_avatar_paths():
                    path = Path(raw_path).resolve()
                    if legacy_avatar_root in path.parents:
                        path.unlink(missing_ok=True)
                break
            except sqlite3.OperationalError as error:
                database_error = str(error).lower()
                if (
                    "locked" not in database_error
                    and "locking protocol" not in database_error
                ) or attempt == DATABASE_STARTUP_ATTEMPTS:
                    raise
                delay = min(attempt * 2, 10)
                logger.warning(
                    "Database is locked during bot startup; retrying in %ss "
                    "(attempt %s/%s)",
                    delay,
                    attempt,
                    DATABASE_STARTUP_ATTEMPTS,
                )
                await asyncio.sleep(delay)
    elif not settings.database_path.is_file():
        raise RuntimeError(
            "Database is not initialized yet; start app.bot_main before app.web_main"
        )

    bot: Any = None
    dispatcher = None
    server = None
    tasks: set[asyncio.Task[object]] = set()

    if mode in {"all", "web"}:
        from app.web import build_web_app

        if mode == "web":
            from app.telegram_files import BotApiFileDownloader

            bot = BotApiFileDownloader(settings.bot_token)

    if mode in {"all", "bot"}:
        from aiogram import Bot
        from aiogram.types import BotCommand

        from app.mtproto_archive import MtprotoBusinessArchive
        from app.telegram_bot import TelegramArchive, build_dispatcher
        from app.user_session_archive import UserSessionArchive, session_file

        bot = Bot(settings.bot_token)

    if mode in {"all", "web"}:
        web_app = build_web_app(db, settings, bot)
        server = uvicorn.Server(
            uvicorn.Config(
                web_app,
                host=settings.host,
                port=settings.port,
                log_level=settings.log_level.lower(),
                access_log=False,
            )
        )
        tasks.add(asyncio.create_task(server.serve(), name="web-server"))

    if mode in {"all", "bot"}:
        archive = TelegramArchive(bot, db, settings)
        await archive.initialize()
        mtproto_archive = MtprotoBusinessArchive(archive)
        user_session_archive = UserSessionArchive(archive, mtproto_archive)
        archive.chat_notification_manager = user_session_archive
        dispatcher = build_dispatcher(
            archive,
            mtproto_archive.request_catch_up,
        )

        await bot.set_my_commands(
            [
                BotCommand(command="start", description="Статус и подключение"),
                BotCommand(command="id", description="Показать Telegram ID"),
                BotCommand(command="cal", description="Красивый калькулятор"),
                BotCommand(command="mute", description="Удалять входящие в чате"),
                BotCommand(command="unmute", description="Отключить мут в чате"),
                BotCommand(command="gifts", description="Статистика подарков"),
                BotCommand(command="archive", description="Открыть веб-архив"),
            ]
        )

        tasks.update(
            {
                asyncio.create_task(
                    dispatcher.start_polling(
                        bot,
                        allowed_updates=[
                            "message",
                            "business_connection",
                            "business_message",
                            "edited_business_message",
                            "deleted_business_messages",
                        ],
                    ),
                    name="telegram-polling",
                ),
                asyncio.create_task(
                    archive.gift_profile_monitor(), name="gift-profile-monitor"
                ),
                asyncio.create_task(
                    archive.media_cleanup_loop(), name="media-cleanup"
                ),
            }
        )

        if settings.telegram_api_id and settings.telegram_api_hash:
            tasks.add(
                asyncio.create_task(
                    mtproto_archive.run(), name="mtproto-business-archive"
                )
            )
            if (
                settings.mtproto_user_session_path
                and session_file(settings.mtproto_user_session_path).exists()
            ):
                tasks.add(
                    asyncio.create_task(
                        run_optional_user_archive(
                            user_session_archive,
                            bot,
                            settings.owner_telegram_id,
                        ),
                        name="mtproto-user-archive",
                    )
                )
            else:
                logger.warning(
                    "MTProto user archive is disabled: authorize it with "
                    "python -m app.auth_user_session"
                )
                await notify_mtproto_disabled(bot, settings.owner_telegram_id)
        else:
            logger.warning(
                "MTProto archive is disabled: TELEGRAM_API_ID and "
                "TELEGRAM_API_HASH are not configured"
            )
            await notify_mtproto_disabled(bot, settings.owner_telegram_id)

    logger.info("ReSpy runtime started mode=%s tasks=%s", mode, len(tasks))
    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if dispatcher is not None:
            with suppress(RuntimeError):
                await dispatcher.stop_polling()
        if server is not None:
            server.should_exit = True
        if mode in {"all", "bot"}:
            await bot.session.close()


def run_mode(mode: RuntimeMode) -> None:
    asyncio.run(serve(mode))


def run() -> None:
    raw_mode = os.getenv("RESPY_MODE", "all").strip().lower()
    if raw_mode not in {"all", "bot", "web"}:
        raise RuntimeError("RESPY_MODE must be one of: all, bot, web")
    run_mode(cast(RuntimeMode, raw_mode))


if __name__ == "__main__":
    run()

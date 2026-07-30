from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    pass


def _positive_int(name: str, default: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None and default is not None:
        return default
    try:
        value = int(raw or "")
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _optional_positive_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be positive")
    return value


def _resolve_path(raw: str, base: Path) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    bot_token: str
    owner_telegram_id: int
    panel_password: str
    session_secret: str
    host: str
    port: int
    database_path: Path
    media_dir: Path
    max_media_bytes: int
    log_level: str
    telegram_api_id: int | None = None
    telegram_api_hash: str = ""
    mtproto_session_path: Path | None = None
    mtproto_user_session_path: Path | None = None
    panel_public_url: str = ""
    gift_poll_interval_seconds: int = 5
    gift_poll_batch_size: int = 5
    log_path: Path | None = None
    media_retention_hours: int = 24
    media_cleanup_interval_seconds: int = 3600

    @classmethod
    def from_env(cls, base_dir: Path | None = None) -> "Settings":
        base = (base_dir or Path.cwd()).resolve()
        load_dotenv(base / ".env")
        bot_token = os.getenv("BOT_TOKEN", "").strip()
        panel_password = os.getenv("PANEL_PASSWORD", "")
        session_secret = os.getenv("SESSION_SECRET", "")

        if not bot_token or ":" not in bot_token:
            raise ConfigError("BOT_TOKEN is missing or invalid")
        if len(panel_password) < 12:
            raise ConfigError("PANEL_PASSWORD must contain at least 12 characters")
        if len(session_secret) < 32:
            raise ConfigError("SESSION_SECRET must contain at least 32 characters")

        host = os.getenv("HOST", "127.0.0.1")
        port = _positive_int("PORT", 8080)
        panel_public_url = os.getenv("PANEL_PUBLIC_URL", "").strip().rstrip("/")
        telegram_api_id = _optional_positive_int("TELEGRAM_API_ID")
        telegram_api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()
        if bool(telegram_api_id) != bool(telegram_api_hash):
            raise ConfigError(
                "TELEGRAM_API_ID and TELEGRAM_API_HASH must be configured together"
            )
        if not panel_public_url:
            browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
            panel_public_url = f"http://{browser_host}:{port}"

        return cls(
            bot_token=bot_token,
            owner_telegram_id=_positive_int("OWNER_TELEGRAM_ID"),
            panel_password=panel_password,
            session_secret=session_secret,
            host=host,
            port=port,
            database_path=_resolve_path(
                os.getenv("DATABASE_PATH", "./data/respy.db"), base
            ),
            media_dir=_resolve_path(os.getenv("MEDIA_DIR", "./data/media"), base),
            max_media_bytes=_positive_int("MAX_MEDIA_BYTES", 20 * 1024 * 1024),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            telegram_api_id=telegram_api_id,
            telegram_api_hash=telegram_api_hash,
            mtproto_session_path=_resolve_path(
                os.getenv("MTPROTO_SESSION_PATH", "./data/respy_mtproto"),
                base,
            ),
            mtproto_user_session_path=_resolve_path(
                os.getenv(
                    "MTPROTO_USER_SESSION_PATH",
                    "./data/respy_user",
                ),
                base,
            ),
            panel_public_url=panel_public_url,
            gift_poll_interval_seconds=_positive_int(
                "GIFT_POLL_INTERVAL_SECONDS", 5
            ),
            gift_poll_batch_size=_positive_int("GIFT_POLL_BATCH_SIZE", 5),
            log_path=_resolve_path(os.getenv("LOG_PATH", "./data/respy.log"), base),
            media_retention_hours=_positive_int("MEDIA_RETENTION_HOURS", 24),
            media_cleanup_interval_seconds=_positive_int(
                "MEDIA_CLEANUP_INTERVAL_SECONDS", 3600
            ),
        )

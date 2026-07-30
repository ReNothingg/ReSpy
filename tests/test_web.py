import hashlib
import hmac
import json
import time
from pathlib import Path
from urllib.parse import urlencode

from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.database import Database
from app.security import make_session
from app.web import COOKIE_NAME, build_web_app


class AvatarBot:
    async def download(self, file_id: str, destination) -> None:
        assert file_id == "telegram-avatar"
        destination.write(b"jpeg-from-telegram")


def settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123:token",
        owner_telegram_id=42,
        panel_password="long-test-password",
        session_secret="s" * 32,
        host="127.0.0.1",
        port=8080,
        database_path=tmp_path / "test.db",
        media_dir=tmp_path / "media",
        max_media_bytes=20 * 1024 * 1024,
        log_level="INFO",
    )


async def test_panel_requires_login_and_renders(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    app = build_web_app(db, config)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        follow_redirects=False,
    ) as client:
        response = await client.get("/")
        assert response.status_code == 303
        assert response.headers["location"].startswith("/login")

        client.cookies.set(COOKIE_NAME, make_session(config.session_secret))
        response = await client.get("/")
        assert response.status_code == 200
        assert "<h1>Чаты</h1>" in response.text
        assert "/static/style.css" in response.text
        assert "/static/app.js" in response.text


async def test_login_rejects_wrong_password(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()

    async with AsyncClient(
        transport=ASGITransport(app=build_web_app(db, config)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/login",
            data={"password": "wrong", "next": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 401
        assert "Неверный пароль" in response.text


async def test_avatar_is_proxied_without_disk_file(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    await db.upsert_connection(
        {
            "id": "connection-1",
            "owner_user_id": 42,
            "owner_name": "Pavel",
            "is_enabled": True,
            "rights": {},
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    )
    await db.upsert_chat(
        "connection-1",
        {"id": 100, "type": "private", "first_name": "Alice"},
        "2026-07-30T12:00:00+00:00",
    )
    await db.update_chat_avatar(
        "connection-1",
        100,
        "telegram-avatar",
        "avatar-unique",
        "2026-07-30T12:00:00+00:00",
    )

    async with AsyncClient(
        transport=ASGITransport(
            app=build_web_app(db, config, AvatarBot())  # type: ignore[arg-type]
        ),
        base_url="http://test",
    ) as client:
        client.cookies.set(COOKIE_NAME, make_session(config.session_secret))
        response = await client.get("/avatars/connection-1/100")

    assert response.status_code == 200
    assert response.content == b"jpeg-from-telegram"
    assert response.headers["cache-control"] == "private, max-age=3600"
    assert not (tmp_path / "avatars").exists()


async def test_telegram_mini_app_authenticates_owner(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "query-1",
        "user": json.dumps(
            {"id": 42, "first_name": "Pavel"},
            separators=(",", ":"),
        ),
    }
    data_check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(
        b"WebAppData", config.bot_token.encode(), hashlib.sha256
    ).digest()
    fields["hash"] = hmac.new(
        secret, data_check.encode(), hashlib.sha256
    ).hexdigest()

    async with AsyncClient(
        transport=ASGITransport(app=build_web_app(db, config)),
        base_url="https://test",
        follow_redirects=False,
    ) as client:
        response = await client.post(
            "/telegram-auth",
            data={"init_data": urlencode(fields)},
        )
        assert response.status_code == 200
        assert COOKIE_NAME in response.cookies


async def test_gift_center_renders_tracked_gift(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    await db.upsert_connection(
        {
            "id": "connection-1",
            "owner_user_id": 42,
            "owner_name": "Pavel",
            "is_enabled": True,
            "rights": {},
            "updated_at": "2026-07-30T12:00:00+00:00",
        }
    )
    await db.upsert_chat(
        "connection-1",
        {"id": 100, "type": "private", "first_name": "Alice"},
        "2026-07-30T12:00:00+00:00",
    )
    gift, _, _ = await db.record_gift(
        {
            "connection_id": "connection-1",
            "chat_id": 100,
            "message_id": 50,
            "direction": "sent",
            "counterparty_id": 100,
            "counterparty_name": "Alice",
            "gift_id": "gift-777",
            "gift_kind": "unique",
            "title": "Plush Pepe",
            "unique_name": "Plush Pepe",
            "unique_number": 4242,
            "model_name": "Green",
            "model_rarity": 30,
            "symbol_name": "Star",
            "symbol_rarity": 15,
            "backdrop_name": "Violet",
            "backdrop_rarity": 10,
            "status": "upgraded",
            "event_type": "gift_upgraded",
            "occurred_at": "2026-07-30T12:00:00+00:00",
            "raw": {"unique_gift": {"number": 4242}},
        }
    )
    app = build_web_app(db, config)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        client.cookies.set(COOKIE_NAME, make_session(config.session_secret))
        response = await client.get("/gifts")
        assert response.status_code == 200
        assert "<h1>Подарки</h1>" in response.text
        assert "Plush Pepe" in response.text

        response = await client.get(f"/gifts/{gift['id']}")
        assert response.status_code == 200
        assert "Green" in response.text
        assert "#4242" in response.text

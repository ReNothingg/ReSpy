from datetime import UTC, datetime
from pathlib import Path

from telethon import types

from app.config import Settings
from app.database import Database
from app.mtproto_archive import MtprotoBusinessArchive, VIEW_ONCE_TTL
from app.telegram_bot import TelegramArchive
from app.user_session_archive import UserSessionArchive


class FakeBot:
    def __init__(self) -> None:
        self.photos: list[dict] = []
        self.notifications: list[str] = []

    async def send_photo(self, chat_id: int, photo, **kwargs) -> None:
        self.photos.append(
            {
                "chat_id": chat_id,
                "data": photo.data,
                **kwargs,
            }
        )

    async def send_message(self, _chat_id: int, text: str, **_kwargs) -> None:
        self.notifications.append(text)


class FakeMtprotoClient:
    async def download_media(self, _message, file=bytes) -> bytes:
        assert file is bytes
        return b"one-time-photo"


class FakeDifferenceClient(FakeMtprotoClient):
    def __init__(self, difference) -> None:
        self.difference = difference
        self.requests = []

    async def __call__(self, request):
        self.requests.append(request)
        return self.difference


def settings(tmp_path: Path) -> Settings:
    return Settings(
        bot_token="123:token",
        owner_telegram_id=42,
        panel_password="long-test-password",
        session_secret="s" * 32,
        host="127.0.0.1",
        port=8080,
        database_path=tmp_path / "archive.db",
        media_dir=tmp_path / "media",
        max_media_bytes=20 * 1024 * 1024,
        log_level="INFO",
    )


async def test_mtproto_view_once_photo_is_saved_before_delete(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    mtproto = MtprotoBusinessArchive(archive)
    now = datetime(2026, 7, 30, 18, 1, tzinfo=UTC)
    photo = types.Photo(
        id=999,
        access_hash=123,
        file_reference=b"reference",
        date=now,
        sizes=[],
        dc_id=2,
    )
    message = types.Message(
        id=413131,
        peer_id=types.PeerUser(100),
        from_id=types.PeerUser(100),
        date=now,
        message="",
        out=False,
        media=types.MessageMediaPhoto(
            photo=photo,
            ttl_seconds=VIEW_ONCE_TTL,
        ),
    )
    update = types.UpdateBotNewBusinessMessage(
        connection_id="business-1",
        message=message,
        qts=1,
    )

    await mtproto.handle_update(FakeMtprotoClient(), update)  # type: ignore[arg-type]

    messages = await db.list_messages("business-1", 100)
    assert len(messages) == 1
    assert messages[0]["message_id"] == 413131
    assert messages[0]["content_type"] == "photo"
    assert Path(messages[0]["media_path"]).read_bytes() == b"one-time-photo"
    assert bot.photos[0]["chat_id"] == 42
    assert bot.photos[0]["data"] == b"one-time-photo"
    assert "Одноразовое фото сохранено" in bot.photos[0]["caption"]


async def test_mtproto_recovers_view_once_photo_routed_to_bot_api(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    mtproto = MtprotoBusinessArchive(archive)
    now = datetime(2026, 7, 30, 18, 1, tzinfo=UTC)
    message = types.Message(
        id=413170,
        peer_id=types.PeerUser(100),
        from_id=types.PeerUser(100),
        date=now,
        message="",
        out=False,
        media=types.MessageMediaPhoto(
            photo=types.Photo(
                id=1000,
                access_hash=456,
                file_reference=b"reference",
                date=now,
                sizes=[],
                dc_id=2,
            ),
            ttl_seconds=VIEW_ONCE_TTL,
        ),
    )
    update = types.UpdateBotNewBusinessMessage(
        connection_id="business-1",
        message=message,
        qts=0,
    )
    recovered_state = types.updates.State(
        pts=10,
        qts=701,
        date=now,
        seq=1,
        unread_count=0,
    )
    client = FakeDifferenceClient(
        types.updates.Difference(
            new_messages=[],
            new_encrypted_messages=[],
            other_updates=[update],
            chats=[],
            users=[],
            state=recovered_state,
        )
    )
    mtproto._difference_state = types.updates.State(
        pts=10,
        qts=700,
        date=now,
        seq=1,
        unread_count=0,
    )

    await mtproto._recover_missed_updates(client)  # type: ignore[arg-type]

    messages = await db.list_messages("business-1", 100)
    assert messages[0]["message_id"] == 413170
    assert Path(messages[0]["media_path"]).read_bytes() == b"one-time-photo"
    assert bot.photos[0]["data"] == b"one-time-photo"
    assert mtproto._difference_state == recovered_state
    assert len(client.requests) == 1


async def test_user_session_captures_view_once_photo_hidden_from_business_bot(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    await db.upsert_connection(
        {
            "id": "business-1",
            "owner_user_id": 42,
            "owner_name": "Pavel",
            "is_enabled": True,
            "rights": {},
            "updated_at": "2026-07-30T18:01:00+00:00",
        }
    )
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    mtproto = MtprotoBusinessArchive(archive)
    user_archive = UserSessionArchive(archive, mtproto)
    now = datetime(2026, 7, 30, 18, 1, tzinfo=UTC)
    message = types.Message(
        id=413195,
        peer_id=types.PeerUser(100),
        from_id=types.PeerUser(100),
        date=now,
        message="",
        out=False,
        media=types.MessageMediaPhoto(
            photo=types.Photo(
                id=1001,
                access_hash=789,
                file_reference=b"reference",
                date=now,
                sizes=[],
                dc_id=2,
            ),
            ttl_seconds=VIEW_ONCE_TTL,
        ),
    )

    await user_archive.handle_update(
        FakeMtprotoClient(),  # type: ignore[arg-type]
        types.UpdateNewMessage(message=message, pts=1, pts_count=1),
    )

    messages = await db.list_messages("business-1", 100)
    assert messages[0]["message_id"] == 413195
    assert Path(messages[0]["media_path"]).read_bytes() == b"one-time-photo"
    assert bot.photos[0]["data"] == b"one-time-photo"


async def test_mtproto_screenshot_action_notifies_once(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    mtproto = MtprotoBusinessArchive(archive)
    message = types.MessageService(
        id=413134,
        peer_id=types.PeerUser(100),
        from_id=types.PeerUser(100),
        date=datetime(2026, 7, 30, 18, 1, tzinfo=UTC),
        out=False,
        action=types.MessageActionScreenshotTaken(),
    )
    update = types.UpdateBotNewBusinessMessage(
        connection_id="business-1",
        message=message,
        qts=2,
    )

    await mtproto.handle_update(FakeMtprotoClient(), update)  # type: ignore[arg-type]
    await mtproto.handle_update(FakeMtprotoClient(), update)  # type: ignore[arg-type]

    assert len(bot.notifications) == 1
    assert "Сделан снимок экрана" in bot.notifications[0]

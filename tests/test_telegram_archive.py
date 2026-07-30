from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from aiogram.types import (
    BusinessConnection,
    BusinessMessagesDeleted,
    Chat,
    Gift,
    GiftInfo,
    Message,
    OwnedGiftRegular,
    OwnedGifts,
    Sticker,
    UniqueGift,
    UniqueGiftBackdrop,
    UniqueGiftBackdropColors,
    UniqueGiftInfo,
    UniqueGiftModel,
    UniqueGiftSymbol,
    User,
)

from app.config import Settings
from app.database import Database
from app.telegram_bot import TelegramArchive


class FakeBot:
    def __init__(self) -> None:
        self.notifications: list[str] = []
        self.calls: list[dict] = []
        self.public_gifts = OwnedGifts(total_count=0, gifts=[])
        self.chat_photo = None

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        self.notifications.append(text)
        self.calls.append({"chat_id": chat_id, "text": text, **kwargs})

    async def get_user_gifts(self, _user_id: int, **_kwargs) -> OwnedGifts:
        return self.public_gifts

    async def get_chat(self, _chat_id: int) -> SimpleNamespace:
        return SimpleNamespace(photo=self.chat_photo)

    async def get_user_profile_photos(
        self, _user_id: int, **_kwargs
    ) -> SimpleNamespace:
        photos = [[self.chat_photo]] if self.chat_photo else []
        return SimpleNamespace(photos=photos)

    async def download(self, _file_id: str, destination: Path) -> None:
        Path(destination).write_bytes(b"telegram-file")


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
        panel_public_url="https://panel.example.test",
    )


def business_message(text: str, edit_date: int | None = None) -> Message:
    return Message(
        message_id=10,
        date=datetime(2026, 7, 30, 12, 0, tzinfo=UTC),
        edit_date=edit_date,
        business_connection_id="business-1",
        chat=Chat(id=100, type="private", first_name="Alice", username="alice"),
        from_user=User(id=100, is_bot=False, first_name="Alice", username="alice"),
        text=text,
    )


async def test_unauthorized_start_notifies_owner(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]

    await archive.notify_unauthorized_start(
        User(
            id=777,
            is_bot=False,
            first_name="<Alice>",
            username="blocked_user",
        )
    )

    assert bot.calls[-1]["chat_id"] == config.owner_telegram_id
    assert "Попытка доступа" in bot.calls[-1]["text"]
    assert "@blocked_user" in bot.calls[-1]["text"]
    assert "<code>777</code>" in bot.calls[-1]["text"]
    assert "&lt;Alice&gt;" in bot.calls[-1]["text"]


def sticker(suffix: str) -> Sticker:
    return Sticker(
        file_id=f"file-{suffix}",
        file_unique_id=f"unique-{suffix}",
        type="regular",
        width=512,
        height=512,
        is_animated=False,
        is_video=False,
    )


def regular_gift_message(sent_at: datetime | None = None) -> Message:
    return Message(
        message_id=50,
        date=sent_at or datetime(2026, 7, 30, 12, 10, tzinfo=UTC),
        business_connection_id="business-1",
        chat=Chat(id=100, type="private", first_name="Alice", username="alice"),
        from_user=User(id=42, is_bot=False, first_name="Pavel"),
        receiver_user=User(id=100, is_bot=False, first_name="Alice", username="alice"),
        gift=GiftInfo(
            gift=Gift(id="gift-777", sticker=sticker("base"), star_count=100),
            convert_star_count=85,
            can_be_upgraded=True,
        ),
    )


def unique_gift_message() -> Message:
    return Message(
        message_id=51,
        date=datetime(2026, 7, 30, 12, 11, tzinfo=UTC),
        business_connection_id="business-1",
        chat=Chat(id=100, type="private", first_name="Alice", username="alice"),
        from_user=User(id=100, is_bot=False, first_name="Alice", username="alice"),
        receiver_user=User(id=42, is_bot=False, first_name="Pavel"),
        unique_gift=UniqueGiftInfo(
            origin="upgrade",
            gift=UniqueGift(
                gift_id="gift-777",
                base_name="Plush Pepe",
                name="Plush Pepe",
                number=4242,
                model=UniqueGiftModel(
                    name="Green",
                    sticker=sticker("model"),
                    rarity_per_mille=30,
                ),
                symbol=UniqueGiftSymbol(
                    name="Star",
                    sticker=sticker("symbol"),
                    rarity_per_mille=15,
                ),
                backdrop=UniqueGiftBackdrop(
                    name="Violet",
                    colors=UniqueGiftBackdropColors(
                        center_color=1,
                        edge_color=2,
                        symbol_color=3,
                        text_color=4,
                    ),
                    rarity_per_mille=10,
                ),
            ),
        ),
    )


async def test_business_message_edit_and_delete_notifications(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]

    await archive.handle_connection(
        BusinessConnection(
            id="business-1",
            user=User(id=42, is_bot=False, first_name="Pavel"),
            user_chat_id=42,
            date=datetime(2026, 7, 30, 11, 59, tzinfo=UTC),
            is_enabled=True,
        )
    )
    await archive.handle_message(business_message("original"))
    await archive.handle_edit(
        business_message(
            "edited",
            edit_date=1_785_412_860,
        )
    )
    await archive.handle_deleted(
        BusinessMessagesDeleted(
            business_connection_id="business-1",
            chat=Chat(id=100, type="private", first_name="Alice"),
            message_ids=[10],
        )
    )

    assert any("Сообщение изменено" in item for item in bot.notifications)
    assert any("original" in item and "edited" in item for item in bot.notifications)
    assert any("Сообщение удалено" in item for item in bot.notifications)
    assert (await db.dashboard_stats())["deleted"] == 1
    deleted_call = next(
        call for call in bot.calls if "Сообщение удалено" in call["text"]
    )
    assert "Архив:" not in deleted_call["text"]
    assert 'href="tg://user?id=100"' in deleted_call["text"]
    assert "<b>Alice</b>" in deleted_call["text"]
    button = deleted_call["reply_markup"].inline_keyboard[0][0]
    assert button.text.endswith("· 1")
    assert button.web_app
    assert button.web_app.url.endswith("/chats/business-1/100")


async def test_chat_avatar_is_downloaded_and_old_media_is_removed(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    bot.chat_photo = SimpleNamespace(
        file_id="avatar-file",
        file_unique_id="avatar-unique",
    )
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    await archive.handle_connection(
        BusinessConnection(
            id="business-1",
            user=User(id=42, is_bot=False, first_name="Pavel"),
            user_chat_id=42,
            date=datetime(2026, 7, 30, 11, 59, tzinfo=UTC),
            is_enabled=True,
        )
    )

    await archive.handle_message(business_message("with avatar"))
    avatar = await db.chat_avatar_state("business-1", 100)
    assert avatar and avatar["avatar_file_unique_id"] == "avatar-unique"
    assert avatar["avatar_file_id"] == "avatar-file"
    assert not (tmp_path / "avatars").exists()

    old_message = {
        "connection_id": "business-1",
        "chat_id": 100,
        "message_id": 11,
        "sender_id": 100,
        "sender_name": "Alice",
        "sender_username": "alice",
        "is_outgoing": False,
        "sent_at": "2020-01-01T00:00:00+00:00",
        "received_at": "2020-01-01T00:00:00+00:00",
        "text": None,
        "caption": None,
        "content_type": "photo",
        "raw": {"message_id": 11},
    }
    row_id, _ = await db.store_message(old_message)
    media_path = config.media_dir / "old-photo.jpg"
    media_path.parent.mkdir(parents=True, exist_ok=True)
    media_path.write_bytes(b"photo")
    await db.update_media(row_id, str(media_path), media_path.stat().st_size, None)

    assert await archive.cleanup_expired_media_once() == 1
    assert not media_path.exists()
    stored = await db.get_message(row_id)
    assert stored and stored["media_path"] is None
    assert "24 ч." in stored["media_error"]


async def test_sent_gift_and_confirmed_upgrade_are_linked(tmp_path: Path) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    await archive.handle_connection(
        BusinessConnection(
            id="business-1",
            user=User(id=42, is_bot=False, first_name="Pavel"),
            user_chat_id=42,
            date=datetime(2026, 7, 30, 12, 9, tzinfo=UTC),
            is_enabled=True,
        )
    )

    await archive.handle_message(regular_gift_message())
    await archive.handle_message(unique_gift_message())

    assert any("Подарок отправлен" in item for item in bot.notifications)
    assert any("Подаренный вами подарок вскрыли" in item for item in bot.notifications)
    gifts = await db.list_gifts()
    assert len(gifts) == 1
    assert gifts[0]["status"] == "upgraded"
    assert gifts[0]["direction"] == "sent"
    assert gifts[0]["unique_number"] == 4242

    await archive.handle_deleted(
        BusinessMessagesDeleted(
            business_connection_id="business-1",
            chat=Chat(id=100, type="private", first_name="Alice"),
            message_ids=[51],
        )
    )
    assert any("Подарок исчез из диалога" in item for item in bot.notifications)
    assert (await db.list_gifts())[0]["status"] == "missing"


async def test_public_gift_inventory_polling_notifies_real_transitions(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path)
    db = Database(config.database_path)
    await db.initialize()
    bot = FakeBot()
    archive = TelegramArchive(bot, db, config)  # type: ignore[arg-type]
    await archive.handle_connection(
        BusinessConnection(
            id="business-1",
            user=User(id=42, is_bot=False, first_name="Pavel"),
            user_chat_id=42,
            date=datetime(2026, 7, 30, 12, 9, tzinfo=UTC),
            is_enabled=True,
        )
    )
    sent_at = datetime.now(UTC).replace(microsecond=0)
    await archive.handle_message(regular_gift_message(sent_at))

    bot.public_gifts = OwnedGifts(
        total_count=1,
        gifts=[
            OwnedGiftRegular(
                gift=Gift(
                    id="gift-777",
                    sticker=sticker("public"),
                    star_count=100,
                ),
                send_date=int(sent_at.timestamp()),
                sender_user=User(id=42, is_bot=False, first_name="Pavel"),
            )
        ],
    )
    await archive.poll_gift_profiles_once()
    assert not any(
        "Подарок снова появился в инвентаре" in item
        for item in bot.notifications
    )

    bot.public_gifts = OwnedGifts(total_count=0, gifts=[])
    await archive.poll_gift_profiles_once()
    assert any("Подарок исчез из инвентаря" in item for item in bot.notifications)

    bot.public_gifts = OwnedGifts(
        total_count=1,
        gifts=[
            OwnedGiftRegular(
                gift=Gift(
                    id="gift-777",
                    sticker=sticker("public-return"),
                    star_count=100,
                ),
                send_date=int(sent_at.timestamp()),
            )
        ],
    )
    await archive.poll_gift_profiles_once()
    assert any(
        "Подарок снова появился в инвентаре" in item
        for item in bot.notifications
    )

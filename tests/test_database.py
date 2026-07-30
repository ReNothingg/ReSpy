from pathlib import Path

from app.database import Database


NOW = "2026-07-30T12:00:00+00:00"


async def prepared_db(path: Path) -> Database:
    db = Database(path)
    await db.initialize()
    await db.upsert_connection(
        {
            "id": "connection-1",
            "owner_user_id": 42,
            "owner_name": "Pavel",
            "owner_username": "pavel",
            "is_enabled": True,
            "rights": {"can_reply": True},
            "updated_at": NOW,
        }
    )
    await db.upsert_chat(
        "connection-1",
        {
            "id": 100,
            "type": "private",
            "first_name": "Alice",
            "username": "alice",
        },
        NOW,
    )
    return db


def message_data(text: str = "hello") -> dict:
    return {
        "connection_id": "connection-1",
        "chat_id": 100,
        "message_id": 7,
        "sender_id": 100,
        "sender_name": "Alice",
        "sender_username": "alice",
        "is_outgoing": False,
        "sent_at": NOW,
        "received_at": NOW,
        "text": text,
        "caption": None,
        "content_type": "text",
        "raw": {"message_id": 7, "text": text},
    }


async def test_archive_edit_delete_flow(tmp_path: Path) -> None:
    db = await prepared_db(tmp_path / "test.db")

    row_id, inserted = await db.store_message(message_data())
    duplicate_id, duplicate_inserted = await db.store_message(message_data())
    assert inserted is True
    assert duplicate_inserted is False
    assert duplicate_id == row_id

    edit = message_data("hello, edited")
    edit["edited_at"] = "2026-07-30T12:01:00+00:00"
    old, changed = await db.apply_edit(edit)
    assert changed is True
    assert old and old["text"] == "hello"

    stored, deleted = await db.mark_deleted(
        "connection-1", 100, 7, "2026-07-30T12:02:00+00:00"
    )
    assert deleted is True
    assert stored and stored["text"] == "hello, edited"

    _, deleted_again = await db.mark_deleted(
        "connection-1", 100, 7, "2026-07-30T12:03:00+00:00"
    )
    assert deleted_again is False

    stats = await db.dashboard_stats()
    assert stats == {
        "chats": 1,
        "messages": 1,
        "deleted": 1,
        "edits": 1,
        "gifts": 0,
        "gift_upgraded": 0,
        "gift_missing": 0,
        "connections": 1,
    }
    versions = await db.get_versions(row_id)
    assert [version["text"] for version in versions] == ["hello"]
    archive_stats = await db.chat_archive_stats("connection-1", 100)
    assert archive_stats
    assert archive_stats["total_messages"] == 1
    assert archive_stats["deleted_messages"] == 1
    assert archive_stats["edit_events"] == 1
    assert archive_stats["media_messages"] == 0


async def test_unknown_delete_is_reported_once(tmp_path: Path) -> None:
    db = await prepared_db(tmp_path / "test.db")

    message, changed = await db.mark_deleted(
        "connection-1", 100, 99, "2026-07-30T12:02:00+00:00"
    )
    assert message is None
    assert changed is True

    _, changed_again = await db.mark_deleted(
        "connection-1", 100, 99, "2026-07-30T12:03:00+00:00"
    )
    assert changed_again is False


async def test_messages_are_returned_oldest_to_newest(tmp_path: Path) -> None:
    db = await prepared_db(tmp_path / "test.db")
    for message_id in range(1, 5):
        data = message_data(f"message-{message_id}")
        data["message_id"] = message_id
        data["sent_at"] = f"2026-07-30T12:0{message_id}:00+00:00"
        await db.store_message(data)

    messages = await db.list_messages("connection-1", 100, limit=3)

    assert [message["message_id"] for message in messages] == [2, 3, 4]


async def test_gift_lifecycle_matches_upgrade_and_marks_uncertain_delete(
    tmp_path: Path,
) -> None:
    db = await prepared_db(tmp_path / "test.db")
    regular = {
        "connection_id": "connection-1",
        "chat_id": 100,
        "message_id": 50,
        "direction": "sent",
        "counterparty_id": 100,
        "counterparty_name": "Alice",
        "counterparty_username": "alice",
        "gift_id": "gift-777",
        "gift_kind": "regular",
        "title": "Telegram Gift",
        "star_count": 100,
        "convert_star_count": 85,
        "upgrade_star_count": 25,
        "status": "sent",
        "event_type": "gift_sent",
        "occurred_at": NOW,
        "raw": {"gift": {"id": "gift-777"}},
    }
    gift, changed, matched = await db.record_gift(regular)
    assert changed is True
    assert matched is False
    assert gift["status"] == "sent"

    upgraded = {
        **regular,
        "message_id": 51,
        "direction": "received",
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
        "origin": "upgrade",
        "status": "upgraded",
        "event_type": "gift_upgraded",
        "occurred_at": "2026-07-30T12:05:00+00:00",
        "raw": {"unique_gift": {"gift_id": "gift-777", "number": 4242}},
    }
    gift, changed, matched = await db.record_gift(upgraded)
    assert changed is True
    assert matched is True
    assert gift["status"] == "upgraded"
    assert gift["direction"] == "sent"
    assert gift["unique_number"] == 4242
    assert (await db.gift_stats())["upgraded"] == 1

    _, source_alert = await db.mark_gift_message_deleted(
        "connection-1", 100, 50, "2026-07-30T12:06:00+00:00"
    )
    assert source_alert is False

    gift, latest_alert = await db.mark_gift_message_deleted(
        "connection-1", 100, 51, "2026-07-30T12:07:00+00:00"
    )
    assert latest_alert is True
    assert gift and gift["status"] == "missing"
    assert gift["confidence"] == "inferred"


async def test_gift_inventory_presence_only_emits_transitions(
    tmp_path: Path,
) -> None:
    db = await prepared_db(tmp_path / "test.db")
    gift, _, _ = await db.record_gift(
        {
            "connection_id": "connection-1",
            "chat_id": 100,
            "message_id": 50,
            "direction": "sent",
            "counterparty_id": 100,
            "counterparty_name": "Alice",
            "counterparty_username": "alice",
            "gift_id": "gift-777",
            "gift_kind": "regular",
            "title": "Telegram Gift",
            "star_count": 100,
            "convert_star_count": 85,
            "upgrade_star_count": 25,
            "status": "sent",
            "event_type": "gift_sent",
            "occurred_at": NOW,
            "raw": {"gift": {"id": "gift-777"}},
        }
    )

    _, initial = await db.update_gift_inventory_presence(
        gift["id"], True, "2026-07-30T12:01:00+00:00"
    )
    _, hidden = await db.update_gift_inventory_presence(
        gift["id"], False, "2026-07-30T12:02:00+00:00"
    )
    _, hidden_again = await db.update_gift_inventory_presence(
        gift["id"], False, "2026-07-30T12:03:00+00:00"
    )
    _, shown = await db.update_gift_inventory_presence(
        gift["id"], True, "2026-07-30T12:04:00+00:00"
    )

    assert initial is None
    assert hidden == "gift_inventory_missing"
    assert hidden_again is None
    assert shown == "gift_inventory_present"
    assert [
        event["event_type"] for event in await db.get_gift_events(gift["id"])
    ][:2] == ["gift_inventory_present", "gift_inventory_missing"]

    second_gift, _, _ = await db.record_gift(
        {
            "connection_id": "connection-1",
            "chat_id": 100,
            "message_id": 60,
            "direction": "sent",
            "counterparty_id": 100,
            "counterparty_name": "Alice",
            "counterparty_username": "alice",
            "gift_id": "gift-888",
            "gift_kind": "regular",
            "title": "Telegram Gift",
            "star_count": 50,
            "convert_star_count": 40,
            "status": "sent",
            "event_type": "gift_sent",
            "occurred_at": NOW,
            "raw": {"gift": {"id": "gift-888"}},
        }
    )
    _, initially_hidden = await db.update_gift_inventory_presence(
        second_gift["id"], False, "2026-07-30T12:05:00+00:00"
    )
    assert initially_hidden == "gift_inventory_missing"

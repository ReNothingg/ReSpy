from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

import aiosqlite


SCHEMA = """
PRAGMA journal_mode=DELETE;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS connections (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    owner_name TEXT NOT NULL DEFAULT '',
    owner_username TEXT,
    is_enabled INTEGER NOT NULL DEFAULT 1,
    rights_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'private',
    title TEXT NOT NULL DEFAULT '',
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    last_message_at TEXT,
    message_count INTEGER NOT NULL DEFAULT 0,
    deleted_count INTEGER NOT NULL DEFAULT 0,
    edited_count INTEGER NOT NULL DEFAULT 0,
    avatar_file_id TEXT,
    avatar_file_unique_id TEXT,
    avatar_checked_at TEXT,
    PRIMARY KEY (connection_id, chat_id),
    FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sender_id INTEGER,
    sender_name TEXT NOT NULL DEFAULT '',
    sender_username TEXT,
    is_outgoing INTEGER NOT NULL DEFAULT 0,
    sent_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    edited_at TEXT,
    deleted_at TEXT,
    text TEXT,
    caption TEXT,
    content_type TEXT NOT NULL,
    file_id TEXT,
    file_unique_id TEXT,
    media_path TEXT,
    media_name TEXT,
    media_mime TEXT,
    media_size INTEGER,
    media_error TEXT,
    edit_count INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    UNIQUE (connection_id, chat_id, message_id),
    FOREIGN KEY (connection_id, chat_id)
      REFERENCES chats(connection_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS message_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_row_id INTEGER NOT NULL,
    version_no INTEGER NOT NULL,
    text TEXT,
    caption TEXT,
    content_type TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    archived_at TEXT NOT NULL,
    FOREIGN KEY (message_row_id) REFERENCES messages(id) ON DELETE CASCADE,
    UNIQUE (message_row_id, version_no)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    chat_id INTEGER,
    message_id INTEGER,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (connection_id) REFERENCES connections(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS muted_chats (
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    muted_at TEXT NOT NULL,
    after_message_id INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (connection_id, chat_id),
    FOREIGN KEY (connection_id, chat_id)
      REFERENCES chats(connection_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS chat_notification_overrides (
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    previous_mute_until TEXT,
    changed_at TEXT NOT NULL,
    PRIMARY KEY (connection_id, chat_id),
    FOREIGN KEY (connection_id, chat_id)
      REFERENCES chats(connection_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS gifts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    connection_id TEXT NOT NULL,
    chat_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    latest_message_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    counterparty_id INTEGER,
    counterparty_name TEXT NOT NULL DEFAULT '',
    counterparty_username TEXT,
    gift_id TEXT NOT NULL,
    gift_kind TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT 'Telegram Gift',
    unique_name TEXT,
    unique_number INTEGER,
    model_name TEXT,
    model_rarity INTEGER,
    symbol_name TEXT,
    symbol_rarity INTEGER,
    backdrop_name TEXT,
    backdrop_rarity INTEGER,
    star_count INTEGER,
    convert_star_count INTEGER,
    upgrade_star_count INTEGER,
    prepaid_upgrade_star_count INTEGER,
    owned_gift_id TEXT,
    origin TEXT,
    status TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    is_private INTEGER NOT NULL DEFAULT 0,
    first_seen_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE (connection_id, chat_id, source_message_id),
    FOREIGN KEY (connection_id, chat_id)
      REFERENCES chats(connection_id, chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS gift_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gift_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    confidence TEXT NOT NULL DEFAULT 'confirmed',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (gift_id) REFERENCES gifts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS gift_profile_state (
    gift_id INTEGER PRIMARY KEY,
    is_visible INTEGER,
    first_visible_at TEXT,
    last_visible_at TEXT,
    last_checked_at TEXT,
    miss_count INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (gift_id) REFERENCES gifts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_chats_last_message
    ON chats(last_message_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_chat
    ON messages(connection_id, chat_id, sent_at DESC, message_id DESC);
CREATE INDEX IF NOT EXISTS idx_messages_deleted
    ON messages(is_deleted, deleted_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_created
    ON events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_muted_chats_connection
    ON muted_chats(connection_id);
CREATE INDEX IF NOT EXISTS idx_gifts_updated
    ON gifts(updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_gifts_match
    ON gifts(connection_id, chat_id, gift_id, status);
CREATE INDEX IF NOT EXISTS idx_gift_events_created
    ON gift_events(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gift_profile_checked
    ON gift_profile_state(last_checked_at);
"""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class Database:
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        db = await aiosqlite.connect(self.path, timeout=30)
        db.row_factory = aiosqlite.Row
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=30000")
        try:
            yield db
        finally:
            await db.close()

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        async with self.connect() as db:
            await db.executescript(SCHEMA)
            columns = {
                row["name"] for row in await db.execute_fetchall("PRAGMA table_info(chats)")
            }
            for name, definition in {
                "avatar_file_id": "TEXT",
                "avatar_path": "TEXT",
                "avatar_file_unique_id": "TEXT",
                "avatar_checked_at": "TEXT",
            }.items():
                if name not in columns:
                    await db.execute(f"ALTER TABLE chats ADD COLUMN {name} {definition}")
            muted_columns = {
                row["name"]
                for row in await db.execute_fetchall(
                    "PRAGMA table_info(muted_chats)"
                )
            }
            if "after_message_id" not in muted_columns:
                await db.execute(
                    "ALTER TABLE muted_chats "
                    "ADD COLUMN after_message_id INTEGER NOT NULL DEFAULT 0"
                )
            await db.commit()

    async def upsert_connection(self, data: dict[str, Any]) -> bool:
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO connections (
                    id, owner_user_id, owner_name, owner_username, is_enabled,
                    rights_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    owner_user_id=excluded.owner_user_id,
                    owner_name=excluded.owner_name,
                    owner_username=excluded.owner_username,
                    is_enabled=excluded.is_enabled,
                    rights_json=excluded.rights_json,
                    updated_at=excluded.updated_at
                """,
                (
                    data["id"],
                    data["owner_user_id"],
                    data.get("owner_name", ""),
                    data.get("owner_username"),
                    int(data.get("is_enabled", True)),
                    _json(data.get("rights", {})),
                    data["updated_at"],
                    data["updated_at"],
                ),
            )
            await db.execute(
                """
                INSERT INTO events (
                    connection_id, event_type, payload_json, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    data["id"],
                    "connection_enabled" if data.get("is_enabled", True)
                    else "connection_disabled",
                    _json({"rights": data.get("rights", {})}),
                    data["updated_at"],
                ),
            )
            await db.commit()
        return True

    async def connection_owner(self, connection_id: str) -> int | None:
        async with self.connect() as db:
            row = await db.execute_fetchall(
                """
                SELECT owner_user_id FROM connections
                WHERE id=? AND is_enabled=1
                """,
                (connection_id,),
            )
        return int(row[0]["owner_user_id"]) if row else None

    async def enabled_connection_for_owner(self, owner_user_id: int) -> str | None:
        async with self.connect() as db:
            row = await db.execute_fetchall(
                """
                SELECT id FROM connections
                WHERE owner_user_id=? AND is_enabled=1
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (owner_user_id,),
            )
        return str(row[0]["id"]) if row else None

    async def connection_rights(self, connection_id: str) -> dict[str, Any]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT rights_json FROM connections
                WHERE id=? AND is_enabled=1
                """,
                (connection_id,),
            )
        if not rows:
            return {}
        try:
            rights = json.loads(rows[0]["rights_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
        return rights if isinstance(rights, dict) else {}

    async def upsert_chat(
        self, connection_id: str, chat: dict[str, Any], message_at: str
    ) -> None:
        title = (
            chat.get("title")
            or " ".join(
                part for part in (chat.get("first_name"), chat.get("last_name")) if part
            )
            or chat.get("username")
            or str(chat["id"])
        )
        async with self.connect() as db:
            await db.execute(
                """
                INSERT INTO chats (
                    connection_id, chat_id, chat_type, title, username,
                    first_name, last_name, last_message_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(connection_id, chat_id) DO UPDATE SET
                    chat_type=excluded.chat_type,
                    title=excluded.title,
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    last_message_at=MAX(
                        COALESCE(chats.last_message_at, ''),
                        excluded.last_message_at
                    )
                """,
                (
                    connection_id,
                    chat["id"],
                    chat.get("type", "private"),
                    title,
                    chat.get("username"),
                    chat.get("first_name"),
                    chat.get("last_name"),
                    message_at,
                ),
            )
            await db.commit()

    async def set_chat_muted(
        self,
        connection_id: str,
        chat_id: int,
        muted: bool,
        changed_at: str,
        after_message_id: int | None = None,
    ) -> bool:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT 1 FROM muted_chats
                WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
            was_muted = bool(rows)
            if muted and not was_muted:
                await db.execute(
                    """
                    INSERT INTO muted_chats (
                        connection_id, chat_id, muted_at, after_message_id
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        connection_id,
                        chat_id,
                        changed_at,
                        after_message_id or 0,
                    ),
                )
            elif muted and after_message_id is not None:
                await db.execute(
                    """
                    UPDATE muted_chats
                    SET muted_at=?, after_message_id=?
                    WHERE connection_id=? AND chat_id=?
                    """,
                    (
                        changed_at,
                        after_message_id,
                        connection_id,
                        chat_id,
                    ),
                )
            elif not muted and was_muted:
                await db.execute(
                    """
                    DELETE FROM muted_chats
                    WHERE connection_id=? AND chat_id=?
                    """,
                    (connection_id, chat_id),
                )
            changed = muted != was_muted
            if changed:
                await db.execute(
                    """
                    INSERT INTO events (
                        connection_id, chat_id, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, '{}', ?)
                    """,
                    (
                        connection_id,
                        chat_id,
                        "chat_muted" if muted else "chat_unmuted",
                        changed_at,
                    ),
                )
            await db.commit()
        return changed

    async def is_chat_muted(self, connection_id: str, chat_id: int) -> bool:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT 1 FROM muted_chats
                WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
        return bool(rows)

    async def muted_chat_states(self) -> list[tuple[str, int, int]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT connection_id, chat_id, after_message_id
                FROM muted_chats
                """
            )
        return [
            (
                str(row["connection_id"]),
                int(row["chat_id"]),
                int(row["after_message_id"]),
            )
            for row in rows
        ]

    async def save_chat_notification_override(
        self,
        connection_id: str,
        chat_id: int,
        previous_mute_until: str | None,
        changed_at: str,
    ) -> bool:
        async with self.connect() as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO chat_notification_overrides (
                    connection_id, chat_id, previous_mute_until, changed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    connection_id,
                    chat_id,
                    previous_mute_until,
                    changed_at,
                ),
            )
            await db.commit()
        return cursor.rowcount == 1

    async def chat_notification_override(
        self,
        connection_id: str,
        chat_id: int,
    ) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT previous_mute_until, changed_at
                FROM chat_notification_overrides
                WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
        return dict(rows[0]) if rows else None

    async def clear_chat_notification_override(
        self,
        connection_id: str,
        chat_id: int,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                DELETE FROM chat_notification_overrides
                WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
            await db.commit()

    async def store_message(self, data: dict[str, Any]) -> tuple[int, bool]:
        async with self.connect() as db:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO messages (
                    connection_id, chat_id, message_id, sender_id, sender_name,
                    sender_username, is_outgoing, sent_at, received_at, text,
                    caption, content_type, file_id, file_unique_id, media_path,
                    media_name, media_mime, media_size, media_error, raw_json
                ) VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    data["connection_id"],
                    data["chat_id"],
                    data["message_id"],
                    data.get("sender_id"),
                    data.get("sender_name", ""),
                    data.get("sender_username"),
                    int(data.get("is_outgoing", False)),
                    data["sent_at"],
                    data["received_at"],
                    data.get("text"),
                    data.get("caption"),
                    data["content_type"],
                    data.get("file_id"),
                    data.get("file_unique_id"),
                    data.get("media_path"),
                    data.get("media_name"),
                    data.get("media_mime"),
                    data.get("media_size"),
                    data.get("media_error"),
                    _json(data["raw"]),
                ),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                row_id = cursor.lastrowid
                await db.execute(
                    """
                    UPDATE chats SET message_count=message_count+1
                    WHERE connection_id=? AND chat_id=?
                    """,
                    (data["connection_id"], data["chat_id"]),
                )
                await db.execute(
                    """
                    INSERT INTO events (
                        connection_id, chat_id, message_id, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, 'message', ?, ?)
                    """,
                    (
                        data["connection_id"],
                        data["chat_id"],
                        data["message_id"],
                        _json({"content_type": data["content_type"]}),
                        data["received_at"],
                    ),
                )
            else:
                row = await db.execute_fetchall(
                    """
                    SELECT id FROM messages
                    WHERE connection_id=? AND chat_id=? AND message_id=?
                    """,
                    (
                        data["connection_id"],
                        data["chat_id"],
                        data["message_id"],
                    ),
                )
                row_id = int(row[0]["id"])
            await db.commit()
        return int(row_id), inserted

    async def update_media(
        self,
        row_id: int,
        media_path: str | None,
        media_size: int | None,
        media_error: str | None,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                UPDATE messages
                SET media_path=?, media_size=?, media_error=?
                WHERE id=?
                """,
                (media_path, media_size, media_error, row_id),
            )
            await db.commit()

    async def expired_media(self, cutoff: str, limit: int = 500) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT id, media_path
                FROM messages
                WHERE media_path IS NOT NULL AND received_at <= ?
                ORDER BY received_at ASC, id ASC
                LIMIT ?
                """,
                (cutoff, limit),
            )
        return [dict(row) for row in rows]

    async def mark_media_expired(self, row_ids: list[int], reason: str) -> None:
        if not row_ids:
            return
        placeholders = ",".join("?" for _ in row_ids)
        async with self.connect() as db:
            await db.execute(
                f"""
                UPDATE messages
                SET media_path=NULL, media_size=NULL, media_error=?
                WHERE id IN ({placeholders})
                """,
                (reason, *row_ids),
            )
            await db.commit()

    async def chat_avatar_state(
        self, connection_id: str, chat_id: int
    ) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT chat_type, avatar_file_id, avatar_file_unique_id,
                       avatar_checked_at
                FROM chats WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
        return dict(rows[0]) if rows else None

    async def update_chat_avatar(
        self,
        connection_id: str,
        chat_id: int,
        file_id: str | None,
        file_unique_id: str | None,
        checked_at: str,
    ) -> None:
        async with self.connect() as db:
            await db.execute(
                """
                UPDATE chats
                SET avatar_file_id=?, avatar_path=NULL,
                    avatar_file_unique_id=?, avatar_checked_at=?
                WHERE connection_id=? AND chat_id=?
                """,
                (
                    file_id,
                    file_unique_id,
                    checked_at,
                    connection_id,
                    chat_id,
                ),
            )
            await db.commit()

    async def clear_legacy_avatar_paths(self) -> list[str]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT avatar_path FROM chats WHERE avatar_path IS NOT NULL"
            )
            await db.execute("UPDATE chats SET avatar_path=NULL")
            await db.commit()
        return [str(row["avatar_path"]) for row in rows if row["avatar_path"]]

    async def apply_edit(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, bool]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM messages
                WHERE connection_id=? AND chat_id=? AND message_id=?
                """,
                (
                    data["connection_id"],
                    data["chat_id"],
                    data["message_id"],
                ),
            )
            if not rows:
                return None, False

            old = dict(rows[0])
            old_body = old.get("text") or old.get("caption") or ""
            new_body = data.get("text") or data.get("caption") or ""
            if old_body == new_body and old.get("content_type") == data["content_type"]:
                return old, False

            version_no = int(old["edit_count"]) + 1
            await db.execute(
                """
                INSERT OR IGNORE INTO message_versions (
                    message_row_id, version_no, text, caption, content_type,
                    raw_json, archived_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    old["id"],
                    version_no,
                    old.get("text"),
                    old.get("caption"),
                    old["content_type"],
                    old["raw_json"],
                    data["edited_at"],
                ),
            )
            await db.execute(
                """
                UPDATE messages SET
                    text=?, caption=?, content_type=?, raw_json=?,
                    edited_at=?, edit_count=?
                WHERE id=?
                """,
                (
                    data.get("text"),
                    data.get("caption"),
                    data["content_type"],
                    _json(data["raw"]),
                    data["edited_at"],
                    version_no,
                    old["id"],
                ),
            )
            await db.execute(
                """
                UPDATE chats SET edited_count=edited_count+1
                WHERE connection_id=? AND chat_id=?
                """,
                (data["connection_id"], data["chat_id"]),
            )
            await db.execute(
                """
                INSERT INTO events (
                    connection_id, chat_id, message_id, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, 'edited', ?, ?)
                """,
                (
                    data["connection_id"],
                    data["chat_id"],
                    data["message_id"],
                    _json({"old": old_body, "new": new_body}),
                    data["edited_at"],
                ),
            )
            await db.commit()
        return old, True

    async def mark_deleted(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
        deleted_at: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM messages
                WHERE connection_id=? AND chat_id=? AND message_id=?
                """,
                (connection_id, chat_id, message_id),
            )
            message = dict(rows[0]) if rows else None
            previous_events = await db.execute_fetchall(
                """
                SELECT 1 FROM events
                WHERE connection_id=? AND chat_id=? AND message_id=?
                  AND event_type='deleted'
                LIMIT 1
                """,
                (connection_id, chat_id, message_id),
            )
            changed = bool(
                (message and not message["is_deleted"])
                or (message is None and not previous_events)
            )
            if changed:
                if message:
                    await db.execute(
                        """
                        UPDATE messages SET is_deleted=1, deleted_at=? WHERE id=?
                        """,
                        (deleted_at, message["id"]),
                    )
                    await db.execute(
                        """
                        UPDATE chats SET deleted_count=deleted_count+1
                        WHERE connection_id=? AND chat_id=?
                        """,
                        (connection_id, chat_id),
                    )
                await db.execute(
                    """
                    INSERT INTO events (
                        connection_id, chat_id, message_id, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, 'deleted', ?, ?)
                    """,
                    (
                        connection_id,
                        chat_id,
                        message_id,
                        _json({"known": message is not None}),
                        deleted_at,
                    ),
                )
            await db.commit()
        return message, changed

    async def record_gift(
        self, data: dict[str, Any]
    ) -> tuple[dict[str, Any], bool, bool]:
        status_priority = {
            "received": 10,
            "sent": 10,
            "upgrade_paid": 20,
            "transferred": 30,
            "resold": 30,
            "offer": 30,
            "upgraded": 40,
            "missing": 50,
            "refunded": 50,
        }
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM gifts
                WHERE connection_id=? AND chat_id=?
                  AND (source_message_id=? OR latest_message_id=?)
                ORDER BY id DESC LIMIT 1
                """,
                (
                    data["connection_id"],
                    data["chat_id"],
                    data["message_id"],
                    data["message_id"],
                ),
            )
            matched_existing = bool(rows)
            if not rows and data["status"] in {
                "upgrade_paid",
                "upgraded",
                "transferred",
                "resold",
                "offer",
            }:
                rows = await db.execute_fetchall(
                    """
                    SELECT * FROM gifts
                    WHERE connection_id=? AND chat_id=? AND gift_id=?
                      AND status NOT IN ('missing', 'refunded')
                    ORDER BY
                      CASE
                        WHEN unique_number=? AND ? IS NOT NULL THEN 0
                        ELSE 1
                      END,
                      CASE WHEN direction='sent' THEN 0 ELSE 1 END,
                      updated_at DESC, id DESC
                    LIMIT 1
                    """,
                    (
                        data["connection_id"],
                        data["chat_id"],
                        data["gift_id"],
                        data.get("unique_number"),
                        data.get("unique_number"),
                    ),
                )
                matched_existing = bool(rows)

            if rows:
                current = dict(rows[0])
                current_priority = status_priority.get(current["status"], 0)
                incoming_priority = status_priority.get(data["status"], 0)
                if (
                    current["confidence"] == "inferred"
                    and data.get("confidence", "confirmed") == "confirmed"
                ):
                    new_status = data["status"]
                else:
                    new_status = (
                        data["status"]
                        if incoming_priority >= current_priority
                        else current["status"]
                    )
                raw_json = _json(data["raw"])
                changed = bool(
                    current["status"] != new_status
                    or current["raw_json"] != raw_json
                    or current["latest_message_id"] != data["message_id"]
                )
                new_direction = (
                    current["direction"]
                    if current["source_message_id"] != data["message_id"]
                    else data["direction"]
                )
                await db.execute(
                    """
                    UPDATE gifts SET
                        latest_message_id=?,
                        direction=?,
                        counterparty_id=COALESCE(?, counterparty_id),
                        counterparty_name=CASE WHEN ?<>'' THEN ? ELSE counterparty_name END,
                        counterparty_username=COALESCE(?, counterparty_username),
                        gift_kind=?,
                        title=CASE WHEN ?<>'' THEN ? ELSE title END,
                        unique_name=COALESCE(?, unique_name),
                        unique_number=COALESCE(?, unique_number),
                        model_name=COALESCE(?, model_name),
                        model_rarity=COALESCE(?, model_rarity),
                        symbol_name=COALESCE(?, symbol_name),
                        symbol_rarity=COALESCE(?, symbol_rarity),
                        backdrop_name=COALESCE(?, backdrop_name),
                        backdrop_rarity=COALESCE(?, backdrop_rarity),
                        star_count=COALESCE(?, star_count),
                        convert_star_count=COALESCE(?, convert_star_count),
                        upgrade_star_count=COALESCE(?, upgrade_star_count),
                        prepaid_upgrade_star_count=COALESCE(
                            ?, prepaid_upgrade_star_count
                        ),
                        owned_gift_id=COALESCE(?, owned_gift_id),
                        origin=COALESCE(?, origin),
                        status=?,
                        confidence=?,
                        is_private=?,
                        updated_at=?,
                        raw_json=?
                    WHERE id=?
                    """,
                    (
                        data["message_id"],
                        new_direction,
                        data.get("counterparty_id"),
                        data.get("counterparty_name", ""),
                        data.get("counterparty_name", ""),
                        data.get("counterparty_username"),
                        data["gift_kind"],
                        data.get("title", ""),
                        data.get("title", ""),
                        data.get("unique_name"),
                        data.get("unique_number"),
                        data.get("model_name"),
                        data.get("model_rarity"),
                        data.get("symbol_name"),
                        data.get("symbol_rarity"),
                        data.get("backdrop_name"),
                        data.get("backdrop_rarity"),
                        data.get("star_count"),
                        data.get("convert_star_count"),
                        data.get("upgrade_star_count"),
                        data.get("prepaid_upgrade_star_count"),
                        data.get("owned_gift_id"),
                        data.get("origin"),
                        new_status,
                        data.get("confidence", "confirmed"),
                        int(data.get("is_private", False)),
                        data["occurred_at"],
                        raw_json,
                        current["id"],
                    ),
                )
                gift_row_id = int(current["id"])
            else:
                cursor = await db.execute(
                    """
                    INSERT INTO gifts (
                        connection_id, chat_id, source_message_id,
                        latest_message_id, direction, counterparty_id,
                        counterparty_name, counterparty_username, gift_id,
                        gift_kind, title, unique_name, unique_number, model_name,
                        model_rarity, symbol_name, symbol_rarity, backdrop_name,
                        backdrop_rarity, star_count, convert_star_count,
                        upgrade_star_count, prepaid_upgrade_star_count,
                        owned_gift_id, origin, status, confidence, is_private,
                        first_seen_at, updated_at, raw_json
                    ) VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        data["connection_id"],
                        data["chat_id"],
                        data["message_id"],
                        data["message_id"],
                        data["direction"],
                        data.get("counterparty_id"),
                        data.get("counterparty_name", ""),
                        data.get("counterparty_username"),
                        data["gift_id"],
                        data["gift_kind"],
                        data.get("title", "Telegram Gift"),
                        data.get("unique_name"),
                        data.get("unique_number"),
                        data.get("model_name"),
                        data.get("model_rarity"),
                        data.get("symbol_name"),
                        data.get("symbol_rarity"),
                        data.get("backdrop_name"),
                        data.get("backdrop_rarity"),
                        data.get("star_count"),
                        data.get("convert_star_count"),
                        data.get("upgrade_star_count"),
                        data.get("prepaid_upgrade_star_count"),
                        data.get("owned_gift_id"),
                        data.get("origin"),
                        data["status"],
                        data.get("confidence", "confirmed"),
                        int(data.get("is_private", False)),
                        data["occurred_at"],
                        data["occurred_at"],
                        _json(data["raw"]),
                    ),
                )
                gift_row_id = int(cursor.lastrowid)
                changed = True

            if changed:
                await db.execute(
                    """
                    INSERT INTO gift_events (
                        gift_id, event_type, confidence, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        gift_row_id,
                        data["event_type"],
                        data.get("confidence", "confirmed"),
                        _json(data.get("event_details", {})),
                        data["occurred_at"],
                    ),
                )
                await db.execute(
                    """
                    INSERT INTO events (
                        connection_id, chat_id, message_id, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        data["connection_id"],
                        data["chat_id"],
                        data["message_id"],
                        data["event_type"],
                        _json({"gift_row_id": gift_row_id}),
                        data["occurred_at"],
                    ),
                )
            await db.commit()
            gift_rows = await db.execute_fetchall(
                "SELECT * FROM gifts WHERE id=?",
                (gift_row_id,),
            )
        return dict(gift_rows[0]), changed, matched_existing

    async def mark_gift_message_deleted(
        self,
        connection_id: str,
        chat_id: int,
        message_id: int,
        deleted_at: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM gifts
                WHERE connection_id=? AND chat_id=?
                  AND (source_message_id=? OR latest_message_id=?)
                ORDER BY id DESC LIMIT 1
                """,
                (connection_id, chat_id, message_id, message_id),
            )
            if not rows:
                return None, False
            gift = dict(rows[0])
            source_only = (
                message_id == gift["source_message_id"]
                and gift["latest_message_id"] != gift["source_message_id"]
            )
            if source_only and gift["status"] in {
                "upgraded",
                "transferred",
                "resold",
                "offer",
            }:
                event_type = "gift_source_message_deleted"
                should_alert = False
            elif gift["status"] == "missing":
                return gift, False
            else:
                event_type = "gift_missing"
                should_alert = True
                await db.execute(
                    """
                    UPDATE gifts SET
                        status='missing', confidence='inferred', updated_at=?
                    WHERE id=?
                    """,
                    (deleted_at, gift["id"]),
                )
            await db.execute(
                """
                INSERT INTO gift_events (
                    gift_id, event_type, confidence, details_json, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    gift["id"],
                    event_type,
                    "inferred",
                    _json({"deleted_message_id": message_id}),
                    deleted_at,
                ),
            )
            await db.commit()
            updated = await db.execute_fetchall(
                "SELECT * FROM gifts WHERE id=?",
                (gift["id"],),
            )
        return dict(updated[0]), should_alert

    async def dashboard_stats(self) -> dict[str, int]:
        async with self.connect() as db:
            row = await db.execute_fetchall(
                """
                SELECT
                    (SELECT COUNT(*) FROM chats) AS chats,
                    (SELECT COUNT(*) FROM messages) AS messages,
                    (SELECT COUNT(*) FROM messages WHERE is_deleted=1) AS deleted,
                    (SELECT COALESCE(SUM(edit_count), 0) FROM messages) AS edits,
                    (SELECT COUNT(*) FROM gifts) AS gifts,
                    (SELECT COUNT(*) FROM gifts WHERE status='upgraded') AS gift_upgraded,
                    (SELECT COUNT(*) FROM gifts WHERE status='missing') AS gift_missing,
                    (SELECT COUNT(*) FROM connections WHERE is_enabled=1) AS connections
                """
            )
        return dict(row[0])

    async def list_chats(
        self, query: str = "", limit: int = 100
    ) -> list[dict[str, Any]]:
        pattern = f"%{query.strip()}%"
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT c.*, cn.owner_name
                FROM chats c
                JOIN connections cn ON cn.id=c.connection_id
                WHERE ?='' OR c.title LIKE ? OR COALESCE(c.username, '') LIKE ?
                ORDER BY c.last_message_at DESC
                LIMIT ?
                """,
                (query.strip(), pattern, pattern, limit),
            )
        return [dict(row) for row in rows]

    async def recent_events(self, limit: int = 30) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT e.*, c.title AS chat_title
                FROM events e
                LEFT JOIN chats c
                  ON c.connection_id=e.connection_id AND c.chat_id=e.chat_id
                WHERE e.event_type IN ('deleted', 'edited')
                ORDER BY e.created_at DESC, e.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    async def get_chat(
        self, connection_id: str, chat_id: int
    ) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM chats WHERE connection_id=? AND chat_id=?
                """,
                (connection_id, chat_id),
            )
        return dict(rows[0]) if rows else None

    async def chat_archive_stats(
        self, connection_id: str, chat_id: int
    ) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT
                    c.title,
                    COUNT(m.id) AS total_messages,
                    SUM(CASE WHEN m.is_deleted=1 THEN 1 ELSE 0 END) AS deleted_messages,
                    COALESCE(SUM(m.edit_count), 0) AS edit_events,
                    SUM(CASE WHEN m.file_id IS NOT NULL THEN 1 ELSE 0 END) AS media_messages,
                    MIN(m.sent_at) AS first_message_at,
                    MAX(m.sent_at) AS last_message_at
                FROM chats c
                LEFT JOIN messages m
                  ON m.connection_id=c.connection_id AND m.chat_id=c.chat_id
                WHERE c.connection_id=? AND c.chat_id=?
                GROUP BY c.connection_id, c.chat_id
                """,
                (connection_id, chat_id),
            )
        if not rows:
            return None
        return {
            key: (int(value or 0) if key in {
                "total_messages",
                "deleted_messages",
                "edit_events",
                "media_messages",
            } else value)
            for key, value in dict(rows[0]).items()
        }

    async def list_messages(
        self,
        connection_id: str,
        chat_id: int,
        query: str = "",
        event_filter: str = "all",
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        clauses = ["connection_id=?", "chat_id=?"]
        params: list[Any] = [connection_id, chat_id]
        if query.strip():
            clauses.append("(COALESCE(text, '') LIKE ? OR COALESCE(caption, '') LIKE ?)")
            pattern = f"%{query.strip()}%"
            params.extend((pattern, pattern))
        if event_filter == "deleted":
            clauses.append("is_deleted=1")
        elif event_filter == "edited":
            clauses.append("edit_count>0")
        params.append(limit)
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE {' AND '.join(clauses)}
                    ORDER BY sent_at DESC, message_id DESC
                    LIMIT ?
                )
                ORDER BY sent_at ASC, message_id ASC
                """,
                params,
            )
        return [dict(row) for row in rows]

    async def get_message(self, row_id: int) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                "SELECT * FROM messages WHERE id=?",
                (row_id,),
            )
        return dict(rows[0]) if rows else None

    async def get_versions(self, row_id: int) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM message_versions
                WHERE message_row_id=? ORDER BY version_no DESC
                """,
                (row_id,),
            )
        return [dict(row) for row in rows]

    async def gift_stats(self) -> dict[str, int]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN direction='sent' THEN 1 ELSE 0 END) AS sent,
                    SUM(CASE WHEN direction='received' THEN 1 ELSE 0 END) AS received,
                    SUM(CASE WHEN status='upgraded' THEN 1 ELSE 0 END) AS upgraded,
                    SUM(CASE WHEN status='missing' THEN 1 ELSE 0 END) AS missing
                FROM gifts
                """
            )
        return {key: int(value or 0) for key, value in dict(rows[0]).items()}

    async def list_gifts(
        self, status: str = "all", direction: str = "all", limit: int = 300
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status != "all":
            clauses.append("g.status=?")
            params.append(status)
        if direction != "all":
            clauses.append("g.direction=?")
            params.append(direction)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT g.*, c.title AS chat_title
                FROM gifts g
                JOIN chats c
                  ON c.connection_id=g.connection_id AND c.chat_id=g.chat_id
                {where}
                ORDER BY g.updated_at DESC, g.id DESC
                LIMIT ?
                """,
                params,
            )
        return [dict(row) for row in rows]

    async def get_gift(self, gift_id: int) -> dict[str, Any] | None:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT g.*, c.title AS chat_title
                FROM gifts g
                JOIN chats c
                  ON c.connection_id=g.connection_id AND c.chat_id=g.chat_id
                WHERE g.id=?
                """,
                (gift_id,),
            )
        return dict(rows[0]) if rows else None

    async def get_gift_events(self, gift_id: int) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM gift_events
                WHERE gift_id=? ORDER BY created_at DESC, id DESC
                """,
                (gift_id,),
            )
        return [dict(row) for row in rows]

    async def recent_gift_events(self, limit: int = 20) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT ge.*, g.title, g.unique_name, g.counterparty_name,
                       g.status, g.direction
                FROM gift_events ge
                JOIN gifts g ON g.id=ge.gift_id
                ORDER BY ge.created_at DESC, ge.id DESC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    async def gift_profile_targets(self, limit: int = 5) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT
                    g.connection_id,
                    g.counterparty_id,
                    MIN(COALESCE(gps.last_checked_at, '')) AS last_checked_at
                FROM gifts g
                LEFT JOIN gift_profile_state gps ON gps.gift_id=g.id
                WHERE g.direction='sent'
                  AND g.counterparty_id IS NOT NULL
                  AND g.status NOT IN ('missing', 'refunded', 'transferred', 'resold')
                  AND datetime(g.first_seen_at) >= datetime('now', '-30 days')
                GROUP BY g.connection_id, g.counterparty_id
                HAVING last_checked_at=''
                   OR MAX(datetime(g.first_seen_at)) >= datetime('now', '-10 minutes')
                   OR SUM(
                        CASE WHEN COALESCE(gps.miss_count, 0) BETWEEN 1 AND 2
                             THEN 1 ELSE 0 END
                      ) > 0
                   OR MIN(datetime(gps.last_checked_at)) <= datetime('now', '-60 seconds')
                ORDER BY last_checked_at ASC, g.counterparty_id ASC
                LIMIT ?
                """,
                (limit,),
            )
        return [dict(row) for row in rows]

    async def tracked_gifts_for_recipient(
        self, connection_id: str, recipient_id: int
    ) -> list[dict[str, Any]]:
        async with self.connect() as db:
            rows = await db.execute_fetchall(
                """
                SELECT g.*, gps.is_visible, gps.last_checked_at, gps.miss_count
                FROM gifts g
                LEFT JOIN gift_profile_state gps ON gps.gift_id=g.id
                WHERE g.connection_id=? AND g.counterparty_id=?
                  AND g.direction='sent'
                  AND g.status NOT IN ('missing', 'refunded', 'transferred', 'resold')
                  AND datetime(g.first_seen_at) >= datetime('now', '-30 days')
                ORDER BY g.first_seen_at DESC, g.id DESC
                """,
                (connection_id, recipient_id),
            )
        return [dict(row) for row in rows]

    async def update_gift_inventory_presence(
        self, gift_id: int, present: bool, checked_at: str
    ) -> tuple[dict[str, Any], str | None]:
        async with self.connect() as db:
            gift_rows = await db.execute_fetchall(
                "SELECT * FROM gifts WHERE id=?",
                (gift_id,),
            )
            if not gift_rows:
                raise LookupError(f"Gift {gift_id} not found")
            gift = dict(gift_rows[0])
            state_rows = await db.execute_fetchall(
                "SELECT * FROM gift_profile_state WHERE gift_id=?",
                (gift_id,),
            )
            previous = (
                None
                if not state_rows or state_rows[0]["is_visible"] is None
                else bool(state_rows[0]["is_visible"])
            )
            event_type: str | None = None
            if previous is None and not present:
                event_type = "gift_inventory_missing"
            elif previous is not None and present and previous is not True:
                event_type = "gift_inventory_present"
            elif previous is not None and not present and previous is True:
                event_type = "gift_inventory_missing"

            await db.execute(
                """
                INSERT INTO gift_profile_state (
                    gift_id, is_visible, first_visible_at, last_visible_at,
                    last_checked_at, miss_count
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(gift_id) DO UPDATE SET
                    is_visible=excluded.is_visible,
                    first_visible_at=COALESCE(
                        gift_profile_state.first_visible_at,
                        excluded.first_visible_at
                    ),
                    last_visible_at=COALESCE(
                        excluded.last_visible_at,
                        gift_profile_state.last_visible_at
                    ),
                    last_checked_at=excluded.last_checked_at,
                    miss_count=CASE
                        WHEN excluded.is_visible=1 THEN 0
                        ELSE gift_profile_state.miss_count+1
                    END
                """,
                (
                    gift_id,
                    int(present),
                    checked_at if present else None,
                    checked_at if present else None,
                    checked_at,
                    0 if present else 1,
                ),
            )
            if event_type:
                confidence = "confirmed" if present else "inferred"
                details = {
                    "inventory_present": present,
                    "previous_present": previous,
                }
                await db.execute(
                    """
                    INSERT INTO gift_events (
                        gift_id, event_type, confidence, details_json, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        gift_id,
                        event_type,
                        confidence,
                        _json(details),
                        checked_at,
                    ),
                )
                await db.execute(
                    """
                    INSERT INTO events (
                        connection_id, chat_id, message_id, event_type,
                        payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        gift["connection_id"],
                        gift["chat_id"],
                        gift["latest_message_id"],
                        event_type,
                        _json({"gift_row_id": gift_id, **details}),
                        checked_at,
                    ),
                )
            await db.commit()
        return gift, event_type

    async def update_gift_profile_visibility(
        self, gift_id: int, visible: bool, checked_at: str
    ) -> tuple[dict[str, Any], str | None]:
        return await self.update_gift_inventory_presence(
            gift_id, visible, checked_at
        )

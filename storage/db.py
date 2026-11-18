"""Thread-safe SQLite storage for persistent chat and file-transfer history."""

from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import time
from typing import Any


def dm_conv_id(user_a: str, user_b: str) -> str:
    """Return canonical conversation ID for a 1-to-1 direct message pair."""
    first, second = sorted((user_a, user_b))
    return f"dm:{first}:{second}"


class HistoryStore:
    """SQLite-backed store for broadcast/DM messages and transfer audit logs."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conv_type TEXT NOT NULL,
                    conv_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL DEFAULT '',
                    text TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_conv
                ON messages (conv_type, conv_id, id)
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS transfer_logs (
                    session_id TEXT PRIMARY KEY,
                    sender TEXT NOT NULL,
                    receiver TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filesize INTEGER NOT NULL,
                    sha256 TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    is_broadcast INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    room_id TEXT PRIMARY KEY,
                    created_by TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_members (
                    room_id TEXT NOT NULL,
                    username TEXT NOT NULL,
                    joined_at REAL NOT NULL,
                    PRIMARY KEY (room_id, username)
                )
                """
            )
            self._conn.commit()

    def save_broadcast_message(
        self,
        sender: str,
        text: str,
        created_at: float | None = None,
    ) -> int:
        """Persist a #all broadcast message and return its row ID."""
        ts = time.time() if created_at is None else float(created_at)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages (conv_type, conv_id, sender, recipient, text, created_at)
                VALUES ('broadcast', '#all', ?, '', ?, ?)
                """,
                (sender, text, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def save_direct_message(
        self,
        sender: str,
        recipient: str,
        text: str,
        created_at: float | None = None,
    ) -> int:
        """Persist a 1-to-1 direct message and return its row ID."""
        ts = time.time() if created_at is None else float(created_at)
        conv_id = dm_conv_id(sender, recipient)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages (conv_type, conv_id, sender, recipient, text, created_at)
                VALUES ('direct', ?, ?, ?, ?, ?)
                """,
                (conv_id, sender, recipient, text, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def save_room_message(
        self,
        room_id: str,
        sender: str,
        text: str,
        created_at: float | None = None,
    ) -> int:
        """Persist a room message and return its row ID."""
        ts = time.time() if created_at is None else float(created_at)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages (conv_type, conv_id, sender, recipient, text, created_at)
                VALUES ('room', ?, ?, '', ?, ?)
                """,
                (room_id, sender, text, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def create_or_join_room(self, room_id: str, username: str) -> None:
        """Ensure room exists and add username as a member."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO rooms (room_id, created_by, created_at) VALUES (?, ?, ?)",
                (room_id, username, now),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO room_members (room_id, username, joined_at) VALUES (?, ?, ?)",
                (room_id, username, now),
            )
            self._conn.commit()

    def leave_room(self, room_id: str, username: str) -> None:
        """Remove username from a room."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM room_members WHERE room_id = ? AND username = ?",
                (room_id, username),
            )
            self._conn.commit()

    def load_all_rooms(self) -> dict[str, list[str]]:
        """Load all persisted rooms and their member lists."""
        with self._lock:
            room_rows = self._conn.execute("SELECT room_id FROM rooms ORDER BY created_at ASC").fetchall()
            member_rows = self._conn.execute(
                "SELECT room_id, username FROM room_members ORDER BY joined_at ASC"
            ).fetchall()
        rooms: dict[str, list[str]] = {row["room_id"]: [] for row in room_rows}
        for row in member_rows:
            rooms.setdefault(row["room_id"], []).append(row["username"])
        return rooms

    def get_user_history(self, username: str, limit: int = 100) -> list[dict[str, Any]]:
        """Return recent broadcast, DM, and joined-room messages visible to ``username``."""
        bounded_limit = max(1, min(int(limit), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, conv_type, conv_id, sender, recipient, text, created_at
                FROM messages
                WHERE conv_type = 'broadcast'
                   OR (conv_type = 'direct' AND (sender = ? OR recipient = ?))
                   OR (conv_type = 'room' AND conv_id IN (
                       SELECT room_id FROM room_members WHERE username = ?
                   ))
                ORDER BY id DESC
                LIMIT ?
                """,
                (username, username, username, bounded_limit),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in reversed(rows):
            result.append(
                {
                    "id": row["id"],
                    "conv_type": row["conv_type"],
                    "conv_id": row["conv_id"],
                    "sender": row["sender"],
                    "recipient": row["recipient"],
                    "text": row["text"],
                    "is_broadcast": row["conv_type"] == "broadcast",
                    "created_at": row["created_at"],
                }
            )
        return result

    def save_transfer_log(
        self,
        session_id: str,
        sender: str,
        receiver: str,
        filename: str,
        filesize: int,
        sha256: str,
        status: str,
        is_broadcast: bool = False,
        created_at: float | None = None,
    ) -> None:
        """Persist or update a completed/failed file transfer record."""
        ts = time.time() if created_at is None else float(created_at)
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO transfer_logs
                (session_id, sender, receiver, filename, filesize, sha256, status, is_broadcast, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    sender,
                    receiver,
                    filename,
                    int(filesize),
                    sha256,
                    status,
                    1 if is_broadcast else 0,
                    ts,
                ),
            )
            self._conn.commit()

    def get_transfer_logs(self, username: str, limit: int = 30) -> list[dict[str, Any]]:
        """Return recent transfer logs involving ``username`` in chronological order."""
        bounded_limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, sender, receiver, filename, filesize, sha256, status, is_broadcast, created_at
                FROM transfer_logs
                WHERE sender = ? OR receiver = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (username, username, bounded_limit),
            ).fetchall()

        return [
            {
                "session_id": row["session_id"],
                "sender": row["sender"],
                "receiver": row["receiver"],
                "filename": row["filename"],
                "filesize": row["filesize"],
                "sha256": row["sha256"],
                "status": row["status"],
                "is_broadcast": bool(row["is_broadcast"]),
                "created_at": row["created_at"],
            }
            for row in reversed(rows)
        ]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()

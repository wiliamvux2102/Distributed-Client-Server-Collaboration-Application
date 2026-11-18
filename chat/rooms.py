"""Room normalization and membership management for multi-room chat."""

from __future__ import annotations

import re
import threading
from typing import Any

_ROOM_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,24}$")


def normalize_room_id(raw_room: Any) -> str:
    """Validate and normalize a room identifier into ``#<lowercase_name>``."""
    if not isinstance(raw_room, str):
        raise ValueError("Tên phòng phải là chuỗi ký tự.")
    name = raw_room.strip().lstrip("#")
    if not _ROOM_PATTERN.fullmatch(name):
        raise ValueError("Tên phòng phải dài 2–24 ký tự ASCII (chữ, số, _ hoặc -).")
    if name.lower() == "all":
        raise ValueError("Không thể dùng tên phòng '#all' (đây là kênh chung mặc định).")
    return f"#{name.lower()}"


class RoomManager:
    """Thread-safe room registry optionally backed by HistoryStore."""

    def __init__(self, history_store=None) -> None:
        self._lock = threading.Lock()
        self._rooms: dict[str, set[str]] = {}
        self._history_store = history_store
        if history_store is not None:
            for room_id, members in history_store.load_all_rooms().items():
                self._rooms[room_id] = set(members)

    def create_room(self, raw_room: Any, username: str) -> tuple[bool, str, list[str]]:
        """Create a room (or report if it already exists) and add creator."""
        room_id = normalize_room_id(raw_room)
        with self._lock:
            if room_id in self._rooms:
                self._rooms[room_id].add(username)
                members = sorted(self._rooms[room_id])
                created = False
            else:
                self._rooms[room_id] = {username}
                members = [username]
                created = True
        if self._history_store is not None:
            self._history_store.create_or_join_room(room_id, username)
        return created, room_id, members

    def join_room(self, raw_room: Any, username: str) -> tuple[bool, str, list[str]]:
        """Join an existing room (or auto-create if missing) and return member list."""
        room_id = normalize_room_id(raw_room)
        with self._lock:
            existed = room_id in self._rooms
            self._rooms.setdefault(room_id, set()).add(username)
            members = sorted(self._rooms[room_id])
        if self._history_store is not None:
            self._history_store.create_or_join_room(room_id, username)
        return existed, room_id, members

    def leave_room(self, raw_room: Any, username: str) -> tuple[bool, str, list[str]]:
        """Remove username from room; returns (was_member, room_id, remaining_members)."""
        room_id = normalize_room_id(raw_room)
        with self._lock:
            members_set = self._rooms.get(room_id)
            if members_set is None or username not in members_set:
                return False, room_id, sorted(members_set) if members_set else []
            members_set.discard(username)
            remaining = sorted(members_set)
        if self._history_store is not None:
            self._history_store.leave_room(room_id, username)
        return True, room_id, remaining

    def is_member(self, room_id: str, username: str) -> bool:
        with self._lock:
            return username in self._rooms.get(room_id, ())

    def get_members(self, room_id: str) -> list[str]:
        with self._lock:
            return sorted(self._rooms.get(room_id, ()))

    def list_rooms(self, username: str) -> list[dict[str, Any]]:
        """Return all known rooms with members and whether ``username`` has joined."""
        with self._lock:
            return [
                {
                    "room_id": room_id,
                    "members": sorted(members),
                    "joined": username in members,
                }
                for room_id, members in sorted(self._rooms.items())
            ]

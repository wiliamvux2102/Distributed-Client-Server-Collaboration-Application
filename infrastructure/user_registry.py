import hashlib
import hmac
import socket
import threading
from dataclasses import dataclass, field
import time
import secrets


def compute_heartbeat_hmac(token: str, username: str, timestamp_ms: int) -> str:
    """Compute HMAC-SHA256 signature for a UDP heartbeat without exposing the token."""
    payload = f"{username}:{timestamp_ms}".encode("utf-8")
    return hmac.new(token.encode("utf-8"), payload, hashlib.sha256).hexdigest()


@dataclass
class UserSession:
    connection: socket.socket
    address: tuple[str, int]
    heartbeat_token: str
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    last_heartbeat: float = field(default_factory=time.monotonic)
    last_heartbeat_ts: int = 0
    
class UserRegistry:
    def __init__(self) -> None:
        # username -> thông tin phiên đang online
        self._users: dict[str, UserSession] = {}

        # Bảo vệ các thao tác đọc/ghi _users.
        self._lock = threading.Lock()

    def refresh_heartbeat(self, token: str, source_ip: str) -> bool:
        with self._lock:
            for session in self._users.values():
                if (
                    secrets.compare_digest(session.heartbeat_token, token)
                    and session.address[0] == source_ip
                ):
                    session.last_heartbeat = time.monotonic()
                    return True

        return False

    def refresh_heartbeat_signed(
        self,
        username: str,
        timestamp_ms: int,
        signature: str,
        source_ip: str,
        max_skew_seconds: float = 30.0,
    ) -> bool:
        """Verify HMAC-SHA256 signed UDP heartbeat with timestamp window and anti-replay guard."""
        now_ms = int(time.time() * 1000)
        if abs(now_ms - timestamp_ms) > int(max_skew_seconds * 1000):
            return False

        with self._lock:
            session = self._users.get(username)
            if session is None:
                return False
            if session.address[0] != source_ip:
                return False
            if timestamp_ms <= session.last_heartbeat_ts:
                return False
            expected = compute_heartbeat_hmac(session.heartbeat_token, username, timestamp_ms)
            if not secrets.compare_digest(expected, signature):
                return False
            session.last_heartbeat = time.monotonic()
            session.last_heartbeat_ts = timestamp_ms
            return True


    def remove_stale(self, timeout_seconds: float):
        now = time.monotonic()
        expired = []

        with self._lock:
            for username, session in list(self._users.items()):
                if now - session.last_heartbeat > timeout_seconds:
                    expired.append((username, session))
                    del self._users[username]

        return expired

    def get_session(self, username: str) -> UserSession | None:
        """Lấy phiên hiện tại của username, nếu có."""

        with self._lock:
            return self._users.get(username)

    def register(
        self,
        username: str,
        connection: socket.socket,
        address: tuple[str, int],
        send_lock: object,
        heartbeat_token: str,
    ) -> bool:
        """Đăng ký username nếu hiện chưa được sử dụng."""

        with self._lock:
            if username in self._users:
                return False

            self._users[username] = UserSession(
                connection=connection,
                address=address,
                heartbeat_token=heartbeat_token,
                send_lock=send_lock,
            )

            return True

    def remove_if_same(
        self,
        username: str,
        connection: socket.socket,
    ) -> bool:
        """Xóa username chỉ khi nó vẫn thuộc đúng socket này."""

        with self._lock:
            session = self._users.get(username)

            if session is None:
                return False

            if session.connection is not connection:
                return False

            del self._users[username]
            return True

    def list_online(self) -> list[str]:
        """Trả bản sao danh sách username đang online."""

        with self._lock:
            return list(self._users.keys())


# Mọi thread server dùng chung một registry.
user_registry = UserRegistry()
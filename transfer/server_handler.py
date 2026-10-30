from dataclasses import dataclass, field
import threading
import time
from typing import Callable, Any

SendToUserCallable = Callable[[str, dict[str, Any], bytes], bool]
IsUserOnlineCallable = Callable[[str], bool]


@dataclass
class ServerTransferSession:
    session_id: str
    sender: str
    receiver: str
    filename: str
    filesize: int
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


class FileTransferServerHandler:
    def __init__(
        self,
        send_to_user_fn: SendToUserCallable,
        is_user_online_fn: IsUserOnlineCallable
    ) -> None:
        self.send_to_user = send_to_user_fn
        self.is_user_online = is_user_online_fn

        self._active_sessions: dict[str, ServerTransferSession] = {}
        self._lock = threading.Lock()

    def handle_message(
        self,
        from_user: str,
        header: dict[str, Any],
        payload: bytes = b""
    ) -> None:
        """Route incoming FILE_* messages from clients."""
        msg_type = header.get("type")
        if msg_type == "FILE_INIT":
            self._handle_init(from_user, header)
        elif msg_type in ("FILE_ACCEPT", "FILE_CHUNK", "FILE_END", "FILE_RESULT", "FILE_ERROR"):
            self._handle_relay(from_user, header, payload)

    def _handle_init(self, sender_user: str, header: dict[str, Any]) -> None:
        """Handle FILE_INIT: verify recipient is online, create session record, and forward."""
        session_id = header.get("session_id")
        receiver = header.get("receiver")
        filename = header.get("filename", "unknown")
        filesize = int(header.get("filesize", 0))

        # Enforce sender authentication (prevent spoofing)
        header["sender"] = sender_user

        if not session_id or not receiver:
            return

        if not self.is_user_online(receiver):
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_USER_OFFLINE",
                    "reason": f"User '{receiver}' is currently offline."
                },
                b""
            )
            return

        session = ServerTransferSession(
            session_id=session_id,
            sender=sender_user,
            receiver=receiver,
            filename=filename,
            filesize=filesize
        )

        with self._lock:
            self._active_sessions[session_id] = session

        success = self.send_to_user(receiver, header, b"")
        if not success:
            with self._lock:
                self._active_sessions.pop(session_id, None)

            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_USER_OFFLINE",
                    "reason": f"Failed to deliver file init to '{receiver}'."
                },
                b""
            )

    def _handle_relay(
        self,
        from_user: str,
        header: dict[str, Any],
        payload: bytes
    ) -> None:
        """Relay control frames and data chunks between sender and receiver."""
        session_id = header.get("session_id")
        if not session_id:
            return

        with self._lock:
            session = self._active_sessions.get(session_id)

        if not session:
            self.send_to_user(
                from_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_SESSION_NOT_FOUND",
                    "reason": "Transfer session not found or expired."
                },
                b""
            )
            return

        # Determine target user
        if from_user == session.sender:
            target_user = session.receiver
        elif from_user == session.receiver:
            target_user = session.sender
        else:
            return

        session.last_updated = time.time()

        success = self.send_to_user(target_user, header, payload)
        if not success:
            self.on_client_disconnect(target_user)
            return

        # Clean up session upon completion or error
        msg_type = header.get("type")
        if msg_type in ("FILE_RESULT", "FILE_ERROR"):
            with self._lock:
                self._active_sessions.pop(session_id, None)

    def on_client_disconnect(self, disconnected_user: str) -> None:
        """Handle client disconnection: notify peers of active file transfers and cleanup sessions."""
        with self._lock:
            affected_sessions = [
                s for s in self._active_sessions.values()
                if s.sender == disconnected_user or s.receiver == disconnected_user
            ]
            for s in affected_sessions:
                self._active_sessions.pop(s.session_id, None)

        for s in affected_sessions:
            other_user = s.receiver if s.sender == disconnected_user else s.sender
            try:
                self.send_to_user(
                    other_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": s.session_id,
                        "code": "ERR_PEER_DISCONNECTED",
                        "reason": f"Peer '{disconnected_user}' disconnected unexpectedly."
                    },
                    b""
                )
            except Exception:
                pass

"""
Server-side file transfer routing and session management.
"""

from __future__ import annotations
import base64
from dataclasses import dataclass, field
import socket
import threading
import time
from typing import Callable, Any

from common import message_types as mt

SendToUserCallable = Callable[[str, dict[str, Any], bytes], bool]
IsUserOnlineCallable = Callable[[str], bool]


@dataclass
class ServerTransferSession:
    """Server-side record of an in-flight file transfer session."""

    session_id: str
    sender: str
    receiver: str
    filename: str
    filesize: int
    created_at: float = field(default_factory=time.time)
    last_updated: float = field(default_factory=time.time)


class FileTransferServerHandler:
    """Manages active file transfer sessions and cut-through streaming relay on the server."""

    def __init__(
        self,
        send_to_user_fn: SendToUserCallable,
        is_user_online_fn: IsUserOnlineCallable,
    ) -> None:
        self.send_to_user = send_to_user_fn
        self.is_user_online = is_user_online_fn

        self._active_sessions: dict[str, ServerTransferSession] = {}
        self._lock = threading.Lock()

    def handle_message(
        self,
        from_user: str,
        header: dict[str, Any],
        payload: bytes = b"",
    ) -> None:
        """Route incoming FILE_* messages from connected clients."""
        msg_type = header.get("type")
        if msg_type == "FILE_INIT":
            self._handle_init(from_user, header)
        elif msg_type in ("FILE_ACCEPT", "FILE_CHUNK", "FILE_END", "FILE_RESULT", "FILE_ERROR"):
            self._handle_relay(from_user, header, payload)

    def _handle_init(self, sender_user: str, header: dict[str, Any]) -> None:
        """Validate recipient online status, create session entry, and forward FILE_INIT."""
        session_id = header.get("session_id")
        receiver = header.get("receiver") or header.get("recipient")
        filename = header.get("filename", "unknown")
        filesize = int(header.get("filesize", 0))

        # Enforce sender authenticity to prevent username spoofing
        header["sender"] = sender_user
        if receiver:
            header["receiver"] = receiver

        if not session_id or not receiver:
            return

        if receiver == sender_user:
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_RECIPIENT_SELF",
                    "reason": "Không thể gửi file cho chính mình.",
                },
                b"",
            )
            return

        if not self.is_user_online(receiver):
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_USER_OFFLINE",
                    "reason": f"User '{receiver}' is currently offline.",
                },
                b"",
            )
            return

        session = ServerTransferSession(
            session_id=session_id,
            sender=sender_user,
            receiver=receiver,
            filename=filename,
            filesize=filesize,
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
                    "reason": f"Failed to deliver file init to '{receiver}'.",
                },
                b"",
            )

    def _handle_relay(
        self,
        from_user: str,
        header: dict[str, Any],
        payload: bytes,
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
                    "reason": "Transfer session not found or expired.",
                },
                b"",
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

        # Clean up session upon termination or error
        msg_type = header.get("type")
        if msg_type in ("FILE_RESULT", "FILE_ERROR"):
            with self._lock:
                self._active_sessions.pop(session_id, None)

    def on_client_disconnect(self, disconnected_user: str) -> None:
        """Notify active transfer peers and clean up sessions upon unexpected disconnect."""
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
                        "reason": f"Peer '{disconnected_user}' disconnected unexpectedly.",
                    },
                    b"",
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Server router integration (wiring with TV1 MessageDispatcher & user_registry)
# ---------------------------------------------------------------------------

def _send_to_user_bridge(target_username: str, header: dict[str, Any], payload: bytes = b"") -> bool:
    """Relay a message to an online user's socket connection."""
    from infrastructure.user_registry import user_registry
    from common.protocol import send_message

    session = user_registry.get_session(target_username)
    if session is None:
        return False

    try:
        message = dict(header)
        if payload:
            message["binary_payload_base64"] = base64.b64encode(payload).decode("utf-8")

        with session.send_lock:
            send_message(session.connection, message)
        return True
    except Exception:
        try:
            session.connection.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        user_registry.remove_if_same(target_username, session.connection)
        return False


def _is_user_online_bridge(username: str) -> bool:
    from infrastructure.user_registry import user_registry
    return user_registry.get_session(username) is not None


# Global server handler instance used by server router
file_transfer_server_handler = FileTransferServerHandler(
    send_to_user_fn=_send_to_user_bridge,
    is_user_online_fn=_is_user_online_bridge,
)


def handle_file_transfer_message(context: Any, message: dict[str, Any]) -> None:
    """Dispatcher callback: decode optional binary payload and forward to handler."""
    payload = b""
    if "binary_payload_base64" in message:
        try:
            payload = base64.b64decode(message.pop("binary_payload_base64"))
        except Exception:
            pass

    file_transfer_server_handler.handle_message(context.username, message, payload)


def register_file_transfer_handlers(dispatcher: Any) -> None:
    """Register all FILE_* message types with TV1's MessageDispatcher."""
    for msg_type in (
        mt.FILE_INIT,
        mt.FILE_ACCEPT,
        mt.FILE_CHUNK,
        mt.FILE_END,
        mt.FILE_RESULT,
        mt.FILE_ERROR,
    ):
        dispatcher.register(msg_type, handle_file_transfer_message)

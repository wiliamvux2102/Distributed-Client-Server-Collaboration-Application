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
    state: str = "INIT"


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

        # Validation: session_id and receiver
        if not session_id or not isinstance(session_id, str):
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": "",
                    "code": "ERR_INVALID_METADATA",
                    "reason": "Missing or invalid session_id.",
                },
                b"",
            )
            return

        if not receiver or not isinstance(receiver, str):
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_INVALID_METADATA",
                    "reason": "Missing or invalid receiver.",
                },
                b"",
            )
            return

        # Validation: filename (check against path traversal)
        if not filename or not isinstance(filename, str) or ".." in filename:
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_INVALID_METADATA",
                    "reason": "Invalid or unsafe filename.",
                },
                b"",
            )
            return

        # Validation: filesize
        raw_filesize = header.get("filesize")
        try:
            filesize = int(raw_filesize)
            if filesize < 0:
                raise ValueError("filesize must be non-negative")
        except (TypeError, ValueError):
            self.send_to_user(
                sender_user,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_INVALID_METADATA",
                    "reason": f"Invalid filesize: {raw_filesize}",
                },
                b"",
            )
            return

        # Validation: optional chunk_size and total_chunks
        raw_chunk_size = header.get("chunk_size")
        if raw_chunk_size is not None:
            try:
                chunk_size = int(raw_chunk_size)
                if chunk_size <= 0:
                    raise ValueError("chunk_size must be positive")
            except (TypeError, ValueError):
                self.send_to_user(
                    sender_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_INVALID_METADATA",
                        "reason": f"Invalid chunk_size: {raw_chunk_size}",
                    },
                    b"",
                )
                return

        raw_total_chunks = header.get("total_chunks")
        if raw_total_chunks is not None:
            try:
                total_chunks = int(raw_total_chunks)
                if total_chunks < 0:
                    raise ValueError("total_chunks must be non-negative")
            except (TypeError, ValueError):
                self.send_to_user(
                    sender_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_INVALID_METADATA",
                        "reason": f"Invalid total_chunks: {raw_total_chunks}",
                    },
                    b"",
                )
                return

        # Enforce sender authenticity to prevent username spoofing
        header["sender"] = sender_user
        header["receiver"] = receiver

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

        with self._lock:
            if session_id in self._active_sessions:
                self.send_to_user(
                    sender_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_DUPLICATE_SESSION",
                        "reason": f"Session ID '{session_id}' already active.",
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
                state="INIT",
            )
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
        """Relay control frames and data chunks between sender and receiver with state validation."""
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

        msg_type = header.get("type")

        # Validate message direction and state machine
        if msg_type == "FILE_ACCEPT":
            if from_user != session.receiver:
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_UNAUTHORIZED_ROLE",
                        "reason": f"Only receiver '{session.receiver}' can send FILE_ACCEPT.",
                    },
                    b"",
                )
                return
            if session.state != "INIT":
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_INVALID_STATE",
                        "reason": f"Cannot accept session in state '{session.state}'.",
                    },
                    b"",
                )
                return
            session.state = "ACCEPTED"
            target_user = session.sender

        elif msg_type == "FILE_CHUNK":
            if from_user != session.sender:
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_UNAUTHORIZED_ROLE",
                        "reason": f"Only sender '{session.sender}' can send FILE_CHUNK.",
                    },
                    b"",
                )
                return
            if session.state not in ("ACCEPTED", "TRANSFERRING"):
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_INVALID_STATE",
                        "reason": f"Cannot send chunks in state '{session.state}'.",
                    },
                    b"",
                )
                return
            session.state = "TRANSFERRING"
            target_user = session.receiver

        elif msg_type == "FILE_END":
            if from_user != session.sender:
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_UNAUTHORIZED_ROLE",
                        "reason": f"Only sender '{session.sender}' can send FILE_END.",
                    },
                    b"",
                )
                return
            if session.state not in ("TRANSFERRING", "ACCEPTED"):
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_INVALID_STATE",
                        "reason": f"Cannot send FILE_END in state '{session.state}'.",
                    },
                    b"",
                )
                return
            session.state = "WAITING_RESULT"
            target_user = session.receiver

        elif msg_type == "FILE_RESULT":
            if from_user != session.receiver:
                self.send_to_user(
                    from_user,
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_UNAUTHORIZED_ROLE",
                        "reason": f"Only receiver '{session.receiver}' can send FILE_RESULT.",
                    },
                    b"",
                )
                return
            session.state = "COMPLETED"
            target_user = session.sender

        elif msg_type == "FILE_ERROR":
            if from_user not in (session.sender, session.receiver):
                return
            session.state = "FAILED"
            target_user = session.receiver if from_user == session.sender else session.sender

        else:
            return

        session.last_updated = time.time()

        success = self.send_to_user(target_user, header, payload)
        if not success:
            self.on_client_disconnect(target_user)
            return

        # Clean up session upon termination or error
        if msg_type in ("FILE_RESULT", "FILE_ERROR"):
            with self._lock:
                self._active_sessions.pop(session_id, None)

    def prune_stale_sessions(self, timeout_seconds: float = 60.0) -> list[str]:
        """Prune transfer sessions that have been inactive longer than timeout_seconds."""
        now = time.time()
        stale_sessions: list[ServerTransferSession] = []

        with self._lock:
            for s in list(self._active_sessions.values()):
                if (now - s.last_updated) > timeout_seconds:
                    stale_sessions.append(s)
                    self._active_sessions.pop(s.session_id, None)

        for s in stale_sessions:
            for user in (s.sender, s.receiver):
                try:
                    self.send_to_user(
                        user,
                        {
                            "type": "FILE_ERROR",
                            "session_id": s.session_id,
                            "code": "ERR_SESSION_TIMEOUT",
                            "reason": f"File transfer session timed out after {timeout_seconds}s of inactivity.",
                        },
                        b"",
                    )
                except Exception:
                    pass

        return [s.session_id for s in stale_sessions]

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
# Server router integration
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
        raw_b64 = message.pop("binary_payload_base64")
        try:
            payload = base64.b64decode(raw_b64, validate=True)
        except Exception as error:
            session_id = message.get("session_id", "unknown")
            file_transfer_server_handler.send_to_user(
                context.username,
                {
                    "type": "FILE_ERROR",
                    "session_id": session_id,
                    "code": "ERR_CORRUPT_PAYLOAD",
                    "reason": f"Dữ liệu Base64 bị hỏng hoặc sai định dạng: {error}",
                },
                b"",
            )
            return

    file_transfer_server_handler.handle_message(context.username, message, payload)


def register_file_transfer_handlers(dispatcher: Any) -> None:
    """Register all FILE_*"""
    for msg_type in (
        mt.FILE_INIT,
        mt.FILE_ACCEPT,
        mt.FILE_CHUNK,
        mt.FILE_END,
        mt.FILE_RESULT,
        mt.FILE_ERROR,
    ):
        dispatcher.register(msg_type, handle_file_transfer_message)

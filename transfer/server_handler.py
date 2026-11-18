"""
Server-side file transfer routing and session management.
"""

from __future__ import annotations
import base64
from dataclasses import dataclass, field
import logging
import socket
import threading
import time
from typing import Callable, Any

from common import message_types as mt
from transfer.integrity import (
    is_safe_filename,
    is_valid_transfer_id,
    parse_non_negative_int,
)

logger = logging.getLogger(__name__)

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
        history_store=None,
    ) -> None:
        self.send_to_user = send_to_user_fn
        self.is_user_online = is_user_online_fn
        self.history_store = history_store

        self._active_sessions: dict[str, ServerTransferSession] = {}
        self._lock = threading.Lock()

    def set_history_store(self, history_store) -> None:
        """Attach an optional HistoryStore for recording completed/failed transfers."""
        self.history_store = history_store

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

    def _send_error(self, target_user: str, session_id: str, code: str, reason: str) -> None:
        self.send_to_user(
            target_user,
            {
                "type": "FILE_ERROR",
                "session_id": session_id,
                "code": code,
                "reason": reason,
            },
            b"",
        )

    def _handle_init(self, sender_user: str, header: dict[str, Any]) -> None:
        """Validate recipient online status, create session entry, and forward FILE_INIT."""
        session_id = header.get("session_id")
        receiver = header.get("receiver") or header.get("recipient")
        filename = header.get("filename", "unknown")

        if not is_valid_transfer_id(session_id):
            self._send_error(sender_user, "", "ERR_INVALID_METADATA", "Missing or invalid session_id.")
            return

        if not isinstance(receiver, str) or not receiver.strip():
            self._send_error(sender_user, session_id, "ERR_INVALID_METADATA", "Missing or invalid receiver.")
            return
        receiver = receiver.strip()

        if not is_safe_filename(filename):
            self._send_error(sender_user, session_id, "ERR_INVALID_METADATA", "Invalid or unsafe filename.")
            return

        raw_filesize = header.get("filesize")
        try:
            filesize = parse_non_negative_int(raw_filesize, "filesize")
        except ValueError:
            self._send_error(sender_user, session_id, "ERR_INVALID_METADATA", f"Invalid filesize: {raw_filesize}")
            return

        raw_chunk_size = header.get("chunk_size")
        if raw_chunk_size is not None:
            try:
                chunk_size = parse_non_negative_int(raw_chunk_size, "chunk_size")
                if chunk_size <= 0:
                    raise ValueError("chunk_size must be positive")
            except ValueError:
                self._send_error(
                    sender_user, session_id, "ERR_INVALID_METADATA", f"Invalid chunk_size: {raw_chunk_size}"
                )
                return

        raw_total_chunks = header.get("total_chunks")
        if raw_total_chunks is not None:
            try:
                parse_non_negative_int(raw_total_chunks, "total_chunks")
            except ValueError:
                self._send_error(
                    sender_user, session_id, "ERR_INVALID_METADATA", f"Invalid total_chunks: {raw_total_chunks}"
                )
                return

        raw_is_broadcast = header.get("is_broadcast", False)
        if not isinstance(raw_is_broadcast, bool):
            self._send_error(
                sender_user, session_id, "ERR_INVALID_METADATA", "Invalid is_broadcast flag."
            )
            return

        # Enforce sender authenticity to prevent username spoofing
        header["sender"] = sender_user
        header["receiver"] = receiver

        if receiver == sender_user:
            self._send_error(sender_user, session_id, "ERR_RECIPIENT_SELF", "Không thể gửi file cho chính mình.")
            return

        if not self.is_user_online(receiver):
            self._send_error(sender_user, session_id, "ERR_USER_OFFLINE", f"User '{receiver}' is currently offline.")
            return

        duplicate = False
        with self._lock:
            if session_id in self._active_sessions:
                duplicate = True
            else:
                self._active_sessions[session_id] = ServerTransferSession(
                    session_id=session_id,
                    sender=sender_user,
                    receiver=receiver,
                    filename=filename,
                    filesize=filesize,
                    state="INIT",
                )

        if duplicate:
            self._send_error(sender_user, session_id, "ERR_DUPLICATE_SESSION", f"Session ID '{session_id}' already active.")
            return

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
        if not isinstance(session_id, str) or not session_id:
            return

        msg_type = header.get("type")
        target_user: str | None = None
        error_code: str | None = None
        error_reason: str = ""

        # Validate role, state transitions, and terminal cleanup atomically under self._lock
        # before performing network I/O to prevent concurrent thread races and lock inversion.
        with self._lock:
            session = self._active_sessions.get(session_id)
            if not session:
                error_code = "ERR_SESSION_NOT_FOUND"
                error_reason = "Transfer session not found or expired."
            elif msg_type == "FILE_ACCEPT":
                if from_user != session.receiver:
                    error_code = "ERR_UNAUTHORIZED_ROLE"
                    error_reason = f"Only receiver '{session.receiver}' can send FILE_ACCEPT."
                elif session.state != "INIT":
                    error_code = "ERR_INVALID_STATE"
                    error_reason = f"Cannot accept session in state '{session.state}'."
                else:
                    session.state = "ACCEPTED"
                    target_user = session.sender
            elif msg_type == "FILE_CHUNK":
                if from_user != session.sender:
                    error_code = "ERR_UNAUTHORIZED_ROLE"
                    error_reason = f"Only sender '{session.sender}' can send FILE_CHUNK."
                elif session.state not in ("ACCEPTED", "TRANSFERRING"):
                    error_code = "ERR_INVALID_STATE"
                    error_reason = f"Cannot send chunks in state '{session.state}'."
                else:
                    session.state = "TRANSFERRING"
                    target_user = session.receiver
            elif msg_type == "FILE_END":
                if from_user != session.sender:
                    error_code = "ERR_UNAUTHORIZED_ROLE"
                    error_reason = f"Only sender '{session.sender}' can send FILE_END."
                elif session.state not in ("TRANSFERRING", "ACCEPTED"):
                    error_code = "ERR_INVALID_STATE"
                    error_reason = f"Cannot send FILE_END in state '{session.state}'."
                else:
                    session.state = "WAITING_RESULT"
                    target_user = session.receiver
            elif msg_type == "FILE_RESULT":
                if from_user != session.receiver:
                    error_code = "ERR_UNAUTHORIZED_ROLE"
                    error_reason = f"Only receiver '{session.receiver}' can send FILE_RESULT."
                elif session.filesize == 0 and session.state not in ("INIT", "WAITING_RESULT"):
                    error_code = "ERR_INVALID_STATE"
                    error_reason = f"Cannot send FILE_RESULT in state '{session.state}'."
                elif session.filesize > 0 and session.state != "WAITING_RESULT":
                    error_code = "ERR_INVALID_STATE"
                    error_reason = f"Cannot send FILE_RESULT in state '{session.state}' before FILE_END."
                else:
                    session.state = "COMPLETED"
                    target_user = session.sender
                    self._active_sessions.pop(session_id, None)
            elif msg_type == "FILE_ERROR":
                if from_user not in (session.sender, session.receiver):
                    return
                session.state = "FAILED"
                target_user = session.receiver if from_user == session.sender else session.sender
                self._active_sessions.pop(session_id, None)
            else:
                return

            if error_code is None and session is not None:
                session.last_updated = time.time()

        if error_code is not None:
            self._send_error(from_user, session_id, error_code, error_reason)
            return

        if msg_type == "FILE_RESULT" and session is not None and self.history_store is not None:
            try:
                status_str = "COMPLETED" if header.get("status") == "OK" else "FAILED"
                self.history_store.save_transfer_log(
                    session_id=session.session_id,
                    sender=session.sender,
                    receiver=session.receiver,
                    filename=session.filename,
                    filesize=session.filesize,
                    sha256="",
                    status=status_str,
                    is_broadcast=session.is_broadcast,
                )
            except Exception:
                pass

        if target_user is not None:
            success = self.send_to_user(target_user, header, payload)
            if not success:
                if not self.is_user_online(target_user):
                    self.on_client_disconnect(target_user)
                else:
                    # Target remains connected: abort only this failing transfer session
                    # (e.g., frame serialization error) without tearing down unrelated transfers.
                    with self._lock:
                        self._active_sessions.pop(session_id, None)
                    fail_reason = f"Failed to relay {msg_type} frame to '{target_user}'."
                    self._send_error(from_user, session_id, "ERR_INVALID_METADATA", fail_reason)
                    if target_user != from_user:
                        self._send_error(target_user, session_id, "ERR_INVALID_METADATA", fail_reason)

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
                    logger.exception("[TRANSFER] Lỗi gửi ERR_SESSION_TIMEOUT tới '%s'.", user)

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
                logger.exception("[TRANSFER] Lỗi gửi ERR_PEER_DISCONNECTED tới '%s'.", other_user)


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
    except ValueError as error:
        # Payload serialization/size error must not terminate an unrelated receiver connection.
        logger.warning(
            "[TRANSFER] Bỏ qua khung file không tuần tự hóa được tới '%s': %s",
            target_username,
            error,
        )
        return False
    except OSError:
        try:
            session.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        if user_registry.remove_if_same(target_username, session.connection):
            file_transfer_server_handler.on_client_disconnect(target_username)
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
    """Register all FILE_* message handlers with the server dispatcher."""
    for msg_type in (
        mt.FILE_INIT,
        mt.FILE_ACCEPT,
        mt.FILE_CHUNK,
        mt.FILE_END,
        mt.FILE_RESULT,
        mt.FILE_ERROR,
    ):
        dispatcher.register(msg_type, handle_file_transfer_message)

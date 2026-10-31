"""
File sender, chunk streaming worker, and client transfer manager.
"""

from __future__ import annotations
import base64
from dataclasses import dataclass, field
from pathlib import Path
import threading
import uuid
from typing import Callable, Any

from transfer.integrity import get_file_metadata, DEFAULT_CHUNK_SIZE
from transfer.receiver import FileReceiver

SendFrameCallable = Callable[[dict[str, Any], bytes], None]
ProgressCallback = Callable[[str, str, int, int], None]
CompleteCallback = Callable[[str, str, str], None]
ErrorCallback = Callable[[str, str, str], None]


@dataclass
class UploadSession:
    """State tracking for an active outbound file upload."""

    session_id: str
    sender: str
    receiver: str
    file_path: Path
    filename: str
    filesize: int
    sha256: str
    chunk_size: int
    total_chunks: int
    sent_bytes: int = 0
    cancel_event: threading.Event = field(default_factory=threading.Event)


class FileSender:
    """Manages file transfer initiation, background chunk streaming, and progress tracking."""

    def __init__(
        self,
        username: str,
        send_frame_fn: SendFrameCallable,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        on_progress: ProgressCallback | None = None,
        on_complete: CompleteCallback | None = None,
        on_error: ErrorCallback | None = None,
    ) -> None:
        self.username = username
        self.send_frame = send_frame_fn
        self.chunk_size = chunk_size
        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_error = on_error

        self._sessions: dict[str, UploadSession] = {}
        self._lock = threading.Lock()

    def start_transfer(self, receiver_username: str, file_path: str | Path) -> str:
        """Extract metadata, initialize session, and dispatch FILE_INIT."""
        path = Path(file_path)
        meta = get_file_metadata(path, self.chunk_size)
        session_id = uuid.uuid4().hex

        session = UploadSession(
            session_id=session_id,
            sender=self.username,
            receiver=receiver_username,
            file_path=path,
            **meta,
        )

        with self._lock:
            self._sessions[session_id] = session

        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": self.username,
            "receiver": receiver_username,
            **meta,
        }

        try:
            self.send_frame(header, b"")
        except Exception as error:
            with self._lock:
                self._sessions.pop(session_id, None)
            if self.on_error:
                self.on_error(session_id, meta["filename"], f"Failed to send FILE_INIT: {error}")
            raise

        return session_id

    def send_file(self, receiver_username: str, file_path: str | Path) -> str:
        """API contract alias for start_transfer as specified in PLAN_M3.md."""
        return self.start_transfer(receiver_username, file_path)

    def handle_incoming_message(self, header: dict[str, Any], payload: bytes = b"") -> None:
        """Process control messages (FILE_ACCEPT, FILE_RESULT, FILE_ERROR)."""
        msg_type = header.get("type")
        session_id = header.get("session_id", "")

        if msg_type == "FILE_ACCEPT":
            with self._lock:
                session = self._sessions.get(session_id)
            if session:
                threading.Thread(target=self._send_chunks_worker, args=(session,), daemon=True).start()

        elif msg_type == "FILE_RESULT":
            with self._lock:
                session = self._sessions.pop(session_id, None)
            if session:
                status = header.get("status")
                reason = header.get("reason", "")
                if status == "OK":
                    if self.on_complete:
                        self.on_complete(session.session_id, session.filename, "File transferred successfully.")
                else:
                    if self.on_error:
                        self.on_error(session.session_id, session.filename, f"Receiver rejected file: {reason}")

        elif msg_type == "FILE_ERROR":
            with self._lock:
                session = self._sessions.pop(session_id, None)
            if session:
                session.cancel_event.set()
                reason = header.get("reason", "Transfer error reported by peer.")
                if self.on_error:
                    self.on_error(session.session_id, session.filename, reason)

    def _send_chunks_worker(self, session: UploadSession) -> None:
        """Background thread worker to stream file chunks sequentially."""
        try:
            if session.filesize > 0:
                with session.file_path.open("rb") as file_handle:
                    for chunk_index in range(session.total_chunks):
                        if session.cancel_event.is_set():
                            return

                        chunk_data = file_handle.read(session.chunk_size)
                        if not chunk_data:
                            break

                        header = {
                            "type": "FILE_CHUNK",
                            "session_id": session.session_id,
                            "chunk_index": chunk_index,
                        }
                        self.send_frame(header, chunk_data)
                        session.sent_bytes += len(chunk_data)

                        if self.on_progress:
                            self.on_progress(
                                session.session_id,
                                session.filename,
                                session.sent_bytes,
                                session.filesize,
                            )

            # Signal completion after all chunks have been streamed
            if not session.cancel_event.is_set():
                self.send_frame(
                    {
                        "type": "FILE_END",
                        "session_id": session.session_id,
                    },
                    b"",
                )

        except Exception as error:
            session.cancel_event.set()
            with self._lock:
                self._sessions.pop(session.session_id, None)
            try:
                self.send_frame(
                    {
                        "type": "FILE_ERROR",
                        "session_id": session.session_id,
                        "reason": str(error),
                    },
                    b"",
                )
            except Exception:
                pass
            if self.on_error:
                self.on_error(session.session_id, session.filename, f"Upload error: {error}")

    def cancel_transfer(self, session_id: str, reason: str = "Sender cancelled transfer") -> None:
        """Cancel an ongoing file upload session."""
        with self._lock:
            session = self._sessions.pop(session_id, None)

        if session:
            session.cancel_event.set()
            try:
                self.send_frame(
                    {
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "reason": reason,
                    },
                    b"",
                )
            except Exception:
                pass
            if self.on_error:
                self.on_error(session_id, session.filename, reason)


class ClientFileTransferManager:
    """Coordinates FileSender and FileReceiver for the ClientSession and CLI interface."""

    def __init__(
        self,
        username: str,
        send_fn: Callable[[dict[str, Any]], None],
        print_fn: Callable[[str], None],
    ) -> None:
        self.username = username
        self.send_fn = send_fn
        self.print_fn = print_fn

        self.sender = FileSender(
            username=username,
            send_frame_fn=self._send_frame,
            on_progress=self._on_progress,
            on_complete=self._on_complete,
            on_error=self._on_error,
        )

        self.receiver = FileReceiver(
            username=username,
            send_frame_fn=self._send_frame,
            download_dir="downloads",
            on_progress=self._on_progress,
            on_complete=self._on_complete_recv,
            on_error=self._on_error,
        )

    def _send_frame(self, header: dict[str, Any], payload: bytes = b"") -> None:
        message = dict(header)
        if payload:
            message["binary_payload_base64"] = base64.b64encode(payload).decode("utf-8")
        self.send_fn(message)

    def handle_incoming(self, message: dict[str, Any]) -> None:
        """Unpack embedded base64 payload and route to receiver or sender."""
        payload = b""
        if "binary_payload_base64" in message:
            try:
                payload = base64.b64decode(message.pop("binary_payload_base64"))
            except Exception:
                pass

        msg_type = message.get("type", "")
        if msg_type in ("FILE_INIT", "FILE_CHUNK", "FILE_END", "FILE_ERROR"):
            if msg_type == "FILE_INIT":
                self.print_fn(f"[FILE] Incoming transfer '{message.get('filename')}' from '{message.get('sender')}'. Downloading...")
            self.receiver.handle_incoming_message(message, payload)
        elif msg_type in ("FILE_ACCEPT", "FILE_RESULT"):
            self.sender.handle_incoming_message(message, payload)

    def register_handlers(self, register_fn: Callable[[str, Callable[[dict], None]], None]) -> None:
        """Register all FILE_* message types with ClientSession."""
        from common import message_types as mt
        for msg_type in (
            mt.FILE_INIT,
            mt.FILE_ACCEPT,
            mt.FILE_CHUNK,
            mt.FILE_END,
            mt.FILE_RESULT,
            mt.FILE_ERROR,
        ):
            register_fn(msg_type, self.handle_incoming)

    def send_file(self, recipient: str, filepath: str | Path) -> None:
        """CLI entrypoint for the /sendfile command."""
        try:
            path = Path(filepath)
            if not path.is_file():
                self.print_fn(f"[ERROR] File not found: {path}")
                return
            self.print_fn(f"[FILE] Starting transfer of '{path.name}' to '{recipient}'...")
            self.sender.start_transfer(recipient, path)
        except Exception as error:
            self.print_fn(f"[FILE ERROR] {error}")

    def _on_progress(self, session_id: str, filename: str, current: int, total: int) -> None:
        pct = (current / total * 100) if total > 0 else 100
        self.print_fn(f"[FILE] Progress {filename}: {current}/{total} bytes ({pct:.1f}%)")

    def _on_complete(self, session_id: str, filename: str, msg: str) -> None:
        self.print_fn(f"[FILE DONE] Successfully sent '{filename}': {msg}")

    def _on_complete_recv(self, session_id: str, filename: str, saved_path: str) -> None:
        self.print_fn(f"[FILE DONE] Received and saved '{filename}' at: {saved_path}")

    def _on_error(self, session_id: str, filename: str, err: str) -> None:
        self.print_fn(f"[FILE ERROR] {filename}: {err}")

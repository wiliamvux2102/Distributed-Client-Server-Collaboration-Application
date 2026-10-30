from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import threading
import uuid
from typing import Callable, Any

from transfer.integrity import get_file_metadata, DEFAULT_CHUNK_SIZE

SendFrameCallable = Callable[[dict[str, Any], bytes], None]
ProgressCallback = Callable[[str, str, int, int], None]
CompleteCallback = Callable[[str, str, str], None]
ErrorCallback = Callable[[str, str, str], None]


@dataclass
class UploadSession:
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
    def __init__(
        self,
        username: str,
        send_frame_fn: SendFrameCallable,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        on_progress: ProgressCallback | None = None,
        on_complete: CompleteCallback | None = None,
        on_error: ErrorCallback | None = None
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
        """Initiate a file transfer session by extracting metadata and sending FILE_INIT."""
        path = Path(file_path)
        meta = get_file_metadata(path, self.chunk_size)
        session_id = uuid.uuid4().hex

        session = UploadSession(
            session_id=session_id,
            sender=self.username,
            receiver=receiver_username,
            file_path=path,
            **meta
        )

        with self._lock:
            self._sessions[session_id] = session

        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": self.username,
            "receiver": receiver_username,
            **meta
        }

        try:
            self.send_frame(header, b"")
        except Exception as e:
            with self._lock:
                self._sessions.pop(session_id, None)
            if self.on_error:
                self.on_error(session_id, meta["filename"], f"Failed to send FILE_INIT: {e}")
            raise

        return session_id

    def handle_incoming_message(self, header: dict[str, Any], payload: bytes = b"") -> None:
        """Handle incoming control messages from receiver/server (FILE_ACCEPT, FILE_RESULT, FILE_ERROR)."""
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
        """Background worker thread to read file in chunks and stream over TCP."""
        try:
            if session.filesize > 0:
                with session.file_path.open("rb") as f:
                    for chunk_index in range(session.total_chunks):
                        if session.cancel_event.is_set():
                            return

                        chunk_data = f.read(session.chunk_size)
                        if not chunk_data:
                            break

                        header = {
                            "type": "FILE_CHUNK",
                            "session_id": session.session_id,
                            "chunk_index": chunk_index
                        }
                        self.send_frame(header, chunk_data)
                        session.sent_bytes += len(chunk_data)

                        if self.on_progress:
                            self.on_progress(
                                session.session_id,
                                session.filename,
                                session.sent_bytes,
                                session.filesize
                            )

            # Send FILE_END after all chunks have been streamed
            if not session.cancel_event.is_set():
                self.send_frame(
                    {
                        "type": "FILE_END",
                        "session_id": session.session_id
                    },
                    b"",
                )

        except Exception as e:
            session.cancel_event.set()
            with self._lock:
                self._sessions.pop(session.session_id, None)
            try:
                self.send_frame(
                    {
                        "type": "FILE_ERROR",
                        "session_id": session.session_id,
                        "reason": str(e)
                    },
                    b"",
                )
            except Exception:
                pass
            if self.on_error:
                self.on_error(session.session_id, session.filename, f"Upload error: {e}")

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
                        "reason": reason
                    },
                    b"",
                )
            except Exception:
                pass
            if self.on_error:
                self.on_error(session_id, session.filename, reason)

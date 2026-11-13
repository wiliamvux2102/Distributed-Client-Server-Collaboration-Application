"""
File sender, chunk streaming worker, and client transfer manager.
"""

from __future__ import annotations
import base64
from dataclasses import dataclass, field
from pathlib import Path
import threading
import time
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

                        # Yield GIL briefly so UI/receiver threads can render real-time progress
                        time.sleep(0.001)

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

    def cancel_all(self, reason: str = "Client disconnected") -> None:
        """Cancel all active upload sessions."""
        with self._lock:
            session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            self.cancel_transfer(session_id, reason)


def _format_size(size_bytes: int) -> str:
    """Format byte counts into human-readable strings (B, KB, MB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _render_bar(pct: float, width: int = 15) -> str:
    """Render a clean text progress bar (pip/npm/cargo style)."""
    filled = max(0, min(width, int(width * pct / 100)))
    if filled >= width:
        return "=" * width
    if filled > 0:
        return "=" * (filled - 1) + ">" + "-" * (width - filled)
    return "-" * width


class ClientFileTransferManager:
    """Coordinates FileSender and FileReceiver for the ClientSession and CLI interface."""

    def __init__(
        self,
        username: str,
        send_fn: Callable[[dict[str, Any]], None],
        print_fn: Callable[[str], None],
        status_update_fn: Callable[[], None] | None = None,
    ) -> None:
        self.username = username
        self.send_fn = send_fn
        self.print_fn = print_fn
        self.status_update_fn = status_update_fn
        self._active_transfers: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

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

    def cleanup(self, reason: str = "Client disconnected") -> None:
        """Abort all active uploads and downloads, cleaning up all temporary files."""
        self.receiver.abort_all(reason)
        self.sender.cancel_all(reason)
        with self._lock:
            self._active_transfers.clear()
        self._trigger_update()

    def get_active_status(self) -> str | None:
        """Return formatted progress string for live inline CLI status, without chat spam."""
        with self._lock:
            if not self._active_transfers:
                return None
            items = []
            for info in self._active_transfers.values():
                fn = info["filename"]
                short_fn = fn if len(fn) <= 22 else fn[:19] + "..."
                peer = info.get("peer", "")
                if info["direction"] == "upload":
                    header = f"[FILE] Gửi -> {peer}: {short_fn}" if peer else f"[FILE] Gửi: {short_fn}"
                else:
                    header = f"[FILE] Nhận <- {peer}: {short_fn}" if peer else f"[FILE] Nhận: {short_fn}"
                cur = info["current"]
                tot = info["total"]
                pct = (cur / tot * 100) if tot > 0 else 100.0
                cur_str = _format_size(cur)
                tot_str = _format_size(tot)
                bar = _render_bar(pct)
                items.append(f"{header}\n[{bar}] {pct:5.1f}% ({cur_str}/{tot_str})")
            return "\n".join(items)

    def _trigger_update(self) -> None:
        if self.status_update_fn:
            try:
                self.status_update_fn()
            except Exception:
                pass

    def _send_frame(self, header: dict[str, Any], payload: bytes = b"") -> None:
        if header.get("type") == "FILE_INIT":
            session_id = header.get("session_id", "")
            if session_id:
                with self._lock:
                    self._active_transfers[session_id] = {
                        "filename": header.get("filename", "unnamed_file"),
                        "peer": header.get("receiver", ""),
                        "direction": "upload",
                        "current": 0,
                        "total": int(header.get("filesize", 0)),
                    }
                self._trigger_update()

        message = dict(header)
        if payload:
            message["binary_payload_base64"] = base64.b64encode(payload).decode("utf-8")
        self.send_fn(message)

    def handle_incoming(self, message: dict[str, Any]) -> None:
        """Unpack embedded base64 payload and route to receiver or sender."""
        payload = b""
        if "binary_payload_base64" in message:
            raw_b64 = message.pop("binary_payload_base64")
            try:
                payload = base64.b64decode(raw_b64, validate=True)
            except Exception as error:
                session_id = message.get("session_id", "")
                self._report_error(
                    session_id,
                    message.get("filename", "unknown"),
                    f"Dữ liệu Base64 nhận được không hợp lệ: {error}",
                )
                if session_id:
                    self.send_fn({
                        "type": "FILE_ERROR",
                        "session_id": session_id,
                        "code": "ERR_CORRUPT_PAYLOAD",
                        "reason": f"Base64 decoding failed: {error}",
                    })
                    with self._lock:
                        self._active_transfers.pop(session_id, None)
                    self._trigger_update()
                    self.receiver.abort_all(f"Corrupt base64 payload: {error}")
                return

        msg_type = message.get("type", "")
        if msg_type == "FILE_ERROR":
            session_id = message.get("session_id", "")
            with self._lock:
                self._active_transfers.pop(session_id, None)
            self._trigger_update()
            # Forward error to both sender and receiver so active upload or download is notified and cleaned up
            self.sender.handle_incoming_message(message, payload)
            self.receiver.handle_incoming_message(message, payload)
        elif msg_type in ("FILE_INIT", "FILE_CHUNK", "FILE_END"):
            if msg_type == "FILE_INIT":
                filename = message.get("filename", "unnamed_file")
                sender = message.get("sender", "unknown")
                filesize = int(message.get("filesize", 0))
                session_id = message.get("session_id", "")
                with self._lock:
                    self._active_transfers[session_id] = {
                        "filename": filename,
                        "peer": sender,
                        "direction": "download",
                        "current": 0,
                        "total": filesize,
                    }
                self._trigger_update()
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
            cleaned_path = str(filepath).strip("\"' ")
            path = Path(cleaned_path)
            if not path.is_file():
                self.print_fn(f"[ERROR] File not found: {path}")
                return
            self.sender.start_transfer(recipient, path)
        except Exception as error:
            self.print_fn(f"[FILE ERROR] {error}")

    def _on_progress(self, session_id: str, filename: str, current: int, total: int) -> None:
        with self._lock:
            info = self._active_transfers.get(session_id)
            if not info:
                info = {
                    "filename": filename,
                    "peer": "",
                    "direction": "upload",
                    "current": current,
                    "total": total,
                }
                self._active_transfers[session_id] = info
            else:
                info["current"] = current
                info["total"] = total

        self._trigger_update()

    def _on_complete(self, session_id: str, filename: str, msg: str) -> None:
        with self._lock:
            info = self._active_transfers.pop(session_id, None)
        self._trigger_update()
        peer = info.get("peer", "") if info else ""
        total = info.get("total", 0) if info else 0
        peer_str = f" tới '{peer}'" if peer else ""
        bar = _render_bar(100.0)
        size_str = _format_size(total)
        self.print_fn(f"[FILE DONE] Đã gửi '{filename}'{peer_str}: [{bar}] 100% ({size_str})")

    def _on_complete_recv(self, session_id: str, filename: str, saved_path: str) -> None:
        with self._lock:
            info = self._active_transfers.pop(session_id, None)
        self._trigger_update()
        peer = info.get("peer", "") if info else ""
        total = info.get("total", 0) if info else 0
        peer_str = f" từ '{peer}'" if peer else ""
        bar = _render_bar(100.0)
        size_str = _format_size(total)
        self.print_fn(
            f"[FILE DONE] Đã nhận '{filename}'{peer_str}: [{bar}] 100% ({size_str}) -> {saved_path}"
        )

    def _on_error(self, session_id: str, filename: str, err: str) -> None:
        with self._lock:
            self._active_transfers.pop(session_id, None)
        self._trigger_update()
        self.print_fn(f"[FILE ERROR] {filename}: {err}")

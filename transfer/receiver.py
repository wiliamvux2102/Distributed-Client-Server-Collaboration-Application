"""
File receiver and temporary download session manager.
"""

from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
import os
import secrets
import socket
import threading
from typing import BinaryIO, Callable, Any

from common.app_config import DEFAULT_DOWNLOADS_DIR
from transfer.integrity import (
    is_valid_transfer_id,
    parse_non_negative_int,
    sanitize_filename,
    verify_file,
)
from transfer.p2p import (
    P2P_MAGIC_CHUNK,
    P2P_MAGIC_END,
    get_local_p2p_hosts,
    make_p2p_cipher,
    recv_p2p_binary_frame,
    recv_p2p_json,
    send_p2p_json,
)

SendFrameCallable = Callable[[dict[str, Any], bytes], None]
ProgressCallback = Callable[[str, str, int, int], None]
CompleteCallback = Callable[[str, str, str], None]
ErrorCallback = Callable[[str, str, str], None]


@dataclass
class DownloadSession:
    """State tracking for an active inbound file download."""

    session_id: str
    sender: str
    filename: str
    filesize: int
    expected_sha256: str
    total_chunks: int
    temp_path: Path
    file_handle: BinaryIO
    received_bytes: int = 0
    next_chunk_index: int = 0
    p2p_server_socket: socket.socket | None = None


class FileReceiver:
    """Manages temporary (.part) downloads, chunk sequence validation, and final integrity checks."""

    def __init__(
        self,
        username: str,
        send_frame_fn: SendFrameCallable,
        download_dir: str | Path = DEFAULT_DOWNLOADS_DIR,
        on_progress: ProgressCallback | None = None,
        on_complete: CompleteCallback | None = None,
        on_error: ErrorCallback | None = None,
        enable_p2p: bool = True,
    ) -> None:
        self.username = username
        self.send_frame = send_frame_fn
        self.download_dir = Path(download_dir)
        self.download_dir.mkdir(parents=True, exist_ok=True)
        self.clean_stale_temp_files()

        self.on_progress = on_progress
        self.on_complete = on_complete
        self.on_error = on_error
        self.enable_p2p = enable_p2p

        self._sessions: dict[str, DownloadSession] = {}
        self._lock = threading.Lock()

    def clean_stale_temp_files(self) -> None:
        """Remove leftover .part files from previous crashed sessions without disturbing active downloads."""
        try:
            for p in self.download_dir.iterdir():
                if p.is_file() and p.name.endswith(".part"):
                    # Check if file is currently locked by another active process on POSIX.
                    try:
                        import fcntl
                        try:
                            with open(p, "r+b") as test_f:
                                fcntl.flock(test_f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                                fcntl.flock(test_f, fcntl.LOCK_UN)
                        except (BlockingIOError, OSError):
                            # File is locked by an active process, do not delete!
                            continue
                    except ImportError:
                        # On Windows, an actively open handle rejects unlink() with PermissionError.
                        pass

                    try:
                        p.unlink(missing_ok=True)
                    except (PermissionError, OSError):
                        pass
        except Exception:
            pass

    def abort_all(self, reason: str = "Client disconnected") -> None:
        """Abort all active download sessions and delete their temporary .part files."""
        with self._lock:
            session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            self._abort_session(session_id, reason, notify_peer=False)

    def handle_incoming_message(self, header: dict[str, Any], payload: bytes = b"") -> None:
        """Route incoming FILE_INIT, FILE_CHUNK, FILE_END, and FILE_ERROR frames."""
        msg_type = header.get("type")
        if msg_type == "FILE_INIT":
            self._handle_file_init(header)
        elif msg_type == "FILE_CHUNK":
            self._handle_file_chunk(header, payload)
        elif msg_type == "FILE_END":
            self._handle_file_end(header)
        elif msg_type == "FILE_ERROR":
            self._handle_file_error(header)

    def _handle_file_init(self, header: dict[str, Any]) -> None:
        """Initialize a new temporary download session and acknowledge with FILE_ACCEPT."""
        session_id = header.get("session_id")
        if not is_valid_transfer_id(session_id):
            return

        raw_sender = header.get("sender", "unknown")
        sender = raw_sender if isinstance(raw_sender, str) else "unknown"
        raw_sha256 = header.get("sha256", "")
        sha256 = raw_sha256 if isinstance(raw_sha256, str) else ""

        try:
            filesize = parse_non_negative_int(header.get("filesize", 0), "filesize")
            total_chunks = parse_non_negative_int(header.get("total_chunks", 0), "total_chunks")
        except ValueError as error:
            try:
                self.send_frame(
                    {"type": "FILE_ERROR", "session_id": session_id, "reason": str(error)},
                    b"",
                )
            except Exception:
                pass
            return

        # Sanitize filename against directory traversal attacks (POSIX & Windows separators)
        safe_name = sanitize_filename(header.get("filename", "unnamed_file"))
        temp_filename = f".{session_id}_{safe_name}.part"
        temp_path = self.download_dir / temp_filename
        try:
            if not temp_path.resolve().is_relative_to(self.download_dir.resolve()):
                if self.on_error:
                    self.on_error(session_id, safe_name, "Unsafe temporary download path rejected.")
                return
        except Exception:
            return

        with self._lock:
            if session_id in self._sessions:
                self._abort_session_unlocked(session_id, "Duplicate session initialization", notify=False)

            try:
                file_handle = temp_path.open("wb")
                try:
                    import fcntl
                    fcntl.flock(file_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except Exception:
                    pass
                session = DownloadSession(
                    session_id=session_id,
                    sender=sender,
                    filename=safe_name,
                    filesize=filesize,
                    expected_sha256=sha256,
                    total_chunks=total_chunks,
                    temp_path=temp_path,
                    file_handle=file_handle,
                )
                self._sessions[session_id] = session
            except Exception as error:
                if self.on_error:
                    self.on_error(session_id, safe_name, f"Failed to create temp file: {error}")
                return

        # Finalize empty files immediately without awaiting chunks
        if filesize == 0:
            self._handle_file_end({"session_id": session_id})
            return

        # Notify sender that receiver is ready (with optional direct P2P listener)
        accept_header: dict[str, Any] = {"type": "FILE_ACCEPT", "session_id": session_id}
        if self.enable_p2p:
            p2p_info = self._start_p2p_listener(session)
            if p2p_info is not None:
                accept_header.update(p2p_info)

        try:
            self.send_frame(accept_header, b"")
        except Exception as error:
            self._abort_session(session_id, f"Failed to send FILE_ACCEPT: {error}")

    def _start_p2p_listener(self, session: DownloadSession) -> dict[str, Any] | None:
        """Bind a temporary ephemeral TCP listener for direct P2P chunk reception."""
        srv_sock: socket.socket | None = None
        try:
            srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv_sock.bind(("0.0.0.0", 0))
            srv_sock.listen(1)
            srv_sock.settimeout(12.0)
            port = int(srv_sock.getsockname()[1])
            token = secrets.token_hex(16)
            session.p2p_server_socket = srv_sock
            worker = threading.Thread(
                target=self._p2p_accept_worker,
                args=(session.session_id, srv_sock, token),
                name=f"p2p-recv-{session.session_id[:8]}",
                daemon=True,
            )
            worker.start()
            return {
                "p2p_hosts": get_local_p2p_hosts(),
                "p2p_port": port,
                "p2p_token": token,
            }
        except Exception:
            if srv_sock is not None:
                try:
                    srv_sock.close()
                except OSError:
                    pass
            session.p2p_server_socket = None
            return None

    def _p2p_accept_worker(
        self,
        session_id: str,
        srv_sock: socket.socket,
        expected_token: str,
    ) -> None:
        """Accept one authenticated direct P2P sender connection and feed chunks into FileReceiver."""
        conn: socket.socket | None = None
        try:
            conn, _addr = srv_sock.accept()
            conn.settimeout(15.0)
            hs = recv_p2p_json(conn)
            if hs.get("session_id") != session_id or not secrets.compare_digest(
                str(hs.get("p2p_token", "")), expected_token
            ):
                send_p2p_json(conn, {"ok": False, "reason": "Unauthorized P2P token"})
                return
            send_p2p_json(conn, {"ok": True})
            cipher = make_p2p_cipher(expected_token)

            while True:
                magic, chunk_index, payload = recv_p2p_binary_frame(conn, cipher=cipher)
                if magic == P2P_MAGIC_CHUNK:
                    self._handle_file_chunk(
                        {"session_id": session_id, "chunk_index": chunk_index},
                        payload,
                    )
                elif magic == P2P_MAGIC_END:
                    self._handle_file_end({"session_id": session_id})
                    break
                else:
                    raise ValueError(f"Unknown P2P frame magic: {magic!r}")
        except Exception:
            # If sender fell back to server relay or session aborted, exit quietly.
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
            try:
                srv_sock.close()
            except OSError:
                pass

    def _handle_file_chunk(self, header: dict[str, Any], chunk_bytes: bytes) -> None:
        """Validate sequence index, append bytes to the temp file, and notify progress."""
        session_id = header.get("session_id")
        chunk_index = header.get("chunk_index")

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return

            if (
                not isinstance(chunk_index, int)
                or isinstance(chunk_index, bool)
                or chunk_index != session.next_chunk_index
            ):
                self._abort_session_unlocked(
                    session_id,
                    f"Chunk sequence error: expected {session.next_chunk_index}, got {chunk_index}",
                    notify=True,
                )
                return

            try:
                session.file_handle.write(chunk_bytes)
                session.received_bytes += len(chunk_bytes)
                session.next_chunk_index += 1
            except Exception as error:
                self._abort_session_unlocked(session_id, f"Disk write error: {error}", notify=True)
                return

        if self.on_progress:
            self.on_progress(session.session_id, session.filename, session.received_bytes, session.filesize)

    def _handle_file_end(self, header: dict[str, Any]) -> None:
        """Close temp file, verify size & SHA-256 hash, and atomically promote to final filename."""
        session_id = header.get("session_id")

        with self._lock:
            session = self._sessions.pop(session_id, None)

        if not session:
            return

        try:
            session.file_handle.flush()
            session.file_handle.close()
        except Exception as error:
            session.temp_path.unlink(missing_ok=True)
            err_msg = f"Failed to close temp file: {error}"
            self._send_result(session.session_id, "FAILED", err_msg)
            if self.on_error:
                self.on_error(session.session_id, session.filename, err_msg)
            return

        # Verify integrity
        is_valid, err_msg = verify_file(session.temp_path, session.filesize, session.expected_sha256)

        if is_valid:
            final_path = self._resolve_final_path(session.filename)
            try:
                os.replace(session.temp_path, final_path)
                self._send_result(session.session_id, "OK", "")
                if self.on_complete:
                    self.on_complete(session.session_id, session.filename, str(final_path))
            except Exception as error:
                session.temp_path.unlink(missing_ok=True)
                err_msg = f"Failed to move file to destination: {error}"
                self._send_result(session.session_id, "FAILED", err_msg)
                if self.on_error:
                    self.on_error(session.session_id, session.filename, err_msg)
        else:
            session.temp_path.unlink(missing_ok=True)
            self._send_result(session.session_id, "FAILED", err_msg)
            if self.on_error:
                self.on_error(session.session_id, session.filename, err_msg)

    def _handle_file_error(self, header: dict[str, Any]) -> None:
        """Handle error/cancellation notification received from the peer or server."""
        session_id = header.get("session_id", "")
        reason = header.get("reason", "File transfer interrupted by peer.")
        self._abort_session(session_id, reason, notify_peer=False)

    def _abort_session(self, session_id: str, reason: str, notify_peer: bool = True) -> None:
        """Thread-safe public abort method."""
        with self._lock:
            self._abort_session_unlocked(session_id, reason, notify=notify_peer)

    def _abort_session_unlocked(self, session_id: str, reason: str, notify: bool = True) -> None:
        """Internal helper to clean up resources without re-acquiring the lock."""
        session = self._sessions.pop(session_id, None)
        if not session:
            return

        filename = session.filename
        if session.p2p_server_socket is not None:
            try:
                session.p2p_server_socket.close()
            except OSError:
                pass
            session.p2p_server_socket = None
        try:
            session.file_handle.close()
        except Exception:
            pass

        try:
            session.temp_path.unlink(missing_ok=True)
        except Exception:
            try:
                import time
                time.sleep(0.05)
                session.temp_path.unlink(missing_ok=True)
            except Exception:
                pass

        if notify:
            try:
                self.send_frame(
                    {"type": "FILE_ERROR", "session_id": session_id, "reason": reason},
                    b"",
                )
            except Exception:
                pass

        if self.on_error:
            self.on_error(session_id, filename, reason)

    def _send_result(self, session_id: str, status: str, reason: str) -> None:
        """Dispatch FILE_RESULT acknowledgment to sender."""
        try:
            self.send_frame(
                {"type": "FILE_RESULT", "session_id": session_id, "status": status, "reason": reason},
                b"",
            )
        except Exception:
            pass

    def _resolve_final_path(self, filename: str) -> Path:
        """Avoid overwriting existing files by appending incremental counters (e.g. file (1).txt)."""
        safe_name = sanitize_filename(filename)
        base_dir = self.download_dir.resolve()
        candidate = self.download_dir / safe_name
        if not candidate.resolve().is_relative_to(base_dir):
            candidate = self.download_dir / "unnamed_file"
        if not candidate.exists():
            return candidate

        stem, suffix = candidate.stem, candidate.suffix
        counter = 1
        while True:
            new_candidate = self.download_dir / f"{stem} ({counter}){suffix}"
            if not new_candidate.exists():
                return new_candidate
            counter += 1

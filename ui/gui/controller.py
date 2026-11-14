"""Thread-safe controller and Qt signal bridge between UI and core client."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from chat.client_chat import ClientChat
from common import message_types as mt
from infrastructure.client_connection import ClientSession
from transfer.sender import ClientFileTransferManager


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DISCONNECTING = "DISCONNECTING"


@dataclass(frozen=True)
class ConnectResultEvent:
    gen: int
    username: str
    server_host: str
    server_port: int | None


@dataclass(frozen=True)
class ErrorEvent:
    gen: int
    message: str
    code: str = ""


@dataclass(frozen=True)
class OnlineListEvent:
    gen: int
    users: tuple[str, ...]


@dataclass(frozen=True)
class ChatMessageEvent:
    gen: int
    sender: str
    text: str
    is_broadcast: bool


@dataclass(frozen=True)
class DeliveryStatusEvent:
    gen: int
    ok: bool
    message: str
    code: str | None
    delivered_count: int | None


@dataclass(frozen=True)
class TransferSnapshotEvent:
    gen: int
    event: str  # started | progress | completed | failed
    session_id: str
    direction: str  # upload | download
    peer: str
    filename: str
    current: int
    total: int
    saved_path: str = ""
    error: str = ""


class GuiSessionBridge(QObject):
    """Qt signal emitter; safe to call from any background thread."""

    state_changed = Signal(str)
    connected = Signal(object)
    connect_failed = Signal(object)
    online_list_received = Signal(object)
    message_received = Signal(object)
    delivery_status_received = Signal(object)
    transfer_event_received = Signal(object)
    server_error_received = Signal(object)
    disconnected = Signal(object)


class SessionController:
    """Owns ClientSession, ClientChat, ClientFileTransferManager, and worker pool."""

    PROGRESS_MIN_INTERVAL_SEC = 0.065  # ~15 Hz throttle per transfer

    def __init__(self, bridge: GuiSessionBridge | None = None) -> None:
        self.bridge = bridge or GuiSessionBridge()
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._session_gen = 0
        self._session: ClientSession | None = None
        self._chat: ClientChat | None = None
        self._ft_manager: ClientFileTransferManager | None = None
        self._progress_last_emit: dict[str, float] = {}
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gui-worker")

    @property
    def state(self) -> ConnectionState:
        with self._lock:
            return self._state

    @property
    def current_gen(self) -> int:
        with self._lock:
            return self._session_gen

    def _set_state(self, new_state: ConnectionState) -> None:
        with self._lock:
            self._state = new_state
        self.bridge.state_changed.emit(new_state.value)

    def connect(
        self,
        username: str,
        server_host: str | None = None,
        server_port: int | None = None,
    ) -> bool:
        """Start connection + login on a worker thread; rejects double-connect."""
        clean_user = username.strip()
        if not clean_user:
            self.bridge.connect_failed.emit(
                ErrorEvent(gen=self.current_gen, message="Username không được để trống.")
            )
            return False

        with self._lock:
            if self._state != ConnectionState.DISCONNECTED:
                return False
            self._state = ConnectionState.CONNECTING
            self._session_gen += 1
            gen = self._session_gen
            self._progress_last_emit.clear()

        self.bridge.state_changed.emit(ConnectionState.CONNECTING.value)
        self._executor.submit(self._connect_worker, gen, clean_user, server_host, server_port)
        return True

    def _connect_worker(
        self,
        gen: int,
        username: str,
        server_host: str | None,
        server_port: int | None,
    ) -> None:
        session: ClientSession | None = None
        try:
            session = ClientSession.connect(
                username=username,
                on_error=lambda msg: self._on_core_error(gen, msg),
                on_unhandled=lambda msg: None,
                server_host=server_host,
                server_port=server_port,
            )

            chat = ClientChat(
                send_message=session.send,
                on_message=lambda sender, text, is_bc: self.bridge.message_received.emit(
                    ChatMessageEvent(gen=gen, sender=sender, text=text, is_broadcast=is_bc)
                ),
                on_status=lambda ok, msg, code, cnt: self.bridge.delivery_status_received.emit(
                    DeliveryStatusEvent(
                        gen=gen, ok=ok, message=msg, code=code, delivered_count=cnt
                    )
                ),
            )
            chat.register_handlers(session.register_handler)

            ft_manager = ClientFileTransferManager(
                username=session.username,
                send_fn=session.send,
                print_fn=lambda _text: None,
                status_update_fn=None,
                on_event=lambda payload: self._on_transfer_event(gen, payload),
            )
            ft_manager.register_handlers(session.register_handler)

            session.register_handler(
                mt.ONLINE_LIST,
                lambda msg: self._on_online_list(gen, msg),
            )
            session.register_handler(
                mt.ERROR,
                lambda msg: self.bridge.server_error_received.emit(
                    ErrorEvent(
                        gen=gen,
                        code=str(msg.get("code", "UNKNOWN")),
                        message=str(msg.get("message", "Server trả lỗi.")),
                    )
                ),
            )
            session.register_handler(
                mt.BYE,
                lambda msg: self._on_server_bye(gen, str(msg.get("message", "Đã kết thúc phiên."))),
            )

            with self._lock:
                if gen != self._session_gen or self._state != ConnectionState.CONNECTING:
                    # Stale or aborted before activation
                    session.close()
                    return
                self._session = session
                self._chat = chat
                self._ft_manager = ft_manager
                self._state = ConnectionState.CONNECTED

            session.start()
            self.bridge.state_changed.emit(ConnectionState.CONNECTED.value)
            self.bridge.connected.emit(
                ConnectResultEvent(
                    gen=gen,
                    username=session.username,
                    server_host=session.server_host,
                    server_port=server_port,
                )
            )
        except Exception as error:
            if session is not None:
                try:
                    session.close()
                except Exception:
                    pass
            with self._lock:
                if gen == self._session_gen:
                    self._session = None
                    self._chat = None
                    self._ft_manager = None
                    self._state = ConnectionState.DISCONNECTED
            self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
            self.bridge.connect_failed.emit(ErrorEvent(gen=gen, message=str(error)))

    def _on_online_list(self, gen: int, message: dict[str, Any]) -> None:
        users = message.get("users")
        if not isinstance(users, list) or any(not isinstance(u, str) for u in users):
            self.bridge.server_error_received.emit(
                ErrorEvent(gen=gen, code="INVALID_ONLINE_LIST", message="ONLINE_LIST không hợp lệ.")
            )
            return
        self.bridge.online_list_received.emit(OnlineListEvent(gen=gen, users=tuple(users)))

    def _on_core_error(self, gen: int, message: str) -> None:
        with self._lock:
            if gen != self._session_gen or self._state != ConnectionState.CONNECTED:
                return
            session = self._session

        if session is None:
            return

        if session.is_closed:
            self._finalize_unexpected_disconnect(gen, message)
            return

        # ClientSession._receive_loop calls _on_error right before setting _stop_event and returning.
        # Probe on a worker thread whether the receiver thread actually exited (fatal socket drop)
        # vs. stayed alive (non-fatal handler/dispatch or heartbeat error), without parsing strings.
        if threading.current_thread() is session._receiver_thread:
            self._executor.submit(self._probe_receiver_exit, gen, session, message)
        else:
            self.bridge.server_error_received.emit(ErrorEvent(gen=gen, message=message))

    def _probe_receiver_exit(self, gen: int, session: ClientSession, message: str) -> None:
        receiver = session._receiver_thread
        if receiver is not None:
            receiver.join(timeout=0.2)

        if session.is_closed or (receiver is not None and not receiver.is_alive()):
            self._finalize_unexpected_disconnect(gen, message)
        else:
            self.bridge.server_error_received.emit(ErrorEvent(gen=gen, message=message))

    def _finalize_unexpected_disconnect(self, gen: int, message: str) -> None:
        with self._lock:
            if gen != self._session_gen or self._state != ConnectionState.CONNECTED:
                return
            self._state = ConnectionState.DISCONNECTED
            ft = self._ft_manager
            self._ft_manager = None
            self._session = None
            self._chat = None

        if ft is not None:
            try:
                ft.cleanup("Connection lost")
            except Exception:
                pass
        self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
        self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=message))

    def _on_server_bye(self, gen: int, reason: str) -> None:
        with self._lock:
            if gen != self._session_gen:
                return
            ft = self._ft_manager
            self._ft_manager = None
            self._session = None
            self._chat = None
            self._state = ConnectionState.DISCONNECTED
        if ft is not None:
            try:
                ft.cleanup(reason)
            except Exception:
                pass
        self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
        self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=reason))

    def _on_transfer_event(self, gen: int, payload: dict[str, Any]) -> None:
        event_name = str(payload.get("event", ""))
        session_id = str(payload.get("session_id", ""))
        current = int(payload.get("current", 0))
        total = int(payload.get("total", 0))

        if event_name == "progress" and session_id:
            now = time.monotonic()
            with self._lock:
                last = self._progress_last_emit.get(session_id, 0.0)
                if current < total and (now - last) < self.PROGRESS_MIN_INTERVAL_SEC:
                    return
                self._progress_last_emit[session_id] = now

        if event_name in ("completed", "failed") and session_id:
            with self._lock:
                self._progress_last_emit.pop(session_id, None)

        self.bridge.transfer_event_received.emit(
            TransferSnapshotEvent(
                gen=gen,
                event=event_name,
                session_id=session_id,
                direction=str(payload.get("direction", "upload")),
                peer=str(payload.get("peer", "")),
                filename=str(payload.get("filename", "unnamed_file")),
                current=current,
                total=total,
                saved_path=str(payload.get("saved_path", "")),
                error=str(payload.get("error", "")),
            )
        )

    def refresh_online_users(self) -> None:
        with self._lock:
            gen = self._session_gen
            session = self._session
            if self._state != ConnectionState.CONNECTED or session is None:
                return
        self._executor.submit(self._send_safe, gen, session, {"type": mt.LIST_ONLINE})

    def send_broadcast(self, text: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._executor.submit(self._send_chat_safe, gen, chat.send_broadcast, text)

    def send_direct(self, recipient: str, text: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._executor.submit(self._send_chat_safe, gen, chat.send_direct, recipient, text)

    def send_file(self, recipient: str, file_path: str | Path) -> None:
        with self._lock:
            gen = self._session_gen
            ft_manager = self._ft_manager
            if self._state != ConnectionState.CONNECTED or ft_manager is None:
                return
        self._executor.submit(self._send_file_worker, gen, ft_manager, recipient, Path(file_path))

    def send_file_broadcast(self, recipients: list[str], file_path: str | Path) -> None:
        with self._lock:
            gen = self._session_gen
            ft_manager = self._ft_manager
            session = self._session
            if self._state != ConnectionState.CONNECTED or ft_manager is None or session is None:
                return
            self_user = session.username
        targets = [u for u in recipients if u and u != self_user]
        if not targets:
            self.bridge.server_error_received.emit(
                ErrorEvent(
                    gen=gen,
                    code="NO_ONLINE_RECIPIENTS",
                    message="Không có người dùng nào khác đang online để gửi file.",
                )
            )
            return
        self._executor.submit(
            self._send_file_broadcast_worker, gen, ft_manager, targets, Path(file_path)
        )

    def _send_file_broadcast_worker(
        self,
        gen: int,
        ft_manager: ClientFileTransferManager,
        recipients: list[str],
        path: Path,
    ) -> None:
        if not path.is_file():
            self.bridge.server_error_received.emit(
                ErrorEvent(gen=gen, code="FILE_SEND_ERROR", message=f"Không tìm thấy file: {path}")
            )
            return
        for peer in recipients:
            try:
                ft_manager.sender.start_transfer(peer, path)
            except Exception as error:
                self.bridge.server_error_received.emit(
                    ErrorEvent(
                        gen=gen,
                        code="FILE_SEND_ERROR",
                        message=f"Lỗi gửi file tới {peer}: {error}",
                    )
                )

    def _send_safe(self, gen: int, session: ClientSession, message: dict[str, Any]) -> None:
        try:
            session.send(message)
        except Exception as error:
            self.bridge.server_error_received.emit(ErrorEvent(gen=gen, message=str(error)))

    def _send_chat_safe(self, gen: int, fn: Any, *args: Any) -> None:
        try:
            fn(*args)
        except Exception as error:
            self.bridge.server_error_received.emit(ErrorEvent(gen=gen, message=str(error)))

    def _send_file_worker(
        self,
        gen: int,
        ft_manager: ClientFileTransferManager,
        recipient: str,
        path: Path,
    ) -> None:
        try:
            ft_manager.sender.start_transfer(recipient, path)
        except Exception as error:
            self.bridge.server_error_received.emit(
                ErrorEvent(gen=gen, code="FILE_SEND_ERROR", message=str(error))
            )

    def disconnect(self, reason: str = "Người dùng đã ngắt kết nối.") -> None:
        with self._lock:
            if self._state != ConnectionState.CONNECTED:
                return
            self._state = ConnectionState.DISCONNECTING
            gen = self._session_gen
            session = self._session
            ft_manager = self._ft_manager
            self._session = None
            self._chat = None
            self._ft_manager = None

        self.bridge.state_changed.emit(ConnectionState.DISCONNECTING.value)
        self._executor.submit(self._disconnect_worker, gen, session, ft_manager, reason)

    def _disconnect_worker(
        self,
        gen: int,
        session: ClientSession | None,
        ft_manager: ClientFileTransferManager | None,
        reason: str,
    ) -> None:
        try:
            if ft_manager is not None:
                ft_manager.cleanup("Client disconnected")
            if session is not None:
                try:
                    session.logout(timeout=2.0)
                except Exception:
                    pass
                session.close()
        finally:
            with self._lock:
                if gen == self._session_gen:
                    self._state = ConnectionState.DISCONNECTED
            self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
            self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=reason))

    def shutdown_sync(self, timeout: float = 2.5) -> None:
        """Bounded cleanup for application exit (closeEvent)."""
        with self._lock:
            self._session_gen += 1
            session = self._session
            ft_manager = self._ft_manager
            self._session = None
            self._chat = None
            self._ft_manager = None
            self._state = ConnectionState.DISCONNECTED

        done = threading.Event()

        def _cleanup() -> None:
            try:
                if ft_manager is not None:
                    ft_manager.cleanup("Application closing")
                if session is not None:
                    try:
                        session.logout(timeout=1.5)
                    except Exception:
                        pass
                    session.close()
            finally:
                done.set()

        t = threading.Thread(target=_cleanup, name="gui-shutdown", daemon=True)
        t.start()
        done.wait(timeout=timeout)
        self._executor.shutdown(wait=False, cancel_futures=True)

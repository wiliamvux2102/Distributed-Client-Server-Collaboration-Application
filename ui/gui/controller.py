"""Thread-safe controller and Qt signal bridge between UI and core client."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import threading
import time
from typing import Any
import uuid

from PySide6.QtCore import QObject, Signal

from chat.client_chat import ClientChat
from chat.p2p_chat import P2PChatManager
from common import message_types as mt
from infrastructure.client_connection import ClientSession
from transfer.sender import ClientFileTransferManager

BROADCAST_CONV_ID = "#all"


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
    tls_version: str = "TLS"
    tls_cipher: str = ""


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
class HistorySyncEvent:
    gen: int
    messages: tuple[dict[str, Any], ...]
    transfers: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RoomMessageEvent:
    gen: int
    room_id: str
    sender: str
    text: str


@dataclass(frozen=True)
class RoomListEvent:
    gen: int
    rooms: tuple[dict[str, Any], ...]


@dataclass(frozen=True)
class RoomStatusEvent:
    gen: int
    ok: bool
    action: str
    room_id: str
    message: str
    members: tuple[str, ...]


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
    is_broadcast: bool = False
    batch_id: str = ""
    batch_total: int = 0
    room_id: str = ""


class GuiSessionBridge(QObject):
    """Qt signal emitter; safe to call from any background thread."""

    state_changed = Signal(str)
    connected = Signal(object)
    connect_failed = Signal(object)
    online_list_received = Signal(object)
    message_received = Signal(object)
    delivery_status_received = Signal(object)
    history_synced = Signal(object)
    room_message_received = Signal(object)
    room_list_received = Signal(object)
    room_status_received = Signal(object)
    transfer_event_received = Signal(object)
    p2p_mode_changed = Signal(bool)
    server_error_received = Signal(object)
    disconnected = Signal(object)


class SessionController:
    """Owns ClientSession, ClientChat, ClientFileTransferManager, and worker pool."""

    PROGRESS_MIN_INTERVAL_SEC = 0.065  # ~15 Hz throttle per transfer

    def __init__(self, bridge: GuiSessionBridge | None = None) -> None:
        self.bridge = bridge or GuiSessionBridge()
        self._lock = threading.Lock()
        self._state = ConnectionState.DISCONNECTED
        self._shutting_down = False
        self._session_gen = 0
        self._session: ClientSession | None = None
        self._chat: ClientChat | None = None
        self._p2p_chat: P2PChatManager | None = None
        self._ft_manager: ClientFileTransferManager | None = None
        self._p2p_enabled = False
        self._progress_last_emit: dict[str, float] = {}
        self._broadcast_batches: dict[str, tuple[str, int]] = {}
        self._batch_tls = threading.local()
        self._executor = ThreadPoolExecutor(max_workers=3, thread_name_prefix="gui-worker")

    def _submit_worker(self, fn: Callable[..., Any], *args: Any) -> bool:
        """Schedule a background task unless the controller is shutting down."""
        with self._lock:
            if self._shutting_down:
                return False
            try:
                self._executor.submit(fn, *args)
                return True
            except RuntimeError:
                return False

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        if isinstance(value, bool):
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

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
            if self._shutting_down or self._state != ConnectionState.DISCONNECTED:
                return False
            self._state = ConnectionState.CONNECTING
            self._session_gen += 1
            gen = self._session_gen
            self._progress_last_emit.clear()
            self._broadcast_batches.clear()

        self.bridge.state_changed.emit(ConnectionState.CONNECTING.value)
        if not self._submit_worker(self._connect_worker, gen, clean_user, server_host, server_port):
            with self._lock:
                if gen == self._session_gen and self._state == ConnectionState.CONNECTING:
                    self._state = ConnectionState.DISCONNECTED
            self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
            return False
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
                on_history=lambda messages, transfers: self.bridge.history_synced.emit(
                    HistorySyncEvent(
                        gen=gen,
                        messages=tuple(messages),
                        transfers=tuple(transfers),
                    )
                ),
                on_room_message=lambda room_id, sender, text: self.bridge.room_message_received.emit(
                    RoomMessageEvent(gen=gen, room_id=room_id, sender=sender, text=text)
                ),
                on_room_list=lambda rooms: self.bridge.room_list_received.emit(
                    RoomListEvent(gen=gen, rooms=tuple(rooms))
                ),
                on_room_status=lambda ok, action, room_id, msg, members: self.bridge.room_status_received.emit(
                    RoomStatusEvent(
                        gen=gen,
                        ok=ok,
                        action=action,
                        room_id=room_id,
                        message=msg,
                        members=tuple(members),
                    )
                ),
            )
            chat.register_handlers(session.register_handler)

            with self._lock:
                p2p_enabled = self._p2p_enabled

            p2p_chat = P2PChatManager(
                username=session.username,
                send_signal_fn=session.send,
                on_message=lambda sender, text: self.bridge.message_received.emit(
                    ChatMessageEvent(
                        gen=gen,
                        sender=sender,
                        text=text,
                        is_broadcast=False,
                    )
                ),
                enabled=p2p_enabled,
            )
            p2p_chat.register_handlers(session.register_handler)

            ft_manager = ClientFileTransferManager(
                username=session.username,
                send_fn=session.send,
                print_fn=lambda _text: None,
                status_update_fn=None,
                on_event=lambda payload: self._on_transfer_event(gen, payload),
                enable_p2p=p2p_enabled,
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
                self._p2p_chat = p2p_chat
                self._ft_manager = ft_manager
                self._state = ConnectionState.CONNECTED

            resolved_port, tls_version, tls_cipher = session.get_tls_details()
            if server_port is not None:
                resolved_port = server_port

            session.start()
            self.bridge.state_changed.emit(ConnectionState.CONNECTED.value)
            self.bridge.connected.emit(
                ConnectResultEvent(
                    gen=gen,
                    username=session.username,
                    server_host=session.server_host,
                    server_port=resolved_port,
                    tls_version=tls_version,
                    tls_cipher=tls_cipher,
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
        if threading.current_thread() is session.receiver_thread:
            self._submit_worker(self._probe_receiver_exit, gen, session, message)
        else:
            self.bridge.server_error_received.emit(ErrorEvent(gen=gen, message=message))

    def _probe_receiver_exit(self, gen: int, session: ClientSession, message: str) -> None:
        receiver = session.receiver_thread
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
            p2p_chat = self._p2p_chat
            session = self._session
            self._ft_manager = None
            self._p2p_chat = None
            self._session = None
            self._chat = None
            self._progress_last_emit.clear()
            self._broadcast_batches.clear()

        if p2p_chat is not None:
            try:
                p2p_chat.close_all()
            except Exception:
                pass
        if ft is not None:
            try:
                ft.cleanup("Connection lost")
            except Exception:
                pass
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
        self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=message))

    def _on_server_bye(self, gen: int, reason: str) -> None:
        with self._lock:
            if gen != self._session_gen:
                return
            ft = self._ft_manager
            session = self._session
            self._ft_manager = None
            self._session = None
            self._chat = None
            self._state = ConnectionState.DISCONNECTED
            self._progress_last_emit.clear()
            self._broadcast_batches.clear()
        if ft is not None:
            try:
                ft.cleanup(reason)
            except Exception:
                pass
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
        self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
        self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=reason))

    def _on_transfer_event(self, gen: int, payload: dict[str, Any]) -> None:
        event_name = str(payload.get("event", ""))
        session_id = str(payload.get("session_id", ""))
        direction = str(payload.get("direction", "upload"))
        current = self._coerce_int(payload.get("current"), 0)
        total = self._coerce_int(payload.get("total"), 0)
        is_broadcast = bool(payload.get("is_broadcast", False))

        with self._lock:
            if event_name == "started" and direction == "upload" and session_id:
                tls_batch_id = getattr(self._batch_tls, "batch_id", "")
                tls_batch_total = getattr(self._batch_tls, "batch_total", 0)
                tls_room_id = getattr(self._batch_tls, "room_id", "")
                if tls_batch_id:
                    self._broadcast_batches[session_id] = (tls_batch_id, tls_batch_total, tls_room_id)

            batch_entry = self._broadcast_batches.get(session_id)
            if batch_entry:
                batch_id = batch_entry[0]
                batch_total = batch_entry[1]
                batch_room_id = batch_entry[2] if len(batch_entry) > 2 else ""
            else:
                batch_id, batch_total, batch_room_id = "", 0, ""

            payload_room_id = str(payload.get("room_id", "") or "")
            room_id = payload_room_id or batch_room_id
            if batch_id and not room_id:
                is_broadcast = True

            if event_name == "progress" and session_id:
                now = time.monotonic()
                last = self._progress_last_emit.get(session_id, 0.0)
                if current < total and (now - last) < self.PROGRESS_MIN_INTERVAL_SEC:
                    return
                self._progress_last_emit[session_id] = now

            if event_name in ("completed", "failed") and session_id:
                self._progress_last_emit.pop(session_id, None)
                self._broadcast_batches.pop(session_id, None)

        self.bridge.transfer_event_received.emit(
            TransferSnapshotEvent(
                gen=gen,
                event=event_name,
                session_id=session_id,
                direction=direction,
                peer=str(payload.get("peer", "")),
                filename=str(payload.get("filename", "unnamed_file")),
                current=current,
                total=total,
                saved_path=str(payload.get("saved_path", "")),
                error=str(payload.get("error", "")),
                is_broadcast=is_broadcast,
                batch_id=batch_id,
                batch_total=batch_total,
                room_id=room_id,
            )
        )

    def refresh_online_users(self) -> None:
        with self._lock:
            gen = self._session_gen
            session = self._session
            if self._state != ConnectionState.CONNECTED or session is None:
                return
        self._submit_worker(self._send_safe, gen, session, {"type": mt.LIST_ONLINE})

    def request_history(self, limit: int = 100) -> None:
        with self._lock:
            gen = self._session_gen
            session = self._session
            if self._state != ConnectionState.CONNECTED or session is None:
                return
        self._submit_worker(
            self._send_safe,
            gen,
            session,
            {"type": mt.HISTORY_REQUEST, "limit": int(limit)},
        )

    def send_broadcast(self, text: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.send_broadcast, text)

    def set_p2p_mode(self, enabled: bool) -> None:
        """Switch between default Client-Server Relay mode and optional P2P Direct mode."""
        with self._lock:
            self._p2p_enabled = bool(enabled)
            ft = self._ft_manager
            p2p_chat = self._p2p_chat
        if ft is not None:
            ft.set_p2p_enabled(bool(enabled))
        if p2p_chat is not None:
            p2p_chat.set_enabled(bool(enabled))

    @property
    def p2p_enabled(self) -> bool:
        with self._lock:
            return self._p2p_enabled

    def send_direct(self, recipient: str, text: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            p2p_chat = self._p2p_chat
            p2p_enabled = self._p2p_enabled
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(
            self._send_direct_worker,
            gen,
            chat,
            p2p_chat,
            p2p_enabled,
            recipient,
            text,
        )

    def _send_direct_worker(
        self,
        gen: int,
        chat: ClientChat,
        p2p_chat: P2PChatManager | None,
        p2p_enabled: bool,
        recipient: str,
        text: str,
    ) -> None:
        if p2p_enabled and p2p_chat is not None:
            try:
                if p2p_chat.send_direct(recipient, text):
                    self.bridge.delivery_status_received.emit(
                        DeliveryStatusEvent(
                            gen=gen,
                            ok=True,
                            message=f"Đã gửi trực tiếp (P2P AES-256-GCM) tới {recipient}.",
                            code=None,
                            delivered_count=None,
                        )
                    )
                    return
            except Exception:
                pass
            self.set_p2p_mode(False)
            self.bridge.p2p_mode_changed.emit(False)
        self._send_chat_safe(gen, chat.send_direct, recipient, text)

    def create_room(self, room_id: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.create_room, room_id)

    def join_room(self, room_id: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.join_room, room_id)

    def leave_room(self, room_id: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.leave_room, room_id)

    def refresh_rooms(self) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.list_rooms)

    def send_room(self, room_id: str, text: str) -> None:
        with self._lock:
            gen = self._session_gen
            chat = self._chat
            if self._state != ConnectionState.CONNECTED or chat is None:
                return
        self._submit_worker(self._send_chat_safe, gen, chat.send_room, room_id, text)

    def send_file(self, recipient: str, file_path: str | Path) -> None:
        with self._lock:
            gen = self._session_gen
            ft_manager = self._ft_manager
            if self._state != ConnectionState.CONNECTED or ft_manager is None:
                return
        self._submit_worker(self._send_file_worker, gen, ft_manager, recipient, Path(file_path))

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
        self._submit_worker(
            self._send_file_broadcast_worker, gen, ft_manager, targets, Path(file_path), BROADCAST_CONV_ID
        )

    def send_file_room(self, room_id: str, recipients: list[str], file_path: str | Path) -> None:
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
                    code="NO_ONLINE_ROOM_MEMBERS",
                    message=f"Không có thành viên nào khác trong phòng {room_id} đang online để gửi file.",
                )
            )
            return
        self._submit_worker(
            self._send_file_broadcast_worker, gen, ft_manager, targets, Path(file_path), room_id
        )

    def _send_file_broadcast_worker(
        self,
        gen: int,
        ft_manager: ClientFileTransferManager,
        recipients: list[str],
        path: Path,
        conv_id: str = BROADCAST_CONV_ID,
    ) -> None:
        if not path.is_file():
            self.bridge.server_error_received.emit(
                ErrorEvent(gen=gen, code="FILE_SEND_ERROR", message=f"Không tìm thấy file: {path}")
            )
            return
        file_size = path.stat().st_size
        batch_id = uuid.uuid4().hex
        batch_total = len(recipients)
        is_room = conv_id != BROADCAST_CONV_ID and conv_id.startswith("#")
        room_id = conv_id if is_room else ""
        self._batch_tls.batch_id = batch_id
        self._batch_tls.batch_total = batch_total
        self._batch_tls.room_id = room_id
        try:
            for idx, peer in enumerate(recipients):
                try:
                    ft_manager.sender.start_transfer(
                        peer, path, is_broadcast=(not is_room), room_id=room_id
                    )
                except Exception as error:
                    self.bridge.server_error_received.emit(
                        ErrorEvent(
                            gen=gen,
                            code="FILE_SEND_ERROR",
                            message=f"Lỗi gửi file tới {peer}: {error}",
                        )
                    )
                    self.bridge.transfer_event_received.emit(
                        TransferSnapshotEvent(
                            gen=gen,
                            event="failed",
                            session_id=f"{batch_id}-err-{idx}",
                            direction="upload",
                            peer=peer,
                            filename=path.name,
                            current=0,
                            total=file_size,
                            error=str(error),
                            is_broadcast=(not is_room),
                            batch_id=batch_id,
                            batch_total=batch_total,
                            room_id=room_id,
                        )
                    )
        finally:
            self._batch_tls.batch_id = ""
            self._batch_tls.batch_total = 0
            self._batch_tls.room_id = ""

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
            if self._shutting_down or self._state != ConnectionState.CONNECTED:
                return
            self._state = ConnectionState.DISCONNECTING
            gen = self._session_gen
            session = self._session
            ft_manager = self._ft_manager
            self._session = None
            self._chat = None
            self._ft_manager = None

        self.bridge.state_changed.emit(ConnectionState.DISCONNECTING.value)
        if not self._submit_worker(self._disconnect_worker, gen, session, ft_manager, reason):
            threading.Thread(
                target=self._disconnect_worker,
                args=(gen, session, ft_manager, reason),
                name="gui-disconnect-fallback",
                daemon=True,
            ).start()

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
                    self._progress_last_emit.clear()
                    self._broadcast_batches.clear()
            self.bridge.state_changed.emit(ConnectionState.DISCONNECTED.value)
            self.bridge.disconnected.emit(ErrorEvent(gen=gen, message=reason))

    def shutdown_sync(self, timeout: float = 2.5) -> None:
        """Bounded cleanup for application exit (closeEvent)."""
        with self._lock:
            self._shutting_down = True
            self._session_gen += 1
            session = self._session
            ft_manager = self._ft_manager
            p2p_chat = self._p2p_chat
            self._session = None
            self._chat = None
            self._p2p_chat = None
            self._ft_manager = None
            self._state = ConnectionState.DISCONNECTED
            self._progress_last_emit.clear()
            self._broadcast_batches.clear()

        done = threading.Event()

        def _cleanup() -> None:
            try:
                if p2p_chat is not None:
                    p2p_chat.close_all()
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
        with self._lock:
            self._executor.shutdown(wait=False, cancel_futures=True)

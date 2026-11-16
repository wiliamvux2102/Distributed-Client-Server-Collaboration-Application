"""Automated unit and offscreen widget tests for the PySide6 GUI."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from transfer.sender import ClientFileTransferManager
from ui.gui.controller import (
    ChatMessageEvent,
    ConnectionState,
    ConnectResultEvent,
    OnlineListEvent,
    SessionController,
)
from ui.gui.main_window import MainWindow
from ui.gui.models import (
    BROADCAST_CONV_ID,
    MAX_MESSAGES_PER_CONV,
    ChatSessionStore,
    MessageListModel,
    MessageRecord,
)


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class GuiStoreAndModelTests(unittest.TestCase):
    def test_store_routes_broadcast_and_dm_with_unread(self) -> None:
        store = ChatSessionStore()
        self.assertEqual(store.active_conv_id, BROADCAST_CONV_ID)

        # Incoming DM while viewing #all increments unread for bob
        conv_id, _ = store.append_incoming("bob", "Chào bạn!", is_broadcast=False)
        self.assertEqual(conv_id, "bob")
        self.assertEqual(store.conversations["bob"].unread_count, 1)

        # Switching to bob resets unread
        store.set_active_conversation("bob")
        self.assertEqual(store.conversations["bob"].unread_count, 0)

        # Incoming broadcast while viewing bob increments #all unread
        conv_id2, _ = store.append_incoming("alice", "Tin chung", is_broadcast=True)
        self.assertEqual(conv_id2, BROADCAST_CONV_ID)
        self.assertEqual(store.conversations[BROADCAST_CONV_ID].unread_count, 1)

    def test_upload_100_percent_waits_for_verification_before_completed(self) -> None:
        store = ChatSessionStore()
        rec = store.apply_transfer_event(
            event="started",
            session_id="s1",
            direction="upload",
            peer="bob",
            filename="report.pdf",
            current=0,
            total=1024,
        )
        self.assertEqual(rec.state, "INIT_SENT")

        rec = store.apply_transfer_event(
            event="progress",
            session_id="s1",
            direction="upload",
            peer="bob",
            filename="report.pdf",
            current=1024,
            total=1024,
        )
        self.assertEqual(rec.state, "AWAITING_VERIFICATION")

        rec = store.apply_transfer_event(
            event="completed",
            session_id="s1",
            direction="upload",
            peer="bob",
            filename="report.pdf",
            current=1024,
            total=1024,
        )
        self.assertEqual(rec.state, "COMPLETED")

    def test_message_list_model_enforces_memory_cap(self) -> None:
        _get_app()
        model = MessageListModel()
        for i in range(MAX_MESSAGES_PER_CONV + 25):
            model.append_message(
                MessageRecord(
                    sender="alice",
                    text=f"msg-{i}",
                    is_outgoing=False,
                    is_broadcast=True,
                    timestamp_str="10:00",
                )
            )
        self.assertEqual(model.rowCount(), MAX_MESSAGES_PER_CONV)
        first_idx = model.index(0, 0)
        self.assertEqual(model.data(first_idx), "msg-25")


class GuiControllerAndWindowTests(unittest.TestCase):
    def test_double_connect_rejected_while_connecting(self) -> None:
        _get_app()
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTING
        self.assertFalse(ctrl.connect("alice"))
        ctrl.shutdown_sync(timeout=0.5)

    def test_progress_throttling_preserves_100_percent_and_completion(self) -> None:
        _get_app()
        ctrl = SessionController()
        received: list[tuple[str, int]] = []
        ctrl.bridge.transfer_event_received.connect(
            lambda ev: received.append((ev.event, ev.current))
        )

        # Rapid progress updates
        ctrl._on_transfer_event(1, {"event": "progress", "session_id": "x", "current": 10, "total": 100})
        ctrl._on_transfer_event(1, {"event": "progress", "session_id": "x", "current": 20, "total": 100})
        ctrl._on_transfer_event(1, {"event": "progress", "session_id": "x", "current": 100, "total": 100})
        ctrl._on_transfer_event(1, {"event": "completed", "session_id": "x", "current": 100, "total": 100})
        QApplication.processEvents()

        self.assertEqual(received[0], ("progress", 10))
        self.assertIn(("progress", 100), received)
        self.assertEqual(received[-1], ("completed", 100))
        ctrl.shutdown_sync(timeout=0.5)

    def test_stale_session_gen_events_ignored_by_window(self) -> None:
        _get_app()
        win = MainWindow()
        win.controller._session_gen = 2
        win._on_connected(ConnectResultEvent(gen=2, username="alice", server_host="127.0.0.1", server_port=5000))
        self.assertEqual(win._active_gen, 2)

        # Stale gen=1 message and online list must be ignored
        win._on_message_received(ChatMessageEvent(gen=1, sender="ghost", text="stale", is_broadcast=True))
        win._on_online_list(OnlineListEvent(gen=1, users=("ghost",)))
        self.assertEqual(win.message_model.rowCount(), 0)
        self.assertEqual(win.store.online_users, [])
        win.close()

    def test_transfer_manager_optional_on_event_preserves_cli(self) -> None:
        events: list[dict] = []
        printed: list[str] = []
        mgr = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: None,
            print_fn=printed.append,
            on_event=events.append,
        )
        mgr.handle_incoming({
            "type": "FILE_INIT",
            "session_id": "sid-1",
            "sender": "bob",
            "filename": "hello.txt",
            "filesize": 12,
            "total_chunks": 1,
            "sha256": "0" * 64,
        })
        self.assertTrue(any(e["event"] == "started" and e["session_id"] == "sid-1" for e in events))
        self.assertIsNotNone(mgr.get_active_status())
        mgr.cleanup("test done")

    def test_unexpected_receiver_exit_disconnects_and_cleans_up_once(self) -> None:
        _get_app()
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 5

        session = Mock()
        session.is_closed = False
        ft_mgr = Mock()
        ctrl._session = session
        ctrl._ft_manager = ft_mgr

        disconnected_events: list[str] = []
        server_errors: list[str] = []
        ctrl.bridge.disconnected.connect(lambda ev: disconnected_events.append(ev.message))
        ctrl.bridge.server_error_received.connect(lambda ev: server_errors.append(ev.message))

        def _fake_receiver_exit() -> None:
            session._receiver_thread = threading.current_thread()
            ctrl._on_core_error(5, "Kết nối kết thúc: EOF")
            session.is_closed = True

        t = threading.Thread(target=_fake_receiver_exit, name="client-receiver", daemon=True)
        t.start()
        t.join(timeout=2.0)

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not disconnected_events:
            QApplication.processEvents()
            time.sleep(0.01)

        self.assertEqual(ctrl.state, ConnectionState.DISCONNECTED)
        self.assertEqual(len(disconnected_events), 1)
        self.assertEqual(server_errors, [])
        ft_mgr.cleanup.assert_called_once_with("Connection lost")

        # Duplicate call for same gen must not re-cleanup
        ctrl._on_core_error(5, "duplicate")
        ft_mgr.cleanup.assert_called_once()
        ctrl.shutdown_sync(timeout=0.5)

    def test_non_fatal_handler_error_on_receiver_does_not_disconnect(self) -> None:
        _get_app()
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 7

        stop_alive = threading.Event()
        session = Mock()
        session.is_closed = False
        ft_mgr = Mock()
        ctrl._session = session
        ctrl._ft_manager = ft_mgr

        disconnected_events: list[str] = []
        server_errors: list[str] = []
        ctrl.bridge.disconnected.connect(lambda ev: disconnected_events.append(ev.message))
        ctrl.bridge.server_error_received.connect(lambda ev: server_errors.append(ev.message))

        def _fake_receiver_alive() -> None:
            session._receiver_thread = threading.current_thread()
            ctrl._on_core_error(7, "Lỗi xử lý bản tin DIRECT_DELIVERY: boom")
            stop_alive.wait(timeout=1.0)

        t = threading.Thread(target=_fake_receiver_alive, name="client-receiver", daemon=True)
        t.start()

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not server_errors:
            QApplication.processEvents()
            time.sleep(0.01)

        stop_alive.set()
        t.join(timeout=1.0)

        self.assertEqual(ctrl.state, ConnectionState.CONNECTED)
        self.assertEqual(disconnected_events, [])
        self.assertEqual(len(server_errors), 1)
        ft_mgr.cleanup.assert_not_called()
        ctrl.shutdown_sync(timeout=0.5)

    def test_send_file_broadcast_fans_out_to_all_other_peers(self) -> None:
        _get_app()
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 9
        session = Mock()
        session.username = "alice"
        ft_mgr = Mock()
        ctrl._session = session
        ctrl._ft_manager = ft_mgr

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"broadcast file data")
            tmp_path = Path(tf.name)

        try:
            ctrl.send_file_broadcast(["alice", "bob", "charlie"], tmp_path)
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and ft_mgr.sender.start_transfer.call_count < 2:
                time.sleep(0.01)

            calls = [c.args for c in ft_mgr.sender.start_transfer.call_args_list]
            self.assertEqual(calls, [("bob", tmp_path), ("charlie", tmp_path)])
        finally:
            tmp_path.unlink(missing_ok=True)
            ctrl.shutdown_sync(timeout=0.5)

    def test_send_file_broadcast_empty_peers_emits_error(self) -> None:
        _get_app()
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 10
        session = Mock()
        session.username = "alice"
        ctrl._session = session
        ctrl._ft_manager = Mock()

        errors: list[str] = []
        ctrl.bridge.server_error_received.connect(lambda ev: errors.append(ev.code))
        ctrl.send_file_broadcast(["alice"], "dummy.txt")
        QApplication.processEvents()
        self.assertEqual(errors, ["NO_ONLINE_RECIPIENTS"])
        ctrl.shutdown_sync(timeout=0.5)

    def test_cli_sendfile_all_queues_file_and_requests_online_list(self) -> None:
        from common import message_types as mt
        from ui import cli

        session = Mock()
        session.username = "alice"
        chat = Mock()
        ft_mgr = Mock()
        pending: list[str] = []

        with tempfile.NamedTemporaryFile(delete=False) as tf:
            tf.write(b"cli broadcast")
            tmp_path = Path(tf.name)

        try:
            keep_going = cli._process_input(
                f'/sendfile all "{tmp_path}"',
                session,
                chat,
                ft_mgr,
                pending_broadcast_files=pending,
            )
            self.assertTrue(keep_going)
            self.assertEqual(pending, [str(tmp_path)])
            session.send.assert_called_once_with({"type": mt.LIST_ONLINE})
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_unexpected_disconnect_and_bye_close_session_and_cleanup_ft(self) -> None:
        ctrl = SessionController()
        mock_session = Mock()
        mock_ft = Mock()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 7
        ctrl._session = mock_session
        ctrl._ft_manager = mock_ft
        ctrl._progress_last_emit["s1"] = 1.0

        ctrl._finalize_unexpected_disconnect(7, "Dropped")
        mock_ft.cleanup.assert_called_once_with("Connection lost")
        mock_session.close.assert_called_once()
        self.assertEqual(ctrl._progress_last_emit, {})

        # Verify _on_server_bye also closes session and cleans up ft
        mock_session_2 = Mock()
        mock_ft_2 = Mock()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 8
        ctrl._session = mock_session_2
        ctrl._ft_manager = mock_ft_2
        ctrl._on_server_bye(8, "Server shutdown")
        mock_ft_2.cleanup.assert_called_once_with("Server shutdown")
        mock_session_2.close.assert_called_once()
        ctrl.shutdown_sync(timeout=0.5)

    def test_bubble_delegate_wraps_long_unbroken_tokens_in_size_hint(self) -> None:
        from PySide6.QtCore import QModelIndex, QRect
        from PySide6.QtWidgets import QStyleOptionViewItem
        from ui.gui.models import MessageBubbleDelegate

        _get_app()
        model = MessageListModel()
        short_rec = MessageRecord(
            sender="alice",
            text="hi",
            is_outgoing=False,
            is_broadcast=True,
            timestamp_str="10:00",
        )
        long_token_rec = MessageRecord(
            sender="alice",
            text="a" * 160,
            is_outgoing=False,
            is_broadcast=True,
            timestamp_str="10:00",
        )
        model.append_message(short_rec)
        model.append_message(long_token_rec)

        delegate = MessageBubbleDelegate()
        option = QStyleOptionViewItem()
        option.rect = QRect(0, 0, 360, 400)

        short_h = delegate.sizeHint(option, model.index(0, 0)).height()
        long_h = delegate.sizeHint(option, model.index(1, 0)).height()
        self.assertGreater(long_h, short_h * 2)

    def test_controller_post_shutdown_calls_and_malformed_progress_are_safe(self) -> None:
        ctrl = SessionController()
        ctrl._state = ConnectionState.CONNECTED
        ctrl._session_gen = 4
        ctrl._session = Mock()
        ctrl._chat = Mock()
        ctrl._ft_manager = Mock()

        # Malformed current/total booleans or non-numeric strings should coerce to 0 cleanly
        progress_events = []
        ctrl.bridge.transfer_event_received.connect(progress_events.append)
        ctrl._on_transfer_event(
            4,
            {
                "event": "progress",
                "session_id": "s1",
                "direction": "upload",
                "peer": "bob",
                "filename": "a.txt",
                "current": True,
                "total": "invalid",
            },
        )
        self.assertEqual(len(progress_events), 1)
        self.assertEqual(progress_events[0].current, 0)
        self.assertEqual(progress_events[0].total, 0)

        ctrl.shutdown_sync(timeout=0.5)

        # Post-shutdown operations must be no-ops without raising RuntimeError
        self.assertFalse(ctrl.connect("alice", "127.0.0.1", 5000))
        ctrl.refresh_online_users()
        ctrl.send_broadcast("hi")
        ctrl.send_direct("bob", "hi")
        ctrl.send_file("bob", "file.txt")
        ctrl.send_file_broadcast(["bob"], "file.txt")
        ctrl.disconnect("late")

    def test_main_window_send_guards_oversized_and_disconnected_messages(self) -> None:
        from chat.client_chat import MAX_CHAT_MESSAGE_BYTES

        _get_app()
        ctrl = SessionController()
        window = MainWindow(controller=ctrl)
        try:
            window._on_connected(
                ConnectResultEvent(gen=ctrl.current_gen, username="alice", server_host="127.0.0.1", server_port=5000)
            )
            self.assertEqual(window.composer.toPlainText(), "")

            # While disconnected, send is blocked with banner and keeps composer text
            window.composer.setPlainText("hello offline")
            window._on_send_clicked()
            self.assertFalse(window.error_banner.isHidden())
            self.assertEqual(window.composer.toPlainText(), "hello offline")

            # While connected, oversized UTF-8 payload is rejected cleanly without clearing draft
            ctrl._state = ConnectionState.CONNECTED
            oversized = "a" * (MAX_CHAT_MESSAGE_BYTES + 10)
            window.composer.setPlainText(oversized)
            before_count = window.message_model.rowCount()
            window._on_send_clicked()
            self.assertEqual(window.message_model.rowCount(), before_count)
            self.assertFalse(window.error_banner.isHidden())
            self.assertEqual(window.composer.toPlainText(), oversized)

            # New connection clears stale draft & hides workspace banner
            window._on_connected(
                ConnectResultEvent(gen=ctrl.current_gen, username="alice", server_host="127.0.0.1", server_port=5000)
            )
            self.assertEqual(window.composer.toPlainText(), "")
            self.assertTrue(window.error_banner.isHidden())
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()

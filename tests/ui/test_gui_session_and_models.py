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
    TransferSnapshotEvent,
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
            session.receiver_thread = threading.current_thread()
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
            session.receiver_thread = threading.current_thread()
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

    def test_theme_toggle_and_sidebar_search_filter(self) -> None:
        from PySide6.QtCore import Qt
        from ui.gui.theme import PALETTE

        _get_app()
        ctrl = SessionController()
        window = MainWindow(controller=ctrl)
        try:
            ctrl._session_gen = 3
            window._on_connected(
                ConnectResultEvent(gen=3, username="alice", server_host="127.0.0.1", server_port=5000)
            )
            window._on_online_list(OnlineListEvent(gen=3, users=("alice", "bob", "charlie")))
            self.assertEqual(window.peer_list.count(), 2)

            window.peer_search_input.setText("bob")
            self.assertEqual(window.peer_list.count(), 1)
            self.assertEqual(window.peer_list.item(0).data(Qt.ItemDataRole.UserRole), "bob")

            window.peer_search_input.clear()
            self.assertEqual(window.peer_list.count(), 2)

            self.assertEqual(PALETTE["mode"], "dark")
            window._toggle_theme()
            self.assertEqual(PALETTE["mode"], "light")
            window._toggle_theme()
            self.assertEqual(PALETTE["mode"], "dark")
        finally:
            window.close()

    def test_inline_file_card_upsert_and_inspector_toggle(self) -> None:
        from PySide6.QtCore import Qt

        _get_app()
        ctrl = SessionController()
        window = MainWindow(controller=ctrl)
        try:
            ctrl._session_gen = 4
            window._on_connected(
                ConnectResultEvent(
                    gen=4,
                    username="alice",
                    server_host="127.0.0.1",
                    server_port=5000,
                    tls_version="TLSv1.3",
                    tls_cipher="TLS_AES_256_GCM_SHA384",
                )
            )
            self.assertEqual(window.tls_chip.text(), "TLSv1.3 Verified")
            self.assertEqual(window.kv_cipher_val.text(), "TLS_AES_256_GCM_SHA384")

            window._on_online_list(OnlineListEvent(gen=4, users=("alice", "bob")))
            window.store.set_active_conversation("bob")
            window._refresh_sidebar()
            window._load_active_conversation()

            window._on_transfer_event(
                TransferSnapshotEvent(
                    gen=4,
                    event="progress",
                    session_id="s1",
                    direction="download",
                    peer="bob",
                    filename="spec.pdf",
                    current=500,
                    total=1000,
                )
            )
            window._on_transfer_event(
                TransferSnapshotEvent(
                    gen=4,
                    event="progress",
                    session_id="s1",
                    direction="download",
                    peer="bob",
                    filename="spec.pdf",
                    current=1000,
                    total=1000,
                )
            )
            window._on_transfer_event(
                TransferSnapshotEvent(
                    gen=4,
                    event="completed",
                    session_id="s1",
                    direction="download",
                    peer="bob",
                    filename="spec.pdf",
                    current=1000,
                    total=1000,
                    saved_path="downloads/spec.pdf",
                )
            )

            self.assertEqual(window.message_model.rowCount(), 1)
            rec = window.message_model.index(0, 0).data(MessageListModel.RecordRole)
            self.assertEqual(rec.kind, "file")
            self.assertEqual(rec.file_state, "COMPLETED")
            self.assertEqual(rec.saved_path, "downloads/spec.pdf")
            self.assertEqual(window.transfer_count_lbl.text(), "1")

            window._set_inspector_open(False)
            self.assertFalse(window.toggle_inspector_btn.isChecked())
            self.assertFalse(window.rail_files_btn.isChecked())
            self.assertEqual(window.inspector_panel.maximumWidth(), 0)

            window._set_inspector_open(True)
            self.assertTrue(window.toggle_inspector_btn.isChecked())
            self.assertTrue(window.rail_files_btn.isChecked())
            self.assertEqual(window.inspector_panel.maximumWidth(), 328)
        finally:
            window.close()

    def test_open_downloads_uses_project_downloads_dir(self) -> None:
        from unittest.mock import patch
        from PySide6.QtCore import QUrl
        from common.app_config import DEFAULT_DOWNLOADS_DIR

        _get_app()
        ctrl = SessionController()
        window = MainWindow(controller=ctrl)
        try:
            with patch("ui.gui.main_window.QDesktopServices.openUrl", return_value=True) as open_spy:
                window._on_open_downloads()
                open_spy.assert_called_once_with(QUrl.fromLocalFile(str(DEFAULT_DOWNLOADS_DIR.resolve())))

            mgr = ClientFileTransferManager("alice", lambda m: None, lambda s: None)
            self.assertEqual(mgr.receiver.download_dir, DEFAULT_DOWNLOADS_DIR)
        finally:
            window.close()

    def test_broadcast_file_transfer_routes_both_sender_and_receiver_into_global_chat(self) -> None:
        import shutil
        import tempfile
        from unittest.mock import Mock

        _get_app()
        tmp_dir = Path(tempfile.mkdtemp())
        sender_ctrl = SessionController()
        sender_win = MainWindow(controller=sender_ctrl)
        recv_ctrl = SessionController()
        recv_win = MainWindow(controller=recv_ctrl)

        try:
            # --- 1) Sender side: fan-out aggregates into 1 bubble inside #all ---
            sender_ctrl._state = ConnectionState.CONNECTED
            sender_ctrl._session_gen = 10
            sender_ctrl._session = Mock(username="alice")
            sender_win._on_connected(
                ConnectResultEvent(gen=10, username="alice", server_host="127.0.0.1", server_port=5000)
            )

            sent_frames: list[dict] = []
            ft_mgr = ClientFileTransferManager(
                username="alice",
                send_fn=sent_frames.append,
                print_fn=lambda _t: None,
                on_event=lambda payload: sender_ctrl._on_transfer_event(10, payload),
                download_dir=tmp_dir / "alice_dl",
            )
            sender_ctrl._ft_manager = ft_mgr

            sample_file = tmp_dir / "broadcast_notes.pdf"
            sample_file.write_bytes(b"BROADCAST-PDF-" * 200)

            sender_ctrl.send_file_broadcast(["bob", "charlie"], sample_file)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if len(sender_win.store.transfers) == 2 and sender_win.message_model.rowCount() == 1:
                    break
                time.sleep(0.01)

            self.assertEqual(len(sender_win.store.transfers), 2)
            self.assertEqual(sender_win.transfer_count_lbl.text(), "2")
            self.assertEqual(len(sender_win.store.conversations[BROADCAST_CONV_ID].messages), 1)
            self.assertNotIn("bob", sender_win.store.conversations)
            self.assertNotIn("charlie", sender_win.store.conversations)

            bc_card = sender_win.store.conversations[BROADCAST_CONV_ID].messages[0]
            self.assertEqual(bc_card.kind, "file")
            self.assertTrue(bc_card.is_broadcast)
            self.assertTrue(bc_card.is_outgoing)
            self.assertEqual(bc_card.total_bytes, sample_file.stat().st_size * 2)

            sids = list(sender_win.store.transfers.keys())
            self.assertEqual(len(sent_frames), 2)
            self.assertTrue(all(f.get("is_broadcast") is True for f in sent_frames))

            # Complete bob's upload and fail charlie's upload -> partial status reported cleanly
            ft_mgr.handle_incoming({"type": "FILE_RESULT", "session_id": sids[0], "status": "OK", "reason": ""})
            ft_mgr.handle_incoming({"type": "FILE_ERROR", "session_id": sids[1], "reason": "Offline"})
            QApplication.processEvents()
            bc_card_final = sender_win.store.conversations[BROADCAST_CONV_ID].messages[0]
            self.assertEqual(bc_card_final.file_state, "FAILED")
            self.assertIn("1/2", bc_card_final.status_note)
            self.assertIn("1 thất bại", bc_card_final.status_note)

            # --- 2) Receiver side: incoming broadcast file goes to #all and increments unread ---
            recv_ctrl._session_gen = 11
            recv_win._on_connected(
                ConnectResultEvent(gen=11, username="bob", server_host="127.0.0.1", server_port=5000)
            )
            recv_win.store.set_active_conversation("alice")
            recv_win._refresh_sidebar()
            recv_win._load_active_conversation()

            recv_win._on_transfer_event(
                TransferSnapshotEvent(
                    gen=11,
                    event="started",
                    session_id="rx-bc-1",
                    direction="download",
                    peer="alice",
                    filename="broadcast_notes.pdf",
                    current=0,
                    total=2800,
                    is_broadcast=True,
                )
            )
            recv_win._on_transfer_event(
                TransferSnapshotEvent(
                    gen=11,
                    event="completed",
                    session_id="rx-bc-1",
                    direction="download",
                    peer="alice",
                    filename="broadcast_notes.pdf",
                    current=2800,
                    total=2800,
                    saved_path="downloads/broadcast_notes.pdf",
                    is_broadcast=True,
                )
            )

            self.assertEqual(len(recv_win.store.conversations["alice"].messages), 0)
            self.assertEqual(len(recv_win.store.conversations[BROADCAST_CONV_ID].messages), 1)
            self.assertEqual(recv_win.store.conversations[BROADCAST_CONV_ID].unread_count, 1)
            rx_msg = recv_win.store.conversations[BROADCAST_CONV_ID].messages[0]
            self.assertEqual(rx_msg.sender, "alice")
            self.assertFalse(rx_msg.is_outgoing)
            self.assertTrue(rx_msg.is_broadcast)
            self.assertEqual(rx_msg.file_state, "COMPLETED")
            self.assertEqual(rx_msg.saved_path, "downloads/broadcast_notes.pdf")
        finally:
            sender_ctrl.shutdown_sync(timeout=0.5)
            recv_ctrl.shutdown_sync(timeout=0.5)
            sender_win.close()
            recv_win.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_room_file_transfer_routes_both_sender_and_receiver_into_room_conversation(self) -> None:
        import shutil
        import tempfile
        from unittest.mock import Mock

        _get_app()
        tmp_dir = Path(tempfile.mkdtemp())
        sender_ctrl = SessionController()
        sender_win = MainWindow(controller=sender_ctrl)
        recv_ctrl = SessionController()
        recv_win = MainWindow(controller=recv_ctrl)

        try:
            # --- 1) Sender side: room transfer aggregates into room conversation ---
            sender_ctrl._state = ConnectionState.CONNECTED
            sender_ctrl._session_gen = 20
            sender_ctrl._session = Mock(username="alice")
            sender_win._on_connected(
                ConnectResultEvent(gen=20, username="alice", server_host="127.0.0.1", server_port=5000)
            )

            # Join room #nhom1 on sender window
            sender_win.store.room_members["#nhom1"] = ["alice", "bob", "charlie"]
            sender_win.store.online_users = {"bob", "charlie"}
            sender_win.store.set_active_conversation("#nhom1")
            sender_win._refresh_sidebar()
            sender_win._load_active_conversation()

            # Test transient status helper does not crash and updates label
            sender_win._set_transient_status("Đang gửi file...", timeout_ms=500)
            self.assertEqual(sender_win.global_status_label.text(), "Đang gửi file...")

            sent_frames: list[dict] = []
            ft_mgr = ClientFileTransferManager(
                username="alice",
                send_fn=sent_frames.append,
                print_fn=lambda _t: None,
                on_event=lambda payload: sender_ctrl._on_transfer_event(20, payload),
                download_dir=tmp_dir / "alice_dl",
            )
            sender_ctrl._ft_manager = ft_mgr

            sample_file = tmp_dir / "room_plan.pdf"
            sample_file.write_bytes(b"ROOM-PLAN-PDF-" * 150)

            sender_ctrl.send_file_room("#nhom1", ["bob", "charlie"], sample_file)
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if len(sender_win.store.transfers) == 2 and sender_win.message_model.rowCount() == 1:
                    break
                time.sleep(0.01)

            self.assertEqual(len(sender_win.store.transfers), 2)
            self.assertEqual(len(sender_win.store.conversations["#nhom1"].messages), 1)
            self.assertNotIn("bob", sender_win.store.conversations)
            self.assertNotIn("charlie", sender_win.store.conversations)

            room_card = sender_win.store.conversations["#nhom1"].messages[0]
            self.assertEqual(room_card.kind, "file")
            self.assertTrue(room_card.is_outgoing)
            self.assertEqual(room_card.total_bytes, sample_file.stat().st_size * 2)

            self.assertEqual(len(sent_frames), 2)
            self.assertTrue(all(f.get("room_id") == "#nhom1" for f in sent_frames))
            self.assertFalse(any(f.get("is_broadcast") is True for f in sent_frames))

            # Finish Bob upload, fail Charlie upload
            sids = list(sender_win.store.transfers.keys())
            ft_mgr.handle_incoming({"type": "FILE_RESULT", "session_id": sids[0], "status": "OK", "reason": ""})
            ft_mgr.handle_incoming({"type": "FILE_ERROR", "session_id": sids[1], "reason": "Connection lost"})
            QApplication.processEvents()
            card_final = sender_win.store.conversations["#nhom1"].messages[0]
            self.assertEqual(card_final.file_state, "FAILED")
            self.assertIn("thành viên #nhom1", card_final.status_note)
            self.assertIn("1 thất bại", card_final.status_note)

            # --- 2) Receiver side: incoming room file goes to room conversation ---
            recv_ctrl._session_gen = 21
            recv_win._on_connected(
                ConnectResultEvent(gen=21, username="bob", server_host="127.0.0.1", server_port=5000)
            )
            recv_win.store.room_members["#nhom1"] = ["alice", "bob", "charlie"]
            recv_win.store.set_active_conversation("#nhom1")
            recv_win._refresh_sidebar()
            recv_win._load_active_conversation()

            recv_win._on_transfer_event(
                TransferSnapshotEvent(
                    gen=21,
                    event="started",
                    session_id="rx-room-1",
                    direction="download",
                    peer="alice",
                    filename="room_plan.pdf",
                    current=0,
                    total=2100,
                    room_id="#nhom1",
                )
            )
            recv_win._on_transfer_event(
                TransferSnapshotEvent(
                    gen=21,
                    event="completed",
                    session_id="rx-room-1",
                    direction="download",
                    peer="alice",
                    filename="room_plan.pdf",
                    current=2100,
                    total=2100,
                    saved_path="downloads/room_plan.pdf",
                    room_id="#nhom1",
                )
            )
            QApplication.processEvents()

            self.assertNotIn("alice", recv_win.store.conversations)
            self.assertEqual(len(recv_win.store.conversations["#nhom1"].messages), 1)
            rx_msg = recv_win.store.conversations["#nhom1"].messages[0]
            self.assertEqual(rx_msg.sender, "alice")
            self.assertFalse(rx_msg.is_outgoing)
            self.assertEqual(rx_msg.file_state, "COMPLETED")
            self.assertEqual(rx_msg.saved_path, "downloads/room_plan.pdf")
        finally:
            sender_ctrl.shutdown_sync(timeout=0.5)
            recv_ctrl.shutdown_sync(timeout=0.5)
            sender_win.close()
            recv_win.close()
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_live_tls_gui_broadcast_file_transfer_e2e(self) -> None:
        import shutil
        import socket
        import tempfile
        from unittest.mock import patch
        from common import message_types as mt
        from infrastructure import client_connection, server as infra_server

        _get_app()
        for user in infra_server.user_registry.list_online():
            sess = infra_server.user_registry.get_session(user)
            if sess is not None:
                infra_server.user_registry.remove_if_same(user, sess.connection)

        tls_ctx = infra_server.create_server_context()
        dispatcher = infra_server.create_message_dispatcher()
        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.bind(("127.0.0.1", 0))
        tcp_sock.listen(8)
        tcp_sock.settimeout(0.2)
        tcp_port = tcp_sock.getsockname()[1]

        stop_event = threading.Event()
        client_threads: list[threading.Thread] = []

        def accept_loop() -> None:
            while not stop_event.is_set():
                try:
                    conn, addr = tcp_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                t = threading.Thread(
                    target=infra_server.handle_client,
                    args=(conn, addr, tls_ctx, dispatcher),
                    daemon=True,
                )
                t.start()
                client_threads.append(t)

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        tmp_dir = Path(tempfile.mkdtemp())
        alice_dl = tmp_dir / "alice_dl"
        bob_dl = tmp_dir / "bob_dl"
        charlie_dl = tmp_dir / "charlie_dl"

        alice_ctrl = SessionController()
        bob_ctrl = SessionController()
        alice_win = MainWindow(controller=alice_ctrl)
        bob_win = MainWindow(controller=bob_ctrl)
        charlie = None
        charlie_ft = None

        try:
            # Connect charlie headless
            charlie_events: list[dict] = []
            charlie_holder: list[ClientFileTransferManager] = []

            def charlie_unhandled(msg: dict) -> None:
                if msg.get("type") in {
                    mt.FILE_INIT,
                    mt.FILE_ACCEPT,
                    mt.FILE_CHUNK,
                    mt.FILE_END,
                    mt.FILE_RESULT,
                    mt.FILE_ERROR,
                } and charlie_holder:
                    charlie_holder[0].handle_incoming(msg)

            charlie = client_connection.ClientSession.connect(
                "charlie", lambda _e: None, charlie_unhandled, server_host="127.0.0.1", server_port=tcp_port
            )
            charlie_ft = ClientFileTransferManager(
                "charlie", charlie.send, lambda _s: None, on_event=charlie_events.append, download_dir=charlie_dl
            )
            charlie_holder.append(charlie_ft)
            charlie.start()

            bob_ctrl.connect("bob", "127.0.0.1", tcp_port)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and bob_win.stack.currentIndex() != 1:
                QApplication.processEvents()
                time.sleep(0.02)
            bob_dl.mkdir(parents=True, exist_ok=True)
            assert bob_ctrl._ft_manager is not None
            bob_ctrl._ft_manager.receiver.download_dir = bob_dl

            alice_ctrl.connect("alice", "127.0.0.1", tcp_port)
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                if (
                    alice_win.stack.currentIndex() == 1
                    and "bob" in alice_win.store.online_users
                    and "charlie" in alice_win.store.online_users
                ):
                    break
                time.sleep(0.02)

            payload = b"TLS-BROADCAST-E2E-" * 4500  # ~81 KB (multi-chunk)
            src_file = tmp_dir / "lecture_slides.bin"
            src_file.write_bytes(payload)

            alice_ctrl.send_file_broadcast(["bob", "charlie"], src_file)

            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                QApplication.processEvents()
                alice_msgs = alice_win.store.conversations[BROADCAST_CONV_ID].messages
                bob_msgs = bob_win.store.conversations[BROADCAST_CONV_ID].messages
                alice_done = bool(alice_msgs and alice_msgs[0].file_state == "COMPLETED")
                bob_done = bool(bob_msgs and bob_msgs[0].file_state == "COMPLETED")
                charlie_done = any(e.get("event") == "completed" for e in charlie_events)
                if alice_done and bob_done and charlie_done:
                    break
                time.sleep(0.02)

            alice_bc_msgs = alice_win.store.conversations[BROADCAST_CONV_ID].messages
            self.assertEqual(len(alice_bc_msgs), 1)
            self.assertEqual(alice_bc_msgs[0].file_state, "COMPLETED")
            self.assertIn("2/2", alice_bc_msgs[0].status_note)
            self.assertNotIn("bob", alice_win.store.conversations)
            self.assertNotIn("charlie", alice_win.store.conversations)

            bob_bc_msgs = bob_win.store.conversations[BROADCAST_CONV_ID].messages
            self.assertEqual(len(bob_bc_msgs), 1)
            self.assertEqual(bob_bc_msgs[0].sender, "alice")
            self.assertEqual(bob_bc_msgs[0].file_state, "COMPLETED")
            self.assertEqual((bob_dl / "lecture_slides.bin").read_bytes(), payload)
            self.assertEqual((charlie_dl / "lecture_slides.bin").read_bytes(), payload)
            self.assertTrue(all(e.get("is_broadcast") is True for e in charlie_events))
        finally:
            alice_ctrl.shutdown_sync(timeout=1.5)
            bob_ctrl.shutdown_sync(timeout=1.5)
            alice_win.close()
            bob_win.close()
            if charlie_ft is not None:
                charlie_ft.cleanup("done")
            if charlie is not None:
                try:
                    charlie.logout(timeout=0.5)
                except Exception:
                    pass
                charlie.close()
            stop_event.set()
            tcp_sock.close()
            accept_thread.join(timeout=1.0)
            for t in client_threads:
                t.join(timeout=1.0)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            for user in infra_server.user_registry.list_online():
                sess = infra_server.user_registry.get_session(user)
                if sess is not None:
                    infra_server.user_registry.remove_if_same(user, sess.connection)


if __name__ == "__main__":
    unittest.main()

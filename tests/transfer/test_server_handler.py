"""
Unit tests for transfer/server_handler.py.
"""

from __future__ import annotations
from pathlib import Path
import sys
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from transfer.server_handler import (
    FileTransferServerHandler,
    register_file_transfer_handlers,
)


class DummyDispatcher:
    def __init__(self) -> None:
        self.handlers = {}

    def register(self, message_type: str, handler) -> None:
        self.handlers[message_type] = handler


class TestServerHandler(unittest.TestCase):
    """Test suite for FileTransferServerHandler and router registration."""

    def setUp(self) -> None:
        self.sent_messages = []
        self.online_users = {"alice", "bob"}

        def mock_send_to_user(target: str, header: dict, payload: bytes) -> bool:
            if target not in self.online_users:
                return False
            self.sent_messages.append((target, header, payload))
            return True

        self.handler = FileTransferServerHandler(
            send_to_user_fn=mock_send_to_user,
            is_user_online_fn=lambda u: u in self.online_users,
        )

    def test_handle_init_online_recipient_relays_frame(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_999",
            "sender": "alice",
            "receiver": "bob",
            "filename": "doc.pdf",
            "filesize": 100,
        }

        self.handler.handle_message("alice", header, b"")

        # Must relay FILE_INIT to bob
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "bob")
        self.assertEqual(h["type"], "FILE_INIT")
        self.assertEqual(h["sender"], "alice")

    def test_handle_init_offline_recipient_returns_user_offline_error(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_offline",
            "sender": "alice",
            "receiver": "charlie",  # charlie is offline
            "filename": "doc.pdf",
            "filesize": 100,
        }

        self.handler.handle_message("alice", header, b"")

        # Must return FILE_ERROR ERR_USER_OFFLINE to alice
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_USER_OFFLINE")

    def test_handle_init_self_recipient_returns_error(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_self",
            "sender": "alice",
            "receiver": "alice",
            "filename": "doc.pdf",
            "filesize": 100,
        }

        self.handler.handle_message("alice", header, b"")

        # Must return FILE_ERROR ERR_RECIPIENT_SELF
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_RECIPIENT_SELF")

    def test_handle_init_recipient_alias_relays_successfully(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_alias",
            "sender": "alice",
            "recipient": "bob",  # uses 'recipient' instead of 'receiver'
            "filename": "doc.pdf",
            "filesize": 100,
        }

        self.handler.handle_message("alice", header, b"")

        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "bob")
        self.assertEqual(h["type"], "FILE_INIT")

    def test_handle_init_preserves_is_broadcast_and_rejects_non_bool(self) -> None:
        valid_bc = {
            "type": "FILE_INIT",
            "session_id": "sid_bc_ok",
            "sender": "alice",
            "receiver": "bob",
            "filename": "doc.pdf",
            "filesize": 100,
            "is_broadcast": True,
        }
        self.handler.handle_message("alice", valid_bc, b"")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, _ = self.sent_messages[0]
        self.assertEqual(target, "bob")
        self.assertTrue(h.get("is_broadcast"))

        self.sent_messages.clear()
        for bad_val in ("true", 1):
            bad_bc = {
                "type": "FILE_INIT",
                "session_id": f"sid_bc_bad_{bad_val}",
                "sender": "alice",
                "receiver": "bob",
                "filename": "doc.pdf",
                "filesize": 100,
                "is_broadcast": bad_val,
            }
            self.handler.handle_message("alice", bad_bc, b"")
            self.assertEqual(self.sent_messages[-1][1]["code"], "ERR_INVALID_METADATA")

    def test_relay_chunk_to_correct_target(self) -> None:
        sid = "sid_relay"
        self.handler.handle_message(
            "alice",
            {
                "type": "FILE_INIT",
                "session_id": sid,
                "sender": "alice",
                "receiver": "bob",
                "filename": "test.txt",
                "filesize": 10,
            },
            b"",
        )
        self.sent_messages.clear()

        # Bob accepts
        self.handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": sid}, b"")
        self.assertEqual(len(self.sent_messages), 1)
        self.assertEqual(self.sent_messages[0][0], "alice")

        # Alice sends chunk
        self.sent_messages.clear()
        chunk_data = b"0123456789"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_CHUNK", "session_id": sid, "chunk_index": 0},
            chunk_data,
        )
        self.assertEqual(len(self.sent_messages), 1)
        self.assertEqual(self.sent_messages[0][0], "bob")
        self.assertEqual(self.sent_messages[0][2], chunk_data)

    def test_relay_unknown_session_returns_session_not_found_error(self) -> None:
        self.handler.handle_message(
            "alice",
            {"type": "FILE_CHUNK", "session_id": "non_existent_sid", "chunk_index": 0},
            b"data",
        )
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_SESSION_NOT_FOUND")

    def test_relay_result_cleans_up_session(self) -> None:
        sid = "sid_cleanup"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid, "sender": "alice", "receiver": "bob", "filename": "f.txt", "filesize": 5},
            b"",
        )
        self.handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": sid}, b"")
        self.handler.handle_message("alice", {"type": "FILE_CHUNK", "session_id": sid, "chunk_index": 0}, b"12345")
        self.handler.handle_message("alice", {"type": "FILE_END", "session_id": sid}, b"")
        self.assertIn(sid, self.handler._active_sessions)

        # Result OK after FILE_END completes and removes session
        self.handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": sid, "status": "OK"}, b"")
        self.assertNotIn(sid, self.handler._active_sessions)

    def test_premature_file_result_rejected_on_non_empty_transfer_and_allowed_on_zero_byte(self) -> None:
        # Non-empty transfer must reject FILE_RESULT before FILE_END
        sid_non_empty = "sid_early_result"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid_non_empty, "sender": "alice", "receiver": "bob", "filename": "a.bin", "filesize": 1},
            b"",
        )
        self.sent_messages.clear()
        self.handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": sid_non_empty, "status": "OK"}, b"")
        self.assertEqual(len(self.sent_messages), 1)
        self.assertEqual(self.sent_messages[0][0], "bob")
        self.assertEqual(self.sent_messages[0][1]["code"], "ERR_INVALID_STATE")
        self.assertIn(sid_non_empty, self.handler._active_sessions)

        self.handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": sid_non_empty}, b"")
        self.sent_messages.clear()
        self.handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": sid_non_empty, "status": "OK"}, b"")
        self.assertEqual(self.sent_messages[0][1]["code"], "ERR_INVALID_STATE")
        self.assertIn(sid_non_empty, self.handler._active_sessions)

        # Zero-byte fast path legitimately transitions INIT -> COMPLETED on FILE_RESULT
        sid_zero = "sid_zero_result"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid_zero, "sender": "alice", "receiver": "bob", "filename": "empty.txt", "filesize": 0},
            b"",
        )
        self.sent_messages.clear()
        self.handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": sid_zero, "status": "OK"}, b"")
        self.assertEqual(len(self.sent_messages), 1)
        self.assertEqual(self.sent_messages[0][0], "alice")
        self.assertEqual(self.sent_messages[0][1]["type"], "FILE_RESULT")
        self.assertNotIn(sid_zero, self.handler._active_sessions)

    def test_on_client_disconnect_notifies_peer_and_cleans_up(self) -> None:
        sid = "sid_disc"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid, "sender": "alice", "receiver": "bob", "filename": "test.txt", "filesize": 10},
            b"",
        )
        self.sent_messages.clear()

        # Alice disconnects unexpectedly
        self.handler.on_client_disconnect("alice")

        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "bob")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_PEER_DISCONNECTED")
        self.assertNotIn(sid, self.handler._active_sessions)

    def test_handle_init_invalid_filesize_returns_error(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_bad_size",
            "sender": "alice",
            "receiver": "bob",
            "filename": "doc.pdf",
            "filesize": "not_a_number",
        }
        self.handler.handle_message("alice", header, b"")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_INVALID_METADATA")

    def test_handle_init_path_traversal_filename_returns_error(self) -> None:
        header = {
            "type": "FILE_INIT",
            "session_id": "sid_bad_name",
            "sender": "alice",
            "receiver": "bob",
            "filename": "../../evil.sh",
            "filesize": 100,
        }
        self.handler.handle_message("alice", header, b"")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_INVALID_METADATA")

    def test_handle_init_duplicate_session_id_rejected(self) -> None:
        sid = "sid_dup"
        header = {
            "type": "FILE_INIT",
            "session_id": sid,
            "sender": "alice",
            "receiver": "bob",
            "filename": "file.txt",
            "filesize": 50,
        }
        self.handler.handle_message("alice", header, b"")
        self.assertIn(sid, self.handler._active_sessions)
        self.sent_messages.clear()

        # Try initializing another session with the same session_id
        self.handler.handle_message("alice", header, b"")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_DUPLICATE_SESSION")

    def test_relay_rejects_unauthorized_sender_role(self) -> None:
        sid = "sid_role"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid, "sender": "alice", "receiver": "bob", "filename": "f.txt", "filesize": 10},
            b"",
        )
        self.sent_messages.clear()

        # Sender 'alice' illegally sends FILE_ACCEPT (which only receiver 'bob' can send)
        self.handler.handle_message("alice", {"type": "FILE_ACCEPT", "session_id": sid}, b"")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_UNAUTHORIZED_ROLE")

    def test_relay_rejects_invalid_state_transition(self) -> None:
        sid = "sid_state"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid, "sender": "alice", "receiver": "bob", "filename": "f.txt", "filesize": 10},
            b"",
        )
        self.sent_messages.clear()

        # Alice attempts to send chunk before Bob accepts (session in INIT state)
        self.handler.handle_message("alice", {"type": "FILE_CHUNK", "session_id": sid, "chunk_index": 0}, b"data")
        self.assertEqual(len(self.sent_messages), 1)
        target, h, p = self.sent_messages[0]
        self.assertEqual(target, "alice")
        self.assertEqual(h["type"], "FILE_ERROR")
        self.assertEqual(h["code"], "ERR_INVALID_STATE")

    def test_prune_stale_sessions(self) -> None:
        import time
        sid = "sid_stale"
        self.handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": sid, "sender": "alice", "receiver": "bob", "filename": "f.txt", "filesize": 10},
            b"",
        )
        self.assertIn(sid, self.handler._active_sessions)
        self.sent_messages.clear()

        # Manually backdate last_updated
        self.handler._active_sessions[sid].last_updated = time.time() - 100

        pruned = self.handler.prune_stale_sessions(timeout_seconds=60)
        self.assertEqual(pruned, [sid])
        self.assertNotIn(sid, self.handler._active_sessions)
        # Both alice and bob receive timeout error
        self.assertEqual(len(self.sent_messages), 2)
        targets = {m[0] for m in self.sent_messages}
        self.assertEqual(targets, {"alice", "bob"})
        self.assertEqual(self.sent_messages[0][1]["code"], "ERR_SESSION_TIMEOUT")

    def test_register_file_transfer_handlers_binds_all_types(self) -> None:
        dispatcher = DummyDispatcher()
        register_file_transfer_handlers(dispatcher)
        for msg_type in ("FILE_INIT", "FILE_ACCEPT", "FILE_CHUNK", "FILE_END", "FILE_RESULT", "FILE_ERROR"):
            self.assertIn(msg_type, dispatcher.handlers)

    def test_handle_init_rejects_slashes_colons_and_unsafe_session_id(self) -> None:
        for bad_filename in ("/etc/passwd", "sub/file.txt", "C:\\evil.exe", ".", ""):
            self.sent_messages.clear()
            self.handler.handle_message(
                "alice",
                {
                    "type": "FILE_INIT",
                    "session_id": "sid_ok",
                    "sender": "alice",
                    "receiver": "bob",
                    "filename": bad_filename,
                    "filesize": 10,
                },
                b"",
            )
            self.assertEqual(len(self.sent_messages), 1)
            self.assertEqual(self.sent_messages[0][1]["code"], "ERR_INVALID_METADATA")

        for bad_sid in ("../sid", "a/b", "a\\b", "a:b"):
            self.sent_messages.clear()
            self.handler.handle_message(
                "alice",
                {
                    "type": "FILE_INIT",
                    "session_id": bad_sid,
                    "sender": "alice",
                    "receiver": "bob",
                    "filename": "ok.txt",
                    "filesize": 10,
                },
                b"",
            )
            self.assertEqual(len(self.sent_messages), 1)
            self.assertEqual(self.sent_messages[0][1]["code"], "ERR_INVALID_METADATA")

    def test_init_rejects_boolean_filesize_and_whitespace_receiver(self) -> None:
        """FILE_INIT must reject boolean filesize/total_chunks and whitespace-only receiver."""
        for bad_msg in (
            {
                "type": "FILE_INIT",
                "session_id": "sid_bool",
                "sender": "alice",
                "receiver": "bob",
                "filename": "ok.txt",
                "filesize": True,
            },
            {
                "type": "FILE_INIT",
                "session_id": "sid_ws_recv",
                "sender": "alice",
                "receiver": "   ",
                "filename": "ok.txt",
                "filesize": 10,
            },
        ):
            self.sent_messages.clear()
            self.handler.handle_message("alice", bad_msg, b"")
            self.assertEqual(len(self.sent_messages), 1)
            self.assertEqual(self.sent_messages[0][1]["code"], "ERR_INVALID_METADATA")

    def test_terminal_relay_pops_session_so_later_disconnect_emits_no_error(self) -> None:
        """Once FILE_RESULT/FILE_ERROR is relayed, disconnect must not emit ERR_PEER_DISCONNECTED."""
        self.handler.handle_message(
            "alice",
            {
                "type": "FILE_INIT",
                "session_id": "sid_done",
                "sender": "alice",
                "receiver": "bob",
                "filename": "ok.txt",
                "filesize": 5,
            },
            b"",
        )
        self.handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": "sid_done"}, b"")
        self.handler.handle_message("alice", {"type": "FILE_CHUNK", "session_id": "sid_done", "chunk_index": 0}, b"12345")
        self.handler.handle_message("alice", {"type": "FILE_END", "session_id": "sid_done"}, b"")
        self.sent_messages.clear()

        self.handler.handle_message(
            "bob",
            {"type": "FILE_RESULT", "session_id": "sid_done", "status": "OK"},
            b"",
        )
        self.assertEqual(len(self.sent_messages), 1)
        self.assertEqual(self.sent_messages[0][1]["type"], "FILE_RESULT")

        self.sent_messages.clear()
        self.handler.on_client_disconnect("alice")
        self.assertEqual(self.sent_messages, [])

    def test_send_to_user_bridge_isolates_value_error_vs_oserror(self) -> None:
        """ValueError during send must not evict recipient; OSError must evict and notify handler."""
        import threading
        from unittest.mock import Mock, patch
        from infrastructure.user_registry import UserRegistry
        from transfer.server_handler import _send_to_user_bridge

        registry = UserRegistry()
        sock = Mock()
        registry.register("bob", sock, ("127.0.0.1", 50000), threading.Lock(), "tok")
        ft_handler = FileTransferServerHandler(
            send_to_user_fn=lambda u, m: True,
            is_user_online_fn=lambda u: registry.get_session(u) is not None,
        )

        # ValueError (e.g. non-JSON/oversized payload): return False, keep bob online
        with (
            patch("infrastructure.user_registry.user_registry", registry),
            patch("transfer.server_handler.file_transfer_server_handler", ft_handler),
            patch("common.protocol.send_message", side_effect=ValueError("bad frame")),
            patch.object(ft_handler, "on_client_disconnect") as disc_spy,
        ):
            self.assertFalse(_send_to_user_bridge("bob", {"type": "PING"}))
            self.assertIsNotNone(registry.get_session("bob"))
            disc_spy.assert_not_called()

        # OSError (broken socket): remove bob and trigger on_client_disconnect
        with (
            patch("infrastructure.user_registry.user_registry", registry),
            patch("transfer.server_handler.file_transfer_server_handler", ft_handler),
            patch("common.protocol.send_message", side_effect=OSError("broken pipe")),
            patch.object(ft_handler, "on_client_disconnect") as disc_spy,
        ):
            self.assertFalse(_send_to_user_bridge("bob", {"type": "PING"}))
            self.assertIsNone(registry.get_session("bob"))
            disc_spy.assert_called_once_with("bob")

    def test_relay_failure_while_recipient_online_isolates_only_failed_session(self) -> None:
        """A single-frame relay failure for an online peer must abort only that transfer."""
        delivered: list[tuple[str, dict, bytes]] = []
        online = {"alice", "bob", "charlie"}

        def flaky_send(target: str, header: dict, payload: bytes = b"") -> bool:
            if header.get("type") == "FILE_CHUNK" and header.get("session_id") == "sid_alice":
                return False  # simulate ValueError in bridge while bob stays online
            delivered.append((target, header, payload))
            return True

        handler = FileTransferServerHandler(
            send_to_user_fn=flaky_send,
            is_user_online_fn=lambda u: u in online,
        )

        # Both alice and charlie start transfers to bob
        handler.handle_message(
            "alice",
            {"type": "FILE_INIT", "session_id": "sid_alice", "receiver": "bob", "filename": "a.bin", "filesize": 5},
            b"",
        )
        handler.handle_message(
            "charlie",
            {"type": "FILE_INIT", "session_id": "sid_charlie", "receiver": "bob", "filename": "c.bin", "filesize": 5},
            b"",
        )
        handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": "sid_alice"}, b"")
        handler.handle_message("bob", {"type": "FILE_ACCEPT", "session_id": "sid_charlie"}, b"")
        delivered.clear()

        # Alice's chunk relay returns False while Bob is still online
        handler.handle_message("alice", {"type": "FILE_CHUNK", "session_id": "sid_alice", "chunk_index": 0}, b"bad!!")

        # Only sid_alice is aborted; charlie's concurrent session stays active without ERR_PEER_DISCONNECTED
        self.assertNotIn("sid_alice", handler._active_sessions)
        self.assertIn("sid_charlie", handler._active_sessions)
        self.assertTrue(all(h.get("session_id") == "sid_alice" and h.get("type") == "FILE_ERROR" for _, h, _ in delivered))
        self.assertEqual({t for t, _, _ in delivered}, {"alice", "bob"})

        # Charlie finishes normally
        delivered.clear()
        handler.handle_message("charlie", {"type": "FILE_CHUNK", "session_id": "sid_charlie", "chunk_index": 0}, b"hello")
        handler.handle_message("charlie", {"type": "FILE_END", "session_id": "sid_charlie"}, b"")
        handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": "sid_charlie", "status": "OK"}, b"")
        self.assertNotIn("sid_charlie", handler._active_sessions)
        self.assertEqual(delivered[-1][0], "charlie")
        self.assertEqual(delivered[-1][1]["type"], "FILE_RESULT")


if __name__ == "__main__":
    unittest.main()

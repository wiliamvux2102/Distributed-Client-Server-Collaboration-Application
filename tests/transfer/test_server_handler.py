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
        self.assertIn(sid, self.handler._active_sessions)

        # Result OK ends session
        self.handler.handle_message("bob", {"type": "FILE_RESULT", "session_id": sid, "status": "OK"}, b"")
        self.assertNotIn(sid, self.handler._active_sessions)

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

    def test_register_file_transfer_handlers_binds_all_types(self) -> None:
        dispatcher = DummyDispatcher()
        register_file_transfer_handlers(dispatcher)
        for msg_type in ("FILE_INIT", "FILE_ACCEPT", "FILE_CHUNK", "FILE_END", "FILE_RESULT", "FILE_ERROR"):
            self.assertIn(msg_type, dispatcher.handlers)


if __name__ == "__main__":
    unittest.main()

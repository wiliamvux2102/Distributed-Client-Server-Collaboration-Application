"""Unit and integration tests for SQLite persistent history and HISTORY_SYNC."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from chat.client_chat import ClientChat
from chat.server_handler import (
    handle_broadcast_message,
    handle_direct_message,
    handle_history_request,
    set_history_store,
)
from common import message_types as mt
from storage.db import HistoryStore
from ui.gui.models import BROADCAST_CONV_ID, ChatSessionStore


class HistoryStoreTests(unittest.TestCase):
    """Tests for SQLite HistoryStore, server handlers, and GUI/ClientChat sync."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "test_history.db"
        self.store = HistoryStore(self.db_path)

    def tearDown(self) -> None:
        set_history_store(None)
        self.store.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_save_and_filter_broadcast_and_direct_messages(self) -> None:
        self.store.save_broadcast_message("alice", "hello all", created_at=1000.0)
        self.store.save_direct_message("alice", "bob", "secret to bob", created_at=1001.0)
        self.store.save_direct_message("bob", "alice", "reply to alice", created_at=1002.0)
        self.store.save_direct_message("bob", "charlie", "bob to charlie", created_at=1003.0)

        alice_hist = self.store.get_user_history("alice", limit=50)
        self.assertEqual(
            [m["text"] for m in alice_hist],
            ["hello all", "secret to bob", "reply to alice"],
        )
        self.assertTrue(alice_hist[0]["is_broadcast"])
        self.assertFalse(alice_hist[1]["is_broadcast"])

        charlie_hist = self.store.get_user_history("charlie", limit=50)
        self.assertEqual([m["text"] for m in charlie_hist], ["hello all", "bob to charlie"])

        limited = self.store.get_user_history("alice", limit=2)
        self.assertEqual([m["text"] for m in limited], ["secret to bob", "reply to alice"])

    def test_save_and_query_transfer_logs(self) -> None:
        self.store.save_transfer_log(
            session_id="sid-1",
            sender="alice",
            receiver="bob",
            filename="report.pdf",
            filesize=1024,
            sha256="a" * 64,
            status="COMPLETED",
            is_broadcast=False,
            created_at=2000.0,
        )
        self.store.save_transfer_log(
            session_id="sid-2",
            sender="charlie",
            receiver="dave",
            filename="other.zip",
            filesize=2048,
            sha256="b" * 64,
            status="FAILED",
            is_broadcast=True,
            created_at=2001.0,
        )

        bob_logs = self.store.get_transfer_logs("bob")
        self.assertEqual(len(bob_logs), 1)
        self.assertEqual(bob_logs[0]["filename"], "report.pdf")
        self.assertEqual(bob_logs[0]["status"], "COMPLETED")
        self.assertFalse(bob_logs[0]["is_broadcast"])

    def test_server_handlers_persist_and_sync_history(self) -> None:
        set_history_store(self.store)
        sent_to_alice: list[dict] = []
        routed: list[tuple[str, dict]] = []

        def mock_send_to_user(target: str, msg: dict) -> bool:
            if target == "offline_user":
                return False
            routed.append((target, msg))
            return True

        ctx = SimpleNamespace(
            username="alice",
            send=sent_to_alice.append,
            send_to_user=mock_send_to_user,
            list_online=lambda: ["alice", "bob"],
        )

        handle_broadcast_message(ctx, {"type": mt.BROADCAST_MESSAGE, "text": "chào cả nhóm"})
        handle_direct_message(ctx, {"type": mt.DIRECT_MESSAGE, "recipient": "bob", "text": "chào bob"})
        # Offline DM must NOT be persisted
        handle_direct_message(ctx, {"type": mt.DIRECT_MESSAGE, "recipient": "offline_user", "text": "missed"})

        sent_to_alice.clear()
        handle_history_request(ctx, {"type": mt.HISTORY_REQUEST, "limit": 50})
        self.assertEqual(len(sent_to_alice), 1)
        sync_msg = sent_to_alice[0]
        self.assertEqual(sync_msg["type"], mt.HISTORY_SYNC)
        self.assertEqual([m["text"] for m in sync_msg["messages"]], ["chào cả nhóm", "chào bob"])

    def test_client_chat_and_gui_store_history_sync(self) -> None:
        sent_frames: list[dict] = []
        synced: list[tuple[list[dict], list[dict]]] = []

        chat = ClientChat(
            send_message=sent_frames.append,
            on_message=lambda s, t, b: None,
            on_status=lambda ok, m, c, cnt: None,
            on_history=lambda msgs, tfs: synced.append((msgs, tfs)),
        )
        chat.request_history(limit=25)
        self.assertEqual(sent_frames[-1], {"type": mt.HISTORY_REQUEST, "limit": 25})

        chat.handle_message(
            {
                "type": mt.HISTORY_SYNC,
                "messages": [
                    {
                        "sender": "bob",
                        "recipient": "",
                        "text": "tin cũ #all",
                        "is_broadcast": True,
                        "created_at": 1700000000.0,
                    },
                    {
                        "sender": "alice",
                        "recipient": "bob",
                        "text": "tin riêng đã gửi",
                        "is_broadcast": False,
                        "created_at": 1700000060.0,
                    },
                ],
                "transfers": [],
            }
        )
        self.assertEqual(len(synced), 1)

        gui_store = ChatSessionStore()
        gui_store.load_history("alice", synced[0][0])
        self.assertEqual(len(gui_store.conversations[BROADCAST_CONV_ID].messages), 1)
        self.assertEqual(gui_store.conversations[BROADCAST_CONV_ID].unread_count, 0)
        self.assertIn("bob", gui_store.conversations)
        self.assertEqual(gui_store.conversations["bob"].unread_count, 0)
        self.assertTrue(gui_store.conversations["bob"].messages[0].is_outgoing)


if __name__ == "__main__":
    unittest.main()

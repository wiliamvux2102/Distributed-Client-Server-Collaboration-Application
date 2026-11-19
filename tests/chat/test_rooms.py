"""Unit and integration tests for multi-room chat, persistence, and client routing."""

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
from chat.rooms import RoomManager, normalize_room_id
from chat.server_handler import (
    handle_history_request,
    handle_room_create,
    handle_room_join,
    handle_room_leave,
    handle_room_list,
    handle_room_message,
    set_history_store,
)
from common import message_types as mt
from storage.db import HistoryStore
from ui.gui.models import ChatSessionStore


class RoomChatTests(unittest.TestCase):
    """Verify room normalization, server handlers, SQLite persistence, and GUI/ClientChat."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.db_path = self.tmp_dir / "rooms.db"
        self.store = HistoryStore(self.db_path)
        set_history_store(self.store)

    def tearDown(self) -> None:
        set_history_store(None)
        self.store.close()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_normalize_room_id(self) -> None:
        self.assertEqual(normalize_room_id("Nhom_1"), "#nhom_1")
        self.assertEqual(normalize_room_id("#team-a"), "#team-a")
        with self.assertRaises(ValueError):
            normalize_room_id("#all")
        with self.assertRaises(ValueError):
            normalize_room_id("a")
        with self.assertRaises(ValueError):
            normalize_room_id("bad room!")

    def test_room_handlers_and_sqlite_persistence(self) -> None:
        sent_by_user: dict[str, list[dict]] = {"alice": [], "bob": [], "charlie": []}

        def make_ctx(user: str) -> SimpleNamespace:
            return SimpleNamespace(
                username=user,
                send=sent_by_user[user].append,
                send_to_user=lambda target, msg: (sent_by_user[target].append(msg) or True)
                if target in sent_by_user
                else False,
                list_online=lambda: ["alice", "bob", "charlie"],
            )

        ctx_alice = make_ctx("alice")
        ctx_bob = make_ctx("bob")
        ctx_charlie = make_ctx("charlie")

        handle_room_create(ctx_alice, {"type": mt.ROOM_CREATE, "room_id": "nhom1"})
        self.assertTrue(sent_by_user["alice"][-1]["ok"])
        self.assertEqual(sent_by_user["alice"][-1]["room_id"], "#nhom1")

        # Charlie is not a member yet -> rejected
        handle_room_message(ctx_charlie, {"type": mt.ROOM_MESSAGE, "room_id": "#nhom1", "text": "let me in"})
        self.assertFalse(sent_by_user["charlie"][-1]["ok"])
        self.assertEqual(sent_by_user["charlie"][-1]["code"], "NOT_ROOM_MEMBER")

        # Bob joins and Alice sends a message
        handle_room_join(ctx_bob, {"type": mt.ROOM_JOIN, "room_id": "#nhom1"})
        handle_room_message(ctx_alice, {"type": mt.ROOM_MESSAGE, "room_id": "#nhom1", "text": "chào bob ở nhom1"})

        bob_deliveries = [m for m in sent_by_user["bob"] if m.get("type") == mt.ROOM_DELIVERY]
        self.assertEqual(len(bob_deliveries), 1)
        self.assertEqual(bob_deliveries[0]["text"], "chào bob ở nhom1")
        self.assertFalse(any(m.get("type") == mt.ROOM_DELIVERY for m in sent_by_user["charlie"]))

        # History sync includes #nhom1 for Bob, excludes #nhom1 for Charlie
        sent_by_user["bob"].clear()
        sent_by_user["charlie"].clear()
        handle_history_request(ctx_bob, {"type": mt.HISTORY_REQUEST, "limit": 50})
        handle_history_request(ctx_charlie, {"type": mt.HISTORY_REQUEST, "limit": 50})
        self.assertEqual([m["text"] for m in sent_by_user["bob"][0]["messages"]], ["chào bob ở nhom1"])
        self.assertEqual(sent_by_user["charlie"][0]["messages"], [])

        # RoomManager reloads persisted rooms after restart
        reloaded = RoomManager(history_store=self.store)
        self.assertEqual(reloaded.get_members("#nhom1"), ["alice", "bob"])

        # Bob leaves room
        handle_room_leave(ctx_bob, {"type": mt.ROOM_LEAVE, "room_id": "#nhom1"})
        handle_room_list(ctx_alice, {"type": mt.ROOM_LIST})
        rooms = sent_by_user["alice"][-1]["rooms"]
        self.assertEqual(rooms[0]["members"], ["alice"])

    def test_client_chat_and_gui_store_room_routing(self) -> None:
        sent_frames: list[dict] = []
        room_msgs: list[tuple[str, str, str]] = []
        chat = ClientChat(
            send_message=sent_frames.append,
            on_message=lambda s, t, b: None,
            on_status=lambda ok, m, c, cnt: None,
            on_room_message=lambda rid, s, t: room_msgs.append((rid, s, t)),
        )
        chat.join_room("nhom2")
        chat.send_room("#nhom2", "hello room")
        self.assertEqual(sent_frames[-2], {"type": mt.ROOM_JOIN, "room_id": "nhom2"})
        self.assertEqual(sent_frames[-1], {"type": mt.ROOM_MESSAGE, "room_id": "#nhom2", "text": "hello room"})

        chat.handle_message({"type": mt.ROOM_DELIVERY, "room_id": "#nhom2", "sender": "bob", "text": "hi"})
        self.assertEqual(room_msgs, [("#nhom2", "bob", "hi")])

        gui_store = ChatSessionStore()
        gui_store.append_room_incoming("#nhom2", "bob", "hi")
        self.assertIn("#nhom2", gui_store.conversations)
        self.assertTrue(gui_store.conversations["#nhom2"].is_room)
        self.assertEqual(gui_store.conversations["#nhom2"].unread_count, 1)

    def test_room_file_transfer_metadata_and_store_routing(self) -> None:
        from transfer.sender import ClientFileTransferManager, FileSender
        from ui.gui.models import TransferRecord

        sent_frames: list[dict] = []
        sender = FileSender(
            username="alice",
            send_frame_fn=lambda h, p: sent_frames.append(dict(h)),
        )
        sample = self.tmp_dir / "room_doc.txt"
        sample.write_text("hello room file transfer", encoding="utf-8")

        sid = sender.start_transfer("bob", sample, room_id="#nhom1")
        self.assertTrue(sid)
        self.assertEqual(sent_frames[-1]["type"], "FILE_INIT")
        self.assertEqual(sent_frames[-1]["room_id"], "#nhom1")
        self.assertFalse(sent_frames[-1].get("is_broadcast", False))

        # ClientFileTransferManager emits events with room_id
        events: list[dict] = []
        ft_mgr = ClientFileTransferManager(
            username="alice",
            send_fn=sent_frames.append,
            print_fn=lambda _t: None,
            on_event=events.append,
            download_dir=self.tmp_dir / "alice_dl",
        )
        ft_mgr.sender.start_transfer("bob", sample, room_id="#nhom1")
        self.assertTrue(any(e.get("event") == "started" and e.get("room_id") == "#nhom1" for e in events))

        # Receiver manager receives FILE_INIT with room_id
        rx_events: list[dict] = []
        rx_mgr = ClientFileTransferManager(
            username="bob",
            send_fn=lambda _m: None,
            print_fn=lambda _t: None,
            on_event=rx_events.append,
            download_dir=self.tmp_dir / "bob_dl",
        )
        init_frame = dict(sent_frames[-1])
        init_frame.pop("binary_payload_base64", None)
        rx_mgr.handle_incoming(init_frame)
        self.assertTrue(any(e.get("event") == "started" and e.get("room_id") == "#nhom1" for e in rx_events))

        # ChatSessionStore routes room batch upload and room incoming file into #nhom1
        store = ChatSessionStore()
        rec_upload = TransferRecord(
            session_id="room-batch-session-1",
            direction="upload",
            peer="bob",
            filename="room_doc.txt",
            total_bytes=100,
            current_bytes=100,
            state="COMPLETED",
            room_id="#nhom1",
        )
        msg_up, _ = store.upsert_broadcast_batch_file(
            username="alice",
            batch_id="b-room-1",
            batch_total=1,
            rec=rec_upload,
            conv_id="#nhom1",
        )
        self.assertIn("#nhom1", store.conversations)
        self.assertTrue(store.conversations["#nhom1"].is_room)
        self.assertEqual(len(store.conversations["#nhom1"].messages), 1)
        self.assertEqual(store.conversations["#nhom1"].messages[0].text, "room_doc.txt")
        self.assertIn("thành viên #nhom1", msg_up.status_note)

        rec_download = TransferRecord(
            session_id="room-rx-session-1",
            direction="download",
            peer="alice",
            filename="room_doc.txt",
            total_bytes=100,
            current_bytes=100,
            state="COMPLETED",
            saved_path=str(self.tmp_dir / "room_doc.txt"),
            room_id="#nhom1",
        )
        msg_rx, _ = store.upsert_file_message(
            conv_id="#nhom1",
            username="bob",
            rec=rec_download,
        )
        self.assertEqual(len(store.conversations["#nhom1"].messages), 2)
        self.assertEqual(msg_rx.sender, "alice")
        self.assertFalse(msg_rx.is_outgoing)

    def test_join_room_pushes_room_history_immediately(self) -> None:
        """Joining an existing room pushes HISTORY_SYNC for that room right after ROOM_STATUS."""
        alice_sent: list[dict] = []
        bob_sent: list[dict] = []
        alice_ctx = SimpleNamespace(username="alice", send=alice_sent.append)
        bob_ctx = SimpleNamespace(username="bob", send=bob_sent.append)

        handle_room_create(alice_ctx, {"type": mt.ROOM_CREATE, "room_id": "dev"})
        self.store.save_room_message("#dev", "alice", "tin nhan cu 1")
        self.store.save_room_message("#dev", "alice", "tin nhan cu 2")

        handle_room_join(bob_ctx, {"type": mt.ROOM_JOIN, "room_id": "dev"})

        self.assertEqual(len(bob_sent), 2)
        self.assertEqual(bob_sent[0]["type"], mt.ROOM_STATUS)
        self.assertTrue(bob_sent[0]["ok"])
        self.assertEqual(bob_sent[1]["type"], mt.HISTORY_SYNC)
        texts = [m["text"] for m in bob_sent[1]["messages"]]
        self.assertEqual(texts, ["tin nhan cu 1", "tin nhan cu 2"])
        self.assertTrue(all(m["conv_id"] == "#dev" for m in bob_sent[1]["messages"]))


if __name__ == "__main__":
    unittest.main()

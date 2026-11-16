"""
Unit tests for transfer/sender.py.
"""

from __future__ import annotations
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from transfer.sender import FileSender, ClientFileTransferManager
from transfer.integrity import calculate_sha256


class TestSender(unittest.TestCase):
    """Test suite for FileSender and ClientFileTransferManager."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.file_path = self.test_dir / "upload.txt"
        self.content = b"Sender test file content with multiple chunks."
        self.file_path.write_bytes(self.content)

        self.sent_frames = []
        self.sender = FileSender(
            username="alice",
            send_frame_fn=lambda h, p: self.sent_frames.append((h, p)),
            chunk_size=10,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_start_transfer_dispatches_file_init_frame(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        self.assertIsInstance(session_id, str)
        self.assertEqual(len(self.sent_frames), 1)

        init_header, payload = self.sent_frames[0]
        self.assertEqual(init_header["type"], "FILE_INIT")
        self.assertEqual(init_header["sender"], "alice")
        self.assertEqual(init_header["receiver"], "bob")
        self.assertEqual(init_header["filename"], "upload.txt")
        self.assertEqual(init_header["filesize"], len(self.content))
        self.assertEqual(init_header["sha256"], calculate_sha256(self.file_path))
        self.assertEqual(payload, b"")

    def test_send_file_alias_matches_start_transfer(self) -> None:
        session_id = self.sender.send_file("bob", self.file_path)
        self.assertIsInstance(session_id, str)
        self.assertEqual(self.sent_frames[0][0]["type"], "FILE_INIT")

    def test_start_transfer_missing_file_raises_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            self.sender.start_transfer("bob", self.test_dir / "non_existent.bin")

    def test_file_accept_triggers_background_chunk_streaming(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        self.sent_frames.clear()

        # Simulate receiving FILE_ACCEPT from receiver
        self.sender.handle_incoming_message({"type": "FILE_ACCEPT", "session_id": session_id})

        # Wait for worker thread to stream chunks and FILE_END
        start = time.time()
        while time.time() - start < 3.0:
            types = [h["type"] for h, p in self.sent_frames]
            if "FILE_END" in types:
                break
            time.sleep(0.02)

        types = [h["type"] for h, p in self.sent_frames]
        self.assertIn("FILE_CHUNK", types)
        self.assertIn("FILE_END", types)

    def test_file_accept_zero_byte_file_sends_file_end(self) -> None:
        empty_file = self.test_dir / "empty.txt"
        empty_file.write_bytes(b"")
        session_id = self.sender.start_transfer("bob", empty_file)
        self.sent_frames.clear()

        self.sender.handle_incoming_message({"type": "FILE_ACCEPT", "session_id": session_id})
        start = time.time()
        while time.time() - start < 3.0:
            types = [h["type"] for h, p in self.sent_frames]
            if "FILE_END" in types:
                break
            time.sleep(0.02)

        types = [h["type"] for h, p in self.sent_frames]
        self.assertNotIn("FILE_CHUNK", types)
        self.assertIn("FILE_END", types)

    def test_cancel_transfer_halts_streaming_and_sends_file_error(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        self.sender.cancel_transfer(session_id, "User requested cancel")
        self.assertEqual(len(self.sent_frames), 2)  # FILE_INIT + FILE_ERROR
        self.assertEqual(self.sent_frames[-1][0]["type"], "FILE_ERROR")

    def test_file_result_ok_triggers_completion_callback(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        completed = []
        self.sender.on_complete = lambda sid, fn, msg: completed.append((sid, fn))

        self.sender.handle_incoming_message({"type": "FILE_RESULT", "session_id": session_id, "status": "OK"})
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0][0], session_id)

    def test_file_result_failed_triggers_error_callback(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        errors = []
        self.sender.on_error = lambda sid, fn, err: errors.append(err)

        self.sender.handle_incoming_message(
            {"type": "FILE_RESULT", "session_id": session_id, "status": "FAILED", "reason": "Hash mismatch"}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Hash mismatch", errors[0])

    def test_file_error_halts_session_and_triggers_error_callback(self) -> None:
        session_id = self.sender.start_transfer("bob", self.file_path)
        errors = []
        self.sender.on_error = lambda sid, fn, err: errors.append(err)

        self.sender.handle_incoming_message(
            {"type": "FILE_ERROR", "session_id": session_id, "reason": "Connection dropped"}
        )
        self.assertEqual(len(errors), 1)
        self.assertIn("Connection dropped", errors[0])

    def test_client_transfer_manager_dispatch_and_send(self) -> None:
        messages_sent = []
        ui_prints = []
        manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: messages_sent.append(msg),
            print_fn=lambda text: ui_prints.append(text),
        )

        # Test invalid file path
        manager.send_file("bob", self.test_dir / "missing.txt")
        self.assertTrue(any("not found" in p.lower() or "không tồn tại" in p.lower() for p in ui_prints))

        # Test valid file path sends FILE_INIT
        ui_prints.clear()
        manager.send_file("bob", self.file_path)
        self.assertTrue(any(m.get("type") == "FILE_INIT" for m in messages_sent))
        self.assertFalse(any("error" in p.lower() for p in ui_prints), f"Unexpected error printed: {ui_prints}")
        status = manager.get_active_status()
        self.assertIsNotNone(status)
        self.assertIn("upload.txt", status)

    def test_client_transfer_manager_send_file_with_quoted_path(self) -> None:
        messages_sent = []
        ui_prints = []
        manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: messages_sent.append(msg),
            print_fn=lambda text: ui_prints.append(text),
        )

        quoted_path = f'"{self.file_path}"'
        manager.send_file("bob", quoted_path)
        self.assertEqual(len(messages_sent), 1)
        self.assertEqual(messages_sent[0]["type"], "FILE_INIT")
        self.assertFalse(any("error" in p.lower() for p in ui_prints), f"Unexpected error printed: {ui_prints}")

    def test_client_file_transfer_manager_full_streaming_e2e(self) -> None:
        """Test full ClientFileTransferManager transfer flow with chunks, progress, and completion."""
        alice_prints = []
        bob_prints = []
        clients = {}

        def send_network(source_user: str, message: dict) -> None:
            target = "bob" if source_user == "alice" else "alice"
            clients[target].handle_incoming(dict(message))

        alice_manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: send_network("alice", msg),
            print_fn=lambda text: alice_prints.append(text),
        )
        bob_manager = ClientFileTransferManager(
            username="bob",
            send_fn=lambda msg: send_network("bob", msg),
            print_fn=lambda text: bob_prints.append(text),
        )
        bob_manager.receiver.download_dir = self.test_dir / "downloads"
        bob_manager.receiver.download_dir.mkdir(exist_ok=True)

        clients["alice"] = alice_manager
        clients["bob"] = bob_manager

        # Alice sends file to Bob
        alice_manager.send_file("bob", self.file_path)

        # Wait for background streaming and verification to complete
        start = time.time()
        while time.time() - start < 3.0:
            if any("FILE DONE" in p for p in alice_prints) and any("FILE DONE" in p for p in bob_prints):
                break
            time.sleep(0.02)

        # Assert no errors occurred anywhere in the process
        self.assertFalse(any("FILE ERROR" in p for p in alice_prints), f"Alice had errors: {alice_prints}")
        self.assertFalse(any("FILE ERROR" in p for p in bob_prints), f"Bob had errors: {bob_prints}")

        # Assert successful completion
        self.assertTrue(any("FILE DONE" in p for p in alice_prints))
        self.assertTrue(any("FILE DONE" in p for p in bob_prints))

        # Verify saved file content matches
        saved_file = bob_manager.receiver.download_dir / "upload.txt"
        self.assertTrue(saved_file.exists())
        self.assertEqual(saved_file.read_bytes(), self.content)

    def test_client_transfer_manager_handles_file_error_for_sender(self) -> None:
        messages_sent = []
        ui_prints = []
        manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: messages_sent.append(msg),
            print_fn=lambda text: ui_prints.append(text),
        )

        # Start transfer
        manager.send_file("bob", self.file_path)
        session_id = messages_sent[0]["session_id"]
        self.assertIsNotNone(manager.get_active_status())

        # Simulate receiving FILE_ERROR (e.g. recipient offline or error)
        manager.handle_incoming({
            "type": "FILE_ERROR",
            "session_id": session_id,
            "code": "ERR_USER_OFFLINE",
            "reason": "User 'bob' is currently offline.",
        })

        # Must report error to UI and clear active transfer
        self.assertTrue(any("offline" in p.lower() or "FILE ERROR" in p for p in ui_prints))
        self.assertIsNone(manager.get_active_status())

    def test_client_transfer_manager_cleanup_on_disconnect(self) -> None:
        """Test that cleanup() clears active transfers and deletes temporary files."""
        manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: None,
            print_fn=lambda text: None,
        )
        manager.receiver.download_dir = self.test_dir / "downloads"
        manager.receiver.download_dir.mkdir(exist_ok=True)

        # Simulate incoming transfer initializing a .part file
        manager.handle_incoming({
            "type": "FILE_INIT",
            "session_id": "sid_cleanup",
            "sender": "bob",
            "filename": "cleanup.bin",
            "filesize": 1024,
            "sha256": "0" * 64,
            "total_chunks": 1,
        })

        temp_part = manager.receiver.download_dir / ".sid_cleanup_cleanup.bin.part"
        self.assertTrue(temp_part.exists())

        # Disconnect triggers cleanup
        manager.cleanup("Client disconnected")

        # Must delete temp file and clear status
        self.assertFalse(temp_part.exists())
        self.assertIsNone(manager.get_active_status())

    def test_single_line_progress_bar_without_multiline_chat_spam(self) -> None:
        """Verify Option A: multi-chunk transfer updates single-line status bar and prints only 1 final line."""
        large_file = self.test_dir / "report.pdf"
        large_file.write_bytes(b"X" * (256 * 1024))  # 256 KB = 4 chunks of 64 KB

        alice_prints: list[str] = []
        bob_prints: list[str] = []
        alice_status_snapshots: list[str] = []
        bob_status_snapshots: list[str] = []
        clients: dict[str, ClientFileTransferManager] = {}

        def send_network(source_user: str, message: dict) -> None:
            target = "bob" if source_user == "alice" else "alice"
            clients[target].handle_incoming(dict(message))

        alice_manager = ClientFileTransferManager(
            username="alice",
            send_fn=lambda msg: send_network("alice", msg),
            print_fn=alice_prints.append,
            status_update_fn=lambda: (
                alice_status_snapshots.append(s)
                if (s := alice_manager.get_active_status()) is not None
                else None
            ),
        )
        bob_manager = ClientFileTransferManager(
            username="bob",
            send_fn=lambda msg: send_network("bob", msg),
            print_fn=bob_prints.append,
            status_update_fn=lambda: (
                bob_status_snapshots.append(s)
                if (s := bob_manager.get_active_status()) is not None
                else None
            ),
        )
        bob_manager.receiver.download_dir = self.test_dir / "downloads"
        bob_manager.receiver.download_dir.mkdir(exist_ok=True)

        clients["alice"] = alice_manager
        clients["bob"] = bob_manager

        alice_manager.send_file("bob", large_file)

        start = time.time()
        while time.time() - start < 3.0:
            if alice_prints and bob_prints:
                break
            time.sleep(0.02)

        # Exactly 1 line printed to chat log on sender and receiver (no 25%, 50%, 75% spam)
        self.assertEqual(len(alice_prints), 1, f"Expected 1 print on sender, got: {alice_prints}")
        self.assertEqual(len(bob_prints), 1, f"Expected 1 print on receiver, got: {bob_prints}")
        self.assertIn("[FILE DONE]", alice_prints[0])
        self.assertIn("100%", alice_prints[0])
        self.assertIn("[FILE DONE]", bob_prints[0])
        self.assertIn("100%", bob_prints[0])

        # Live single-line toolbar snapshots were captured during chunk streaming (25%, 50%, 75%, etc.)
        self.assertTrue(any("25.0%" in s or "50.0%" in s or "75.0%" in s for s in alice_status_snapshots))
        self.assertTrue(any("25.0%" in s or "50.0%" in s or "75.0%" in s for s in bob_status_snapshots))

        # Toolbar clears automatically after completion
        self.assertIsNone(alice_manager.get_active_status())
        self.assertIsNone(bob_manager.get_active_status())

    def test_duplicate_file_accept_starts_only_one_upload_worker(self) -> None:
        """Receiving FILE_ACCEPT twice for the same session must not launch duplicate workers."""
        from unittest.mock import patch

        spawned: list[str] = []

        def fake_worker(self_obj, session) -> None:
            spawned.append(session.session_id)

        sender = FileSender(username="alice", send_frame_fn=lambda h, p=b"": None, chunk_size=16)
        sid = sender.start_transfer("bob", self.file_path)

        with patch.object(FileSender, "_send_chunks_worker", fake_worker):
            sender.handle_incoming_message({"type": "FILE_ACCEPT", "session_id": sid}, b"")
            sender.handle_incoming_message({"type": "FILE_ACCEPT", "session_id": sid}, b"")
            time.sleep(0.05)

        self.assertEqual(spawned, [sid])

    def test_send_file_rejects_empty_or_whitespace_path(self) -> None:
        """Empty or whitespace paths must error without opening the working directory."""
        errors: list[str] = []
        sent: list[dict] = []
        mgr = ClientFileTransferManager(
            username="alice",
            send_fn=sent.append,
            print_fn=errors.append,
        )
        mgr.send_file("bob", "   ")
        self.assertEqual(sent, [])
        self.assertTrue(any("cannot be empty" in line.lower() for line in errors))

    def test_corrupt_base64_chunk_aborts_only_affected_session_and_notifies_error(self) -> None:
        """Invalid base64 chunk aborts only that session via _on_error while keeping other downloads intact."""
        import base64
        import hashlib

        alice_out: list[dict] = []
        events: list[dict] = []
        prints: list[str] = []

        mgr = ClientFileTransferManager(
            username="bob",
            send_fn=alice_out.append,
            print_fn=prints.append,
            on_event=events.append,
        )
        mgr.receiver.download_dir = self.test_dir / "dl"
        mgr.receiver.download_dir.mkdir(exist_ok=True)

        good_payload = b"hello-world"
        good_sha = hashlib.sha256(good_payload).hexdigest()

        try:
            mgr.handle_incoming(
                {
                    "type": "FILE_INIT",
                    "session_id": "sid_bad_b64",
                    "sender": "alice",
                    "receiver": "bob",
                    "filename": "bad.bin",
                    "filesize": 5,
                    "sha256": "0" * 64,
                    "total_chunks": 1,
                }
            )
            mgr.handle_incoming(
                {
                    "type": "FILE_INIT",
                    "session_id": "sid_good",
                    "sender": "carol",
                    "receiver": "bob",
                    "filename": "good.bin",
                    "filesize": len(good_payload),
                    "sha256": good_sha,
                    "total_chunks": 1,
                }
            )

            # Corrupt base64 on sid_bad_b64 only
            mgr.handle_incoming(
                {
                    "type": "FILE_CHUNK",
                    "session_id": "sid_bad_b64",
                    "chunk_index": 0,
                    "binary_payload_base64": "!!!not-valid-base64!!!",
                }
            )

            error_events = [
                e for e in events if e.get("event") == "failed" and e.get("session_id") == "sid_bad_b64"
            ]
            self.assertEqual(len(error_events), 1)
            self.assertEqual(error_events[0].get("direction"), "download")
            self.assertEqual(error_events[0].get("peer"), "alice")
            self.assertTrue(
                any(m.get("type") == "FILE_ERROR" and m.get("session_id") == "sid_bad_b64" for m in alice_out)
            )

            # Second download session still completes cleanly
            mgr.handle_incoming(
                {
                    "type": "FILE_CHUNK",
                    "session_id": "sid_good",
                    "chunk_index": 0,
                    "binary_payload_base64": base64.b64encode(good_payload).decode("ascii"),
                }
            )
            mgr.handle_incoming({"type": "FILE_END", "session_id": "sid_good"})
            self.assertEqual((mgr.receiver.download_dir / "good.bin").read_bytes(), good_payload)
        finally:
            mgr.cleanup("test cleanup")

    def test_file_error_on_download_preserves_direction_and_peer_metadata(self) -> None:
        """FILE_ERROR on an active download must preserve direction='download' and sender peer in UI event."""
        events: list[dict] = []
        mgr = ClientFileTransferManager(
            username="bob",
            send_fn=lambda m: None,
            print_fn=lambda s: None,
            on_event=events.append,
        )
        mgr.receiver.download_dir = self.test_dir / "dl2"
        mgr.receiver.download_dir.mkdir(exist_ok=True)

        try:
            mgr.handle_incoming(
                {
                    "type": "FILE_INIT",
                    "session_id": "sid_err_meta",
                    "sender": "alice",
                    "receiver": "bob",
                    "filename": "report.pdf",
                    "filesize": 100,
                    "sha256": "a" * 64,
                    "total_chunks": 1,
                }
            )
            mgr.handle_incoming(
                {
                    "type": "FILE_ERROR",
                    "session_id": "sid_err_meta",
                    "code": "ERR_PEER_DISCONNECTED",
                    "reason": "Peer offline",
                }
            )

            err_ev = next(e for e in events if e.get("event") == "failed")
            self.assertEqual(err_ev["session_id"], "sid_err_meta")
            self.assertEqual(err_ev["direction"], "download")
            self.assertEqual(err_ev["peer"], "alice")
        finally:
            mgr.cleanup("test cleanup")


if __name__ == "__main__":
    unittest.main()

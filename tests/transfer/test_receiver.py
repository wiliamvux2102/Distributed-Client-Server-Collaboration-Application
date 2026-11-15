"""
Unit tests for transfer/receiver.py.
"""

from __future__ import annotations
import hashlib
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from transfer.receiver import FileReceiver


class TestReceiver(unittest.TestCase):
    """Test suite for FileReceiver component."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.download_dir = self.test_dir / "downloads"
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.sent_frames = []
        self.receiver = FileReceiver(
            username="bob",
            send_frame_fn=lambda h, p: self.sent_frames.append((h, p)),
            download_dir=self.download_dir,
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_file_init_creates_temp_part_file_and_sends_accept(self) -> None:
        session_id = "test_sid_123"
        content = b"Receiver test content."
        sha = hashlib.sha256(content).hexdigest()

        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "recv.txt",
            "filesize": len(content),
            "sha256": sha,
            "total_chunks": 1,
        }

        self.receiver.handle_incoming_message(header, b"")

        # Must send FILE_ACCEPT
        self.assertEqual(len(self.sent_frames), 1)
        self.assertEqual(self.sent_frames[0][0]["type"], "FILE_ACCEPT")
        self.assertEqual(self.sent_frames[0][0]["session_id"], session_id)

        # Temporary .part file must exist on disk
        temp_file = self.download_dir / f".{session_id}_recv.txt.part"
        self.assertTrue(temp_file.exists())

        # Cleanup session
        self.receiver.handle_incoming_message({"type": "FILE_ERROR", "session_id": session_id}, b"")

    def test_file_init_sanitizes_path_traversal(self) -> None:
        session_id = "traversal_sid"
        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "attacker",
            "filename": "../../etc/passwd",
            "filesize": 10,
            "sha256": "0" * 64,
            "total_chunks": 1,
        }

        self.receiver.handle_incoming_message(header, b"")

        # Temp file must be located inside download_dir with basename only
        expected_temp = self.download_dir / f".{session_id}_passwd.part"
        self.assertTrue(expected_temp.exists())

        # Cleanup session
        self.receiver.handle_incoming_message({"type": "FILE_ERROR", "session_id": session_id}, b"")

    def test_empty_file_creates_final_file_immediately(self) -> None:
        session_id = "empty_sid"
        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "empty.txt",
            "filesize": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
            "total_chunks": 0,
        }

        self.receiver.handle_incoming_message(header, b"")
        final_file = self.download_dir / "empty.txt"
        self.assertTrue(final_file.exists())
        self.assertEqual(final_file.stat().st_size, 0)

        # Should have sent FILE_RESULT OK
        types = [h["type"] for h, p in self.sent_frames]
        self.assertIn("FILE_RESULT", types)

    def test_chunk_out_of_order_aborts_and_deletes_temp_file(self) -> None:
        session_id = "ooo_sid"
        content = b"1234567890"
        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "out_of_order.txt",
            "filesize": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "total_chunks": 2,
        }

        self.receiver.handle_incoming_message(header, b"")
        temp_file = self.download_dir / f".{session_id}_out_of_order.txt.part"
        self.assertTrue(temp_file.exists())

        # Receive chunk index 1 first (expected 0)
        self.sent_frames.clear()
        self.receiver.handle_incoming_message(
            {"type": "FILE_CHUNK", "session_id": session_id, "chunk_index": 1},
            b"67890",
        )

        # Temp file must be deleted upon sequence error
        self.assertFalse(temp_file.exists())
        self.assertTrue(any(h.get("type") == "FILE_ERROR" for h, p in self.sent_frames))

    def test_file_end_success_renames_to_final_file(self) -> None:
        session_id = "ok_sid"
        content = b"Successful payload."
        sha = hashlib.sha256(content).hexdigest()

        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "document.txt",
            "filesize": len(content),
            "sha256": sha,
            "total_chunks": 1,
        }

        self.receiver.handle_incoming_message(header, b"")
        self.receiver.handle_incoming_message(
            {"type": "FILE_CHUNK", "session_id": session_id, "chunk_index": 0},
            content,
        )

        self.sent_frames.clear()
        self.receiver.handle_incoming_message({"type": "FILE_END", "session_id": session_id}, b"")

        # Result OK sent
        self.assertEqual(len(self.sent_frames), 1)
        self.assertEqual(self.sent_frames[0][0]["status"], "OK")

        # Final file exists, temp file gone
        final_file = self.download_dir / "document.txt"
        self.assertTrue(final_file.exists())
        self.assertEqual(final_file.read_bytes(), content)
        temp_file = self.download_dir / f".{session_id}_document.txt.part"
        self.assertFalse(temp_file.exists())

    def test_file_end_hash_mismatch_deletes_temp_file(self) -> None:
        session_id = "bad_hash_sid"
        content = b"corrupted data"
        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "corrupted.txt",
            "filesize": len(content),
            "sha256": "0" * 64,  # incorrect hash
            "total_chunks": 1,
        }

        self.receiver.handle_incoming_message(header, b"")
        self.receiver.handle_incoming_message(
            {"type": "FILE_CHUNK", "session_id": session_id, "chunk_index": 0},
            content,
        )

        self.sent_frames.clear()
        self.receiver.handle_incoming_message({"type": "FILE_END", "session_id": session_id}, b"")

        # Must send FILE_RESULT FAILED
        self.assertEqual(len(self.sent_frames), 1)
        self.assertEqual(self.sent_frames[0][0]["status"], "FAILED")

        # Files must be cleaned up
        self.assertFalse((self.download_dir / "corrupted.txt").exists())
        self.assertFalse((self.download_dir / f".{session_id}_corrupted.txt.part").exists())

    def test_duplicate_filename_resolves_with_counter(self) -> None:
        existing_file = self.download_dir / "report.pdf"
        existing_file.write_bytes(b"existing content")

        resolved = self.receiver._resolve_final_path("report.pdf")
        self.assertEqual(resolved.name, "report (1).pdf")

        # If report (1).pdf also exists
        (self.download_dir / "report (1).pdf").write_bytes(b"first duplicate")
        resolved_second = self.receiver._resolve_final_path("report.pdf")
        self.assertEqual(resolved_second.name, "report (2).pdf")

    def test_peer_error_aborts_and_deletes_temp_file(self) -> None:
        session_id = "peer_err_sid"
        header = {
            "type": "FILE_INIT",
            "session_id": session_id,
            "sender": "alice",
            "filename": "cancelled.txt",
            "filesize": 100,
            "sha256": "0" * 64,
            "total_chunks": 2,
        }

        self.receiver.handle_incoming_message(header, b"")
        temp_file = self.download_dir / f".{session_id}_cancelled.txt.part"
        self.assertTrue(temp_file.exists())

        # Sender aborts
        self.receiver.handle_incoming_message(
            {"type": "FILE_ERROR", "session_id": session_id, "reason": "Sender disconnected"},
            b"",
        )
        self.assertFalse(temp_file.exists())

    def test_abort_all_deletes_all_active_temp_files(self) -> None:
        """Test that calling abort_all() deletes all active temporary .part files."""
        # Initialize two download sessions
        for i, sid in enumerate(["sid_1", "sid_2"]):
            header = {
                "type": "FILE_INIT",
                "session_id": sid,
                "sender": "alice",
                "filename": f"file_{i}.txt",
                "filesize": 100,
                "sha256": "0" * 64,
                "total_chunks": 2,
            }
            self.receiver.handle_incoming_message(header, b"")

        # Verify temp files exist
        temp_1 = self.download_dir / ".sid_1_file_0.txt.part"
        temp_2 = self.download_dir / ".sid_2_file_1.txt.part"
        self.assertTrue(temp_1.exists())
        self.assertTrue(temp_2.exists())

        # Disconnect client -> abort_all
        self.receiver.abort_all("Client disconnected")

        # Verify both temp files are deleted
        self.assertFalse(temp_1.exists())
        self.assertFalse(temp_2.exists())
        self.assertEqual(len(self.receiver._sessions), 0)

    def test_clean_stale_temp_files_on_init(self) -> None:
        """Test that stale .part files from previously crashed sessions are deleted upon startup."""
        stale_file_1 = self.download_dir / ".old_crash_1.txt.part"
        stale_file_2 = self.download_dir / "old_crash_2.part"
        stale_file_1.write_bytes(b"garbage data from crash")
        stale_file_2.write_bytes(b"garbage data 2")

        self.assertTrue(stale_file_1.exists())
        self.assertTrue(stale_file_2.exists())

        # Starting a new FileReceiver must sweep stale files
        fresh_receiver = FileReceiver(
            username="bob",
            send_frame_fn=lambda h, p: None,
            download_dir=self.download_dir,
        )

        self.assertFalse(stale_file_1.exists())
        self.assertFalse(stale_file_2.exists())

    def test_file_init_rejects_traversal_in_session_id(self) -> None:
        """Unsafe session_id containing path separators or '..' must be rejected."""
        for bad_sid in ("../escape", "sub/sid", "sub\\sid", "bad:sid"):
            self.receiver.handle_incoming_message(
                {
                    "type": "FILE_INIT",
                    "session_id": bad_sid,
                    "sender": "attacker",
                    "filename": "normal.txt",
                    "filesize": 10,
                    "sha256": "0" * 64,
                    "total_chunks": 1,
                },
                b"",
            )
        self.assertEqual(len(self.sent_frames), 0)
        self.assertEqual(len(self.receiver._sessions), 0)

    def test_file_init_sanitizes_backslash_and_dot_filenames(self) -> None:
        """Backslash paths and dot/empty filenames must resolve safely inside download_dir."""
        self.receiver.handle_incoming_message(
            {
                "type": "FILE_INIT",
                "session_id": "sid_bs",
                "sender": "attacker",
                "filename": "..\\..\\win.ini",
                "filesize": 10,
                "sha256": "0" * 64,
                "total_chunks": 1,
            },
            b"",
        )
        self.assertTrue((self.download_dir / ".sid_bs_win.ini.part").exists())
        self.receiver.handle_incoming_message({"type": "FILE_ERROR", "session_id": "sid_bs"}, b"")

        for idx, dot_name in enumerate(("..", ".", "")):
            sid = f"sid_dot_{idx}"
            self.receiver.handle_incoming_message(
                {
                    "type": "FILE_INIT",
                    "session_id": sid,
                    "sender": "attacker",
                    "filename": dot_name,
                    "filesize": 10,
                    "sha256": "0" * 64,
                    "total_chunks": 1,
                },
                b"",
            )
            self.assertTrue((self.download_dir / f".{sid}_unnamed_file.part").exists())
            self.receiver.handle_incoming_message({"type": "FILE_ERROR", "session_id": sid}, b"")


if __name__ == "__main__":
    unittest.main()

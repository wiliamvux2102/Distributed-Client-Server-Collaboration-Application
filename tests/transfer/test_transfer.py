"""
Integration tests for file transfer covering the 5 mandatory scenarios from PLAN_M3.md.
"""

from __future__ import annotations
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from transfer.sender import FileSender
from transfer.receiver import FileReceiver
from transfer.server_handler import FileTransferServerHandler


class TestTransferEndToEnd(unittest.TestCase):
    """End-to-end integration tests for Member 3 transfer scenarios."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.source_dir = self.test_dir / "source"
        self.download_dir = self.test_dir / "downloads"
        self.source_dir.mkdir(parents=True, exist_ok=True)
        self.download_dir.mkdir(parents=True, exist_ok=True)

        self.clients = {}
        self.completed = []
        self.errors = []

        def mock_send_to_user(target_user: str, header: dict, payload: bytes) -> bool:
            if target_user not in self.clients:
                return False
            self.clients[target_user].handle_incoming_message(header, payload)
            return True

        self.server_handler = FileTransferServerHandler(
            send_to_user_fn=mock_send_to_user,
            is_user_online_fn=lambda u: u in self.clients,
        )

        self.sender = FileSender(
            username="alice",
            send_frame_fn=lambda h, p: self.server_handler.handle_message("alice", h, p),
            chunk_size=1024,
            on_complete=lambda sid, fn, msg: self.completed.append(("sender", fn)),
            on_error=lambda sid, fn, err: self.errors.append(("sender", err)),
        )

        self.receiver = FileReceiver(
            username="bob",
            send_frame_fn=lambda h, p: self.server_handler.handle_message("bob", h, p),
            download_dir=self.download_dir,
            on_complete=lambda sid, fn, path: self.completed.append(("receiver", fn, path)),
            on_error=lambda sid, fn, err: self.errors.append(("receiver", err)),
        )

        self.clients["alice"] = self.sender
        self.clients["bob"] = self.receiver

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def _wait_for_completion(self, count: int = 2, timeout: float = 3.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if len(self.completed) >= count:
                return True
            time.sleep(0.02)
        return False

    def test_scenario_1_empty_file_transfers_successfully(self) -> None:
        """Scenario 1: Zero-byte file creates destination file without divide-by-zero errors."""
        empty_file = self.source_dir / "empty.txt"
        empty_file.write_bytes(b"")

        self.sender.start_transfer("bob", empty_file)
        self.assertTrue(self._wait_for_completion(2), "Empty file transfer timed out")

        target = self.download_dir / "empty.txt"
        self.assertTrue(target.exists())
        self.assertEqual(target.stat().st_size, 0)

    def test_scenario_2_small_single_chunk_file_transfers_successfully(self) -> None:
        """Scenario 2: Small file (< 10KB) transfers completely within a single chunk."""
        small_file = self.source_dir / "small.txt"
        content = b"Small file payload test."
        small_file.write_bytes(content)

        self.sender.start_transfer("bob", small_file)
        self.assertTrue(self._wait_for_completion(2), "Small file transfer timed out")

        target = self.download_dir / "small.txt"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), content)

    def test_scenario_3_multi_chunk_large_file_transfers_cleanly(self) -> None:
        """Scenario 3: Multi-chunk data streams thousands of bytes across multiple chunk frames."""
        large_file = self.source_dir / "large.dat"
        content = os.urandom(64 * 1024)  # 64 KB with 1KB chunk_size = 64 chunks
        large_file.write_bytes(content)

        self.sender.start_transfer("bob", large_file)
        self.assertTrue(self._wait_for_completion(2, timeout=5.0), "Large file transfer timed out")

        target = self.download_dir / "large.dat"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), content)

    def test_scenario_4_binary_data_transfers_without_corruption(self) -> None:
        """Scenario 4: Raw binary byte values (0-255) transfer without UTF-8 decode issues."""
        binary_file = self.source_dir / "binary.bin"
        content = bytes(range(256)) * 20  # 5,120 bytes of non-ASCII binary data
        binary_file.write_bytes(content)

        self.sender.start_transfer("bob", binary_file)
        self.assertTrue(self._wait_for_completion(2), "Binary file transfer timed out")

        target = self.download_dir / "binary.bin"
        self.assertTrue(target.exists())
        self.assertEqual(target.read_bytes(), content)

    def test_scenario_5_corrupted_chunk_detected_and_temp_file_deleted(self) -> None:
        """Scenario 5A: Corrupted chunk payload fails SHA-256 integrity and deletes .part file."""
        test_file = self.source_dir / "corrupted_test.txt"
        test_file.write_bytes(b"Original content that will be tampered.")

        # Tamper payload on the fly in transit
        def tampering_send(target_user: str, header: dict, payload: bytes) -> bool:
            if header.get("type") == "FILE_CHUNK":
                # Invert bytes in chunk
                payload = b"TAMPERED_BYTES"
            self.clients[target_user].handle_incoming_message(header, payload)
            return True

        self.server_handler.send_to_user = tampering_send

        self.sender.start_transfer("bob", test_file)
        start = time.time()
        while time.time() - start < 3.0:
            if any("failed" in err.lower() or "mismatch" in err.lower() for _, err in self.errors):
                break
            time.sleep(0.02)

        # Receiver should report error and no final or temp file should exist
        self.assertTrue(any("failed" in err.lower() or "mismatch" in err.lower() for _, err in self.errors))
        self.assertFalse((self.download_dir / "corrupted_test.txt").exists())
        self.assertFalse(any(p.name.endswith(".part") for p in self.download_dir.glob(".*.part")))

    def test_scenario_5_abrupt_disconnect_cleans_up_receiver_temp_file(self) -> None:
        """Scenario 5B: Unexpected client disconnect cleans active session and deletes .part file."""
        test_file = self.source_dir / "disconnect_test.txt"
        test_file.write_bytes(b"A" * 5000)

        # Intercept after FILE_ACCEPT to simulate disconnect halfway
        original_send = self.server_handler.send_to_user

        def disconnect_after_first_chunk(target_user: str, header: dict, payload: bytes) -> bool:
            if header.get("type") == "FILE_CHUNK":
                # Alice abruptly disconnects
                self.server_handler.on_client_disconnect("alice")
                return False
            return original_send(target_user, header, payload)

        self.server_handler.send_to_user = disconnect_after_first_chunk

        self.sender.start_transfer("bob", test_file)
        start = time.time()
        while time.time() - start < 3.0:
            if not any(p.name.endswith(".part") for p in self.download_dir.glob(".*.part")):
                break
            time.sleep(0.02)

        # Receiver should have received ERR_PEER_DISCONNECTED and deleted .part file
        self.assertFalse((self.download_dir / "disconnect_test.txt").exists())
        self.assertFalse(any(p.name.endswith(".part") for p in self.download_dir.glob(".*.part")))


if __name__ == "__main__":
    unittest.main()

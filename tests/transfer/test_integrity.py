"""
Unit tests for transfer/integrity.py.
"""

from __future__ import annotations
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from transfer.integrity import calculate_sha256, get_file_metadata, verify_file


class TestIntegrity(unittest.TestCase):
    """Test suite for hashing, metadata extraction, and file verification."""

    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.file_path = self.test_dir / "sample.txt"
        self.content = b"Hello, Integrity Test!"
        self.file_path.write_bytes(self.content)

    def tearDown(self) -> None:
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_calculate_sha256_success(self) -> None:
        sha_hex = calculate_sha256(self.file_path, chunk_size=8)
        self.assertEqual(len(sha_hex), 64)

    def test_calculate_sha256_missing_file_raises_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            calculate_sha256(self.test_dir / "non_existent.txt")

    def test_get_file_metadata_non_empty_file(self) -> None:
        meta = get_file_metadata(self.file_path, chunk_size=5)
        self.assertEqual(meta["filename"], "sample.txt")
        self.assertEqual(meta["filesize"], len(self.content))
        self.assertEqual(meta["chunk_size"], 5)
        self.assertEqual(meta["total_chunks"], 5)  # ceil(22 / 5) = 5
        self.assertEqual(meta["sha256"], calculate_sha256(self.file_path))

    def test_get_file_metadata_empty_file(self) -> None:
        empty_path = self.test_dir / "empty.txt"
        empty_path.write_bytes(b"")
        empty_meta = get_file_metadata(empty_path)
        self.assertEqual(empty_meta["filesize"], 0)
        self.assertEqual(empty_meta["total_chunks"], 0)

    def test_get_file_metadata_missing_file_raises_error(self) -> None:
        with self.assertRaises(FileNotFoundError):
            get_file_metadata(self.test_dir / "non_existent.txt")

    def test_verify_file_matching_hash_and_size_returns_ok(self) -> None:
        sha_hex = calculate_sha256(self.file_path)
        is_valid, msg = verify_file(self.file_path, len(self.content), sha_hex)
        self.assertTrue(is_valid)
        self.assertEqual(msg, "OK")

    def test_verify_file_missing_file_returns_false(self) -> None:
        sha_hex = calculate_sha256(self.file_path)
        is_valid, msg = verify_file(self.test_dir / "fake.txt", len(self.content), sha_hex)
        self.assertFalse(is_valid)
        self.assertIn("does not exist", msg.lower())

    def test_verify_file_size_mismatch_returns_false(self) -> None:
        sha_hex = calculate_sha256(self.file_path)
        is_valid, msg = verify_file(self.file_path, len(self.content) + 10, sha_hex)
        self.assertFalse(is_valid)
        self.assertIn("Size mismatch", msg)

    def test_verify_file_hash_mismatch_returns_false(self) -> None:
        is_valid, msg = verify_file(self.file_path, len(self.content), "a" * 64)
        self.assertFalse(is_valid)
        self.assertIn("hash mismatch", msg)


if __name__ == "__main__":
    unittest.main()

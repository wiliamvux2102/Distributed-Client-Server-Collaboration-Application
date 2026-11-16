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

from transfer.integrity import (
    calculate_sha256,
    get_file_metadata,
    is_safe_filename,
    is_valid_transfer_id,
    parse_non_negative_int,
    sanitize_filename,
    verify_file,
)


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

    def test_is_valid_transfer_id_accepts_and_rejects(self) -> None:
        for valid_id in ("abc", "sid-123_XYZ", "0" * 32):
            self.assertTrue(is_valid_transfer_id(valid_id))
        for bad_id in ("", "   ", "../bad", "a/b", "a\\b", "a:b", "bad id", None, 123):
            self.assertFalse(is_valid_transfer_id(bad_id))

    def test_is_safe_filename_and_sanitize_filename(self) -> None:
        self.assertTrue(is_safe_filename("report_final-2026.pdf"))
        for bad_name in ("", "  ", ".", "..", "../a.txt", "sub/a.txt", "C:\\x.exe", "bad\x00name", 123):
            self.assertFalse(is_safe_filename(bad_name))

        self.assertEqual(sanitize_filename("..\\..\\win.ini"), "win.ini")
        self.assertEqual(sanitize_filename(".."), "unnamed_file")
        self.assertEqual(sanitize_filename("."), "unnamed_file")
        self.assertEqual(sanitize_filename(None), "unnamed_file")

    def test_parse_non_negative_int_rejects_bool_negative_and_invalid(self) -> None:
        self.assertEqual(parse_non_negative_int(0, "filesize"), 0)
        self.assertEqual(parse_non_negative_int(42, "filesize"), 42)
        self.assertEqual(parse_non_negative_int("15", "filesize"), 15)

        for bad_val in (True, False, -1, -100, "abc", None):
            with self.assertRaises(ValueError):
                parse_non_negative_int(bad_val, "filesize")


if __name__ == "__main__":
    unittest.main()

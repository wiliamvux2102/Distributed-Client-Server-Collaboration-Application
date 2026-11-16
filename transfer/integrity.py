"""
File integrity verification and SHA-256 calculation.
"""

from __future__ import annotations
import hashlib
import math
from pathlib import Path
from typing import TypedDict

DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB per chunk


def is_valid_transfer_id(value: object) -> bool:
    """Validate that a transfer session identifier is a non-empty path-safe string."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and not any(ch.isspace() for ch in value)
        and "/" not in value
        and "\\" not in value
        and ".." not in value
        and ":" not in value
        and "\x00" not in value
    )


def is_safe_filename(value: object) -> bool:
    """Strictly check that a wire filename is a single path-safe filename."""
    return (
        isinstance(value, str)
        and bool(value.strip())
        and value.strip() not in {".", ".."}
        and ".." not in value
        and "/" not in value
        and "\\" not in value
        and ":" not in value
        and "\x00" not in value
    )


def sanitize_filename(filename: object, fallback: str = "unnamed_file") -> str:
    """Reduce any filename string to a safe single basename for local staging."""
    raw_name = filename.replace("\\", "/") if isinstance(filename, str) else ""
    safe_name = Path(raw_name).name.strip()
    if not safe_name or safe_name in {".", ".."} or ":" in safe_name or "\x00" in safe_name:
        return fallback
    return safe_name


def parse_non_negative_int(value: object, field_name: str = "value") -> int:
    """Validate a non-negative integer while rejecting booleans, floats, strings, and negatives."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Invalid {field_name}: {value!r} (expected integer)")
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value


class FileMetadata(TypedDict):
    filename: str
    filesize: int
    sha256: str
    chunk_size: int
    total_chunks: int


def calculate_sha256(file_path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Calculate the SHA-256 checksum of a file by reading incrementally."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    hasher = hashlib.sha256()
    with path.open("rb") as file_handle:
        while chunk := file_handle.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_file_metadata(file_path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> FileMetadata:
    """Extract metadata (filename, size, hash, chunk count) before initiating a transfer."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File does not exist or is a directory: {path}")

    filesize = path.stat().st_size
    total_chunks = 0 if filesize == 0 else math.ceil(filesize / chunk_size)
    sha256_hash = calculate_sha256(path, chunk_size)

    return {
        "filename": path.name,
        "filesize": filesize,
        "sha256": sha256_hash,
        "chunk_size": chunk_size,
        "total_chunks": total_chunks,
    }


def verify_file(
    file_path: str | Path,
    expected_size: int,
    expected_sha256: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> tuple[bool, str]:
    """Verify that a received file matches the expected byte size and SHA-256 hash."""
    path = Path(file_path)
    if not path.is_file():
        return False, "Temporary file does not exist on disk."

    try:
        actual_size = path.stat().st_size
        if actual_size != expected_size:
            return False, f"Size mismatch: expected {expected_size}, got {actual_size} bytes."

        actual_hash = calculate_sha256(path, chunk_size)
        if actual_hash.lower() != expected_sha256.lower():
            return False, "SHA-256 hash mismatch."

        return True, "OK"
    except Exception as error:
        return False, f"Integrity check error: {error}"

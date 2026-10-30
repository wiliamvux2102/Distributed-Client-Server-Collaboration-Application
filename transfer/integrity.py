import hashlib
import math
from pathlib import Path
from typing import TypedDict

DEFAULT_CHUNK_SIZE = 64 * 1024  # 64 KB


class FileMetadata(TypedDict):
    filename: str
    filesize: int
    sha256: str
    chunk_size: int
    total_chunks: int


def calculate_sha256(file_path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Calculate SHA-256 checksum of a file by reading in chunks to conserve RAM."""
    path = Path(file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    hasher = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            hasher.update(chunk)
    return hasher.hexdigest()


def get_file_metadata(file_path: str | Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> FileMetadata:
    """Extract complete file metadata before sending a FILE_INIT message."""
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
        "total_chunks": total_chunks
    }


def verify_file(
    file_path: str | Path,
    expected_size: int,
    expected_sha256: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE
) -> tuple[bool, str]:
    """Verify received file against expected size and SHA-256 hash."""
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
    except Exception as e:
        return False, f"Integrity check error: {e}"

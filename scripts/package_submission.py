#!/usr/bin/env python3
"""
Packaging script for project submission.
Creates a clean, submission-ready ZIP archive adhering strictly to security requirements:
- Excludes CA private key (certs/private/dev-ca-key.pem) per PKI security standards.
- Retains server private key (certs/private/server-key.pem) so the server can run TLS immediately.
- Excludes virtual environments (.venv), cache (__pycache__), git metadata (.git), large test PDFs, and temporary files (*.part, *.tmp).
"""

from __future__ import annotations
import argparse
from pathlib import Path
import sys
import zipfile

# Root directory of the repository
ROOT_DIR = Path(__file__).resolve().parent.parent

# Files and directories that MUST be excluded from the submission archive
EXCLUDED_DIRS = {
    ".git",
    ".venv",
    "venv",
    "env",
    "ENV",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".vscode",
    ".idea",
    ".agents",
    "dist",
    "build",
}

EXCLUDED_FILES = {
    # CRITICAL: CA private key must NEVER be distributed in submission archives
    "certs/private/dev-ca-key.pem",
    "certs/private/server.csr",
    "config.json",
    "config.local.json",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".part",
    ".tmp",
    ".log",
    ".swp",
    ".aux",
    ".toc",
    ".out",
    ".fls",
    ".fdb_latexmk",
    ".bbl",
    ".blg",
    ".bcf",
    ".run.xml",
    ".nav",
    ".snm",
    ".vrb",
    ".idx",
    ".ind",
    ".ilg",
    ".lof",
    ".lot",
    ".xdv",
}

# Large temporary test assets that should not bloat the source code submission
EXCLUDED_TEST_ASSETS = {
    "Doraemon.pdf",
    "blu-ray-booklet.pdf",
    "2410966.pdf",
}


def should_include_file(file_path: Path) -> bool:
    """Determine whether a file should be packaged into the submission ZIP."""
    rel_path = file_path.relative_to(ROOT_DIR)
    rel_str = str(rel_path).replace("\\", "/")

    # Check directory exclusions
    for part in rel_path.parts[:-1]:
        if part in EXCLUDED_DIRS:
            return False

    # Check explicit file exclusions
    if rel_str in EXCLUDED_FILES:
        return False

    # Check test assets in root
    if rel_str in EXCLUDED_TEST_ASSETS:
        return False

    # Check extension exclusions
    if file_path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return False
    if file_path.name.lower().endswith(".synctex.gz"):
        return False

    # Exclude files inside downloads/ (only preserve the downloads folder itself)
    if len(rel_path.parts) > 1 and rel_path.parts[0] == "downloads":
        if file_path.name != ".gitkeep":
            return False

    return True


def package_project(output_zip: Path) -> None:
    """Package repository files into the target ZIP file."""
    import os

    output_zip.parent.mkdir(parents=True, exist_ok=True)

    included_count = 0
    total_uncompressed_bytes = 0

    print("=" * 65)
    print(" BÀI TẬP LỚN: DISTRIBUTED CLIENT-SERVER COLLABORATION APP")
    print(" Đóng gói mã nguồn nộp bài (Submission Packaging)")
    print("=" * 65)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Ensure downloads folder exists in the archive
        zipf.writestr("downloads/.gitkeep", "")

        for root, dirs, files in os.walk(ROOT_DIR):
            # Prune excluded directories in-place so os.walk does not traverse them
            dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]

            for file in files:
                file_path = Path(root) / file
                if not should_include_file(file_path):
                    continue

                arcname = str(file_path.relative_to(ROOT_DIR)).replace("\\", "/")
                zipf.write(file_path, arcname)
                included_count += 1
                total_uncompressed_bytes += file_path.stat().st_size

    # Verification checks
    print(f"\n[+] Đã đóng gói: {included_count} tệp tin")
    print(f"[+] Tổng dung lượng chưa nén: {total_uncompressed_bytes / (1024 * 1024):.2f} MB")
    print(f"[+] File ZIP đầu ra: {output_zip.resolve()} ({output_zip.stat().st_size / (1024 * 1024):.2f} MB)")

    with zipfile.ZipFile(output_zip, "r") as check_zip:
        names = set(check_zip.namelist())

        # Security check 1: CA private key must NOT be inside
        assert "certs/private/dev-ca-key.pem" not in names, (
            "BẢO MẬT THẤT BẠI: Khóa riêng CA (dev-ca-key.pem) bị phát hiện trong ZIP!"
        )
        print("[✓] Bảo mật: Khóa riêng CA (certs/private/dev-ca-key.pem) đã được loại bỏ.")

        # Security check 2: Server private key MUST be inside for server to run TLS
        assert "certs/private/server-key.pem" in names, (
            "THIẾU TỆP: Khóa riêng server (certs/private/server-key.pem) cần thiết cho TLS!"
        )
        print("[✓] Khóa riêng Server: certs/private/server-key.pem có sẵn cho TLS Server.")

        # Hygiene check: No .venv or cache
        assert not any(n.startswith(".venv/") for n in names), ".venv bị đóng gói vào ZIP!"
        assert not any("__pycache__" in n for n in names), "__pycache__ bị đóng gói vào ZIP!"
        print("[✓] Vệ sinh mã nguồn: Toàn bộ .venv, __pycache__, và file rác đã được làm sạch.")

    print("\n" + "=" * 65)
    print(" ĐÓNG GÓI THÀNH CÔNG")
    print("=" * 65)


def main() -> None:
    parser = argparse.ArgumentParser(description="Package project for submission.")
    parser.add_argument(
        "--output",
        "-o",
        type=Path,
        default=ROOT_DIR / "dist" / "Distributed-Client-Server-Collaboration-Application.zip",
        help="Path to the output ZIP file (default: dist/Distributed-Client-Server-Collaboration-Application.zip)",
    )
    args = parser.parse_args()
    package_project(args.output)


if __name__ == "__main__":
    main()

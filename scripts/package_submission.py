#!/usr/bin/env python3
"""
Packaging script for project submission.
Creates a clean, submission-ready ZIP archive using an explicit allowlist of project files:
- Excludes CA private key (certs/private/dev-ca-key.pem) per PKI security standards.
- Retains server private key (certs/private/server-key.pem) so the server can run TLS immediately.
- Includes only required source, config template, certificate, test, and documentation paths so extra local folders never leak into the archive.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import zipfile

ROOT_DIR = Path(__file__).resolve().parent.parent

INCLUDED_ROOT_FILES = (
    ".gitignore",
    "README.md",
    "config.example.json",
    "requirements.txt",
)

INCLUDED_DIRS = (
    "certs",
    "chat",
    "common",
    "docs",
    "infrastructure",
    "scripts",
    "storage",
    "tests",
    "transfer",
    "ui",
)

EXCLUDED_SUBDIRS = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

EXCLUDED_FILES = {
    # CRITICAL: CA private key and CSR must never be distributed in submission archives.
    "certs/private/dev-ca-key.pem",
    "certs/private/server.csr",
}

EXCLUDED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".part",
    ".tmp",
    ".log",
    ".swp",
}


def should_include_file(file_path: Path) -> bool:
    """Determine whether a file belongs in the submission ZIP archive."""
    rel_path = file_path.relative_to(ROOT_DIR)
    rel_str = str(rel_path).replace("\\", "/")

    if len(rel_path.parts) == 1:
        return rel_str in INCLUDED_ROOT_FILES

    if rel_path.parts[0] not in INCLUDED_DIRS:
        return False

    if any(part in EXCLUDED_SUBDIRS for part in rel_path.parts[1:-1]):
        return False

    if rel_str in EXCLUDED_FILES:
        return False

    if file_path.suffix.lower() in EXCLUDED_EXTENSIONS:
        return False

    return True


def package_project(output_zip: Path) -> None:
    """Package allowlisted project files into the target ZIP file."""
    output_zip.parent.mkdir(parents=True, exist_ok=True)

    included_count = 0
    total_uncompressed_bytes = 0

    print("=" * 65)
    print(" BÀI TẬP LỚN: DISTRIBUTED CLIENT-SERVER COLLABORATION APP")
    print(" Đóng gói mã nguồn nộp bài (Submission Packaging)")
    print("=" * 65)

    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        # Ensure downloads directory exists cleanly in the extracted project.
        zipf.writestr("downloads/.gitkeep", "")

        for filename in INCLUDED_ROOT_FILES:
            file_path = ROOT_DIR / filename
            if file_path.is_file() and should_include_file(file_path):
                zipf.write(file_path, filename)
                included_count += 1
                total_uncompressed_bytes += file_path.stat().st_size

        for dir_name in INCLUDED_DIRS:
            base_dir = ROOT_DIR / dir_name
            if not base_dir.is_dir():
                continue

            for root, dirs, files in os.walk(base_dir):
                dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_SUBDIRS)

                for file in sorted(files):
                    file_path = Path(root) / file
                    if not should_include_file(file_path):
                        continue

                    arcname = str(file_path.relative_to(ROOT_DIR)).replace("\\", "/")
                    zipf.write(file_path, arcname)
                    included_count += 1
                    total_uncompressed_bytes += file_path.stat().st_size

    print(f"\n[+] Đã đóng gói: {included_count} tệp tin (+ downloads/.gitkeep)")
    print(f"[+] Tổng dung lượng chưa nén: {total_uncompressed_bytes / (1024 * 1024):.2f} MB")
    print(f"[+] File ZIP đầu ra: {output_zip.resolve()} ({output_zip.stat().st_size / (1024 * 1024):.2f} MB)")

    with zipfile.ZipFile(output_zip, "r") as check_zip:
        names = set(check_zip.namelist())

        assert "certs/private/dev-ca-key.pem" not in names, (
            "BẢO MẬT THẤT BẠI: Khóa riêng CA (dev-ca-key.pem) bị phát hiện trong ZIP!"
        )
        print("[✓] Bảo mật: Khóa riêng CA (certs/private/dev-ca-key.pem) đã được loại bỏ.")

        assert "certs/private/server-key.pem" in names, (
            "THIẾU TỆP: Khóa riêng server (certs/private/server-key.pem) cần thiết cho TLS!"
        )
        print("[✓] Khóa riêng Server: certs/private/server-key.pem có sẵn cho TLS Server.")

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

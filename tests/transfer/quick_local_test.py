"""
Quick local test suite for the File Transfer module.
Tests: empty file, small file, binary file, offline user, and successful transfer flow.
"""
from pathlib import Path
import shutil
import time
from transfer.sender import FileSender
from transfer.receiver import FileReceiver
from transfer.server_handler import FileTransferServerHandler


def run_tests() -> None:
    print("=== Starting File Transfer Local Tests ===")

    test_dir = Path("test_sandbox")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(exist_ok=True)

    downloads_dir = test_dir / "downloads"
    source_dir = test_dir / "source"
    source_dir.mkdir(exist_ok=True)

    empty_file = source_dir / "empty.txt"
    empty_file.write_bytes(b"")

    small_file = source_dir / "small.txt"
    small_file.write_bytes(b"Hello, Distributed Systems World! This is a test file.")

    binary_file = source_dir / "data.bin"
    binary_file.write_bytes(bytes(range(256)) * 100) # 25.6 KB binary data

    clients = {}

    def mock_send_to_user(target_user: str, header: dict, payload: bytes) -> bool:
        if target_user not in clients:
            return False
        # Simulate network packet dispatch
        clients[target_user].handle_incoming_message(header, payload)
        return True

    server_handler = FileTransferServerHandler(
        send_to_user_fn=mock_send_to_user,
        is_user_online_fn=lambda u: u in clients,
    )

    completed = []

    alice_sender = FileSender(
        username="alice",
        send_frame_fn=lambda hdr, pld: server_handler.handle_message("alice", hdr, pld),
        chunk_size=1024,
        on_complete=lambda sid, fn, msg: completed.append(("alice", sid, fn)),
        on_error=lambda sid, fn, err: print(f"[Alice Error] {fn}: {err}"),
    )

    bob_receiver = FileReceiver(
        username="bob",
        send_frame_fn=lambda hdr, pld: server_handler.handle_message("bob", hdr, pld),
        download_dir=downloads_dir,
        on_complete=lambda sid, fn, path: completed.append(("bob", sid, fn, path)),
        on_error=lambda sid, fn, err: print(f"[Bob Error] {fn}: {err}"),
    )

    clients["alice"] = alice_sender
    clients["bob"] = bob_receiver

    def wait_for_completion(timeout: float = 5.0) -> bool:
        start = time.time()
        while time.time() - start < timeout:
            if len(completed) >= 2: # both sender and receiver recorded completion
                return True
            time.sleep(0.05)
        return False

    # Test 1: Empty file transfer
    print("\n--- Test 1: Empty file (0 bytes) ---")
    completed.clear()
    sid_empty = alice_sender.start_transfer("bob", empty_file)
    assert wait_for_completion(), "Empty file transfer timed out"
    target_empty = downloads_dir / "empty.txt"
    assert target_empty.exists(), "Empty file should be created"
    assert target_empty.stat().st_size == 0
    print("Test 1 PASSED.")

    # Test 2: Small file transfer
    print("\n--- Test 2: Small file ---")
    completed.clear()
    sid_small = alice_sender.start_transfer("bob", small_file)
    assert wait_for_completion(), "Small file transfer timed out"
    target_small = downloads_dir / "small.txt"
    assert target_small.exists()
    assert target_small.read_bytes() == small_file.read_bytes()
    print("Test 2 PASSED.")

    # Test 3: Binary file transfer (multi-chunk)
    print("\n--- Test 3: Binary file (multi-chunk) ---")
    completed.clear()
    sid_bin = alice_sender.start_transfer("bob", binary_file)
    assert wait_for_completion(timeout=10.0), "Binary file transfer timed out"
    target_bin = downloads_dir / "data.bin"
    assert target_bin.exists()
    assert target_bin.read_bytes() == binary_file.read_bytes()
    print("Test 3 PASSED.")

    # Test 4: Offline user error handling
    print("\n--- Test 4: Offline user ---")
    offline_errors = []
    charlie_sender = FileSender(
        username="charlie",
        send_frame_fn=lambda hdr, pld: server_handler.handle_message("charlie", hdr, pld),
        on_error=lambda sid, fn, err: offline_errors.append(err),
    )
    clients["charlie"] = charlie_sender
    charlie_sender.start_transfer("david", small_file)
    assert len(offline_errors) > 0, "Should report offline error"
    print("Test 4 PASSED.")

    # Cleanup sandbox
    shutil.rmtree(test_dir)
    print("\n=== All Local Transfer Tests PASSED Successfully! ===")


if __name__ == "__main__":
    run_tests()

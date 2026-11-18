"""Tests for direct TCP P2P file transfer and automatic server-relay fallback."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import socket
import tempfile
import threading
import time
import unittest

from transfer.p2p import (
    P2P_MAGIC_CHUNK,
    make_p2p_cipher,
    recv_p2p_binary_frame,
    recv_p2p_json,
    send_p2p_binary_frame,
    send_p2p_json,
)
from transfer.receiver import FileReceiver
from transfer.sender import FileSender
from transfer.server_handler import FileTransferServerHandler


class P2PTransferTests(unittest.TestCase):
    """Verify direct P2P binary chunking, fallback to server relay, and P2P token checks."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.src_dir = self.tmp_dir / "src"
        self.dl_dir = self.tmp_dir / "downloads"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.dl_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _wire_pair(self, enable_p2p_receiver: bool = True):
        relayed_types: list[str] = []
        done = threading.Event()
        errors: list[str] = []

        def send_to_user(target: str, header: dict, payload: bytes) -> bool:
            if target == "bob":
                receiver.handle_incoming_message(header, payload)
                return True
            if target == "alice":
                sender.handle_incoming_message(header, payload)
                return True
            return False

        server = FileTransferServerHandler(
            send_to_user_fn=send_to_user,
            is_user_online_fn=lambda u: u in {"alice", "bob"},
        )

        def alice_send(header: dict, payload: bytes = b"") -> None:
            relayed_types.append(header["type"])
            server.handle_message("alice", header, payload)

        def bob_send(header: dict, payload: bytes = b"") -> None:
            relayed_types.append(header["type"])
            server.handle_message("bob", header, payload)

        sender = FileSender(
            username="alice",
            send_frame_fn=alice_send,
            on_complete=lambda sid, fn, msg: done.set(),
            on_error=lambda sid, fn, err: (errors.append(err), done.set()),
        )
        receiver = FileReceiver(
            username="bob",
            send_frame_fn=bob_send,
            download_dir=self.dl_dir,
            enable_p2p=enable_p2p_receiver,
            on_error=lambda sid, fn, err: (errors.append(err), done.set()),
        )
        return sender, receiver, server, relayed_types, done, errors

    def test_direct_p2p_bypasses_server_for_chunks_and_preserves_sha256(self) -> None:
        sender, receiver, _server, relayed_types, done, errors = self._wire_pair(True)
        payload = os.urandom(180 * 1024)
        src_file = self.src_dir / "p2p_binary.bin"
        src_file.write_bytes(payload)

        sender.start_transfer("bob", src_file)
        self.assertTrue(done.wait(timeout=10.0), "P2P transfer timed out")
        self.assertEqual(errors, [])

        saved = self.dl_dir / "p2p_binary.bin"
        self.assertTrue(saved.exists())
        self.assertEqual(
            hashlib.sha256(saved.read_bytes()).hexdigest(),
            hashlib.sha256(payload).hexdigest(),
        )
        # Chunks and FILE_END must go directly over P2P, not through server relay
        self.assertNotIn("FILE_CHUNK", relayed_types)
        self.assertNotIn("FILE_END", relayed_types)
        self.assertIn("FILE_INIT", relayed_types)
        self.assertIn("FILE_ACCEPT", relayed_types)
        self.assertIn("FILE_RESULT", relayed_types)

    def test_unreachable_p2p_falls_back_automatically_to_server_relay(self) -> None:
        sender, receiver, _server, relayed_types, done, errors = self._wire_pair(False)
        payload = os.urandom(140 * 1024)
        src_file = self.src_dir / "fallback.bin"
        src_file.write_bytes(payload)

        # Find a closed port to simulate NAT/firewall blocking direct P2P
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            closed_port = probe.getsockname()[1]

        orig_handle = sender.handle_incoming_message

        def inject_unreachable_p2p(header: dict, p: bytes = b"") -> None:
            if header.get("type") == "FILE_ACCEPT":
                header = {
                    **header,
                    "p2p_hosts": ["127.0.0.1"],
                    "p2p_port": closed_port,
                    "p2p_token": "unused",
                }
            orig_handle(header, p)

        sender.handle_incoming_message = inject_unreachable_p2p  # type: ignore[method-assign]
        sender.start_transfer("bob", src_file)
        self.assertTrue(done.wait(timeout=10.0), "Fallback relay transfer timed out")
        self.assertEqual(errors, [])

        saved = self.dl_dir / "fallback.bin"
        self.assertTrue(saved.exists())
        self.assertEqual(saved.read_bytes(), payload)
        self.assertIn("FILE_CHUNK", relayed_types)
        self.assertIn("FILE_END", relayed_types)

    def test_p2p_listener_rejects_invalid_token(self) -> None:
        captured_accept: list[dict] = []
        receiver = FileReceiver(
            username="bob",
            send_frame_fn=lambda h, p=b"": captured_accept.append(h),
            download_dir=self.dl_dir,
            enable_p2p=True,
        )
        receiver.handle_incoming_message(
            {
                "type": "FILE_INIT",
                "session_id": "sid_auth_test",
                "sender": "alice",
                "filename": "test.bin",
                "filesize": 100,
                "sha256": "0" * 64,
                "total_chunks": 1,
            }
        )
        self.assertEqual(len(captured_accept), 1)
        accept = captured_accept[0]
        port = accept["p2p_port"]
        with socket.create_connection(("127.0.0.1", port), timeout=2.0) as sock:
            send_p2p_json(sock, {"session_id": "sid_auth_test", "p2p_token": "wrong_token"})
            resp = recv_p2p_json(sock)
            self.assertFalse(resp["ok"])
        receiver.abort_all("cleanup")

    def test_p2p_binary_frame_is_aes_256_gcm_encrypted(self) -> None:
        secret = b"SUPER_SECRET_CONFIDENTIAL_DOCUMENT_BYTES"
        cipher = make_p2p_cipher("one_time_p2p_token_123")
        wrong_cipher = make_p2p_cipher("wrong_token")

        s1, s2 = socket.socketpair()
        try:
            send_p2p_binary_frame(s1, P2P_MAGIC_CHUNK, 0, secret, cipher=cipher)
            raw_on_wire = s2.recv(4096)
            self.assertNotIn(secret, raw_on_wire)

            # Put bytes back via a second socketpair to test decryption & wrong-key rejection
            a1, a2 = socket.socketpair()
            try:
                a1.sendall(raw_on_wire)
                with self.assertRaises(Exception):
                    recv_p2p_binary_frame(a2, cipher=wrong_cipher)
            finally:
                a1.close()
                a2.close()

            b1, b2 = socket.socketpair()
            try:
                b1.sendall(raw_on_wire)
                magic, idx, decrypted = recv_p2p_binary_frame(b2, cipher=cipher)
                self.assertEqual(magic, P2P_MAGIC_CHUNK)
                self.assertEqual(idx, 0)
                self.assertEqual(decrypted, secret)
            finally:
                b1.close()
                b2.close()
        finally:
            s1.close()
            s2.close()


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for infrastructure layer: UserRegistry, Protocol Framing, Authentication, and MessageDispatcher.
"""

from __future__ import annotations
import json
from pathlib import Path
import struct
import sys
import threading
import time
import unittest
from unittest.mock import Mock

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from common import message_types as mt
from common.protocol import (
    HEADER_FORMAT,
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    recv_exact,
    recv_frame,
    recv_message,
    send_frame,
    send_message,
)
from infrastructure.authentication import normalize_username
from infrastructure.message_dispatcher import (
    ClientContext,
    MessageDispatcher,
)
from infrastructure.user_registry import UserRegistry, UserSession


class FakeSocket:
    """In-memory socket emulator for deterministic TCP stream and fragmentation testing."""

    def __init__(self, incoming_data: bytes = b"", chunk_size: int = 0) -> None:
        self.incoming = bytearray(incoming_data)
        self.outgoing = bytearray()
        self.chunk_size = chunk_size

    def recv(self, size: int) -> bytes:
        if not self.incoming:
            return b""
        n = min(size, self.chunk_size) if self.chunk_size > 0 else min(size, len(self.incoming))
        chunk = bytes(self.incoming[:n])
        del self.incoming[:n]
        return chunk

    def sendall(self, data: bytes) -> None:
        self.outgoing.extend(data)


class UserRegistryTests(unittest.TestCase):
    """Test user registration, online list, and heartbeat expiry in UserRegistry."""

    def setUp(self) -> None:
        self.registry = UserRegistry()
        self.conn1 = Mock()
        self.conn2 = Mock()
        self.lock1 = threading.Lock()
        self.lock2 = threading.Lock()

    def test_register_and_list_online(self) -> None:
        success = self.registry.register(
            "alice",
            self.conn1,
            ("192.168.1.10", 50000),
            self.lock1,
            "token_alice",
        )
        self.assertTrue(success)
        self.assertEqual(self.registry.list_online(), ["alice"])
        session = self.registry.get_session("alice")
        self.assertIsNotNone(session)
        self.assertEqual(session.heartbeat_token, "token_alice")

    def test_duplicate_username_rejected(self) -> None:
        self.registry.register("alice", self.conn1, ("192.168.1.10", 50000), self.lock1, "token1")
        # Attempt registering same username with another connection
        second_attempt = self.registry.register(
            "alice",
            self.conn2,
            ("192.168.1.11", 50001),
            self.lock2,
            "token2",
        )
        self.assertFalse(second_attempt)
        # Verify original session remains untouched
        session = self.registry.get_session("alice")
        self.assertIs(session.connection, self.conn1)

    def test_remove_if_same_socket_enforces_identity(self) -> None:
        self.registry.register("alice", self.conn1, ("192.168.1.10", 50000), self.lock1, "token1")

        # Wrong connection should not be able to unregister alice
        removed_wrong = self.registry.remove_if_same("alice", self.conn2)
        self.assertFalse(removed_wrong)
        self.assertIn("alice", self.registry.list_online())

        # Correct connection successfully unregisters
        removed_correct = self.registry.remove_if_same("alice", self.conn1)
        self.assertTrue(removed_correct)
        self.assertNotIn("alice", self.registry.list_online())

    def test_refresh_heartbeat_token_and_ip_validation(self) -> None:
        self.registry.register("alice", self.conn1, ("192.168.1.10", 50000), self.lock1, "secret_token")

        # Correct token and IP
        self.assertTrue(self.registry.refresh_heartbeat("secret_token", "192.168.1.10"))

        # Wrong token
        self.assertFalse(self.registry.refresh_heartbeat("wrong_token", "192.168.1.10"))

        # Wrong IP
        self.assertFalse(self.registry.refresh_heartbeat("secret_token", "10.0.0.1"))

    def test_remove_stale_heartbeats_prunes_expired_sessions(self) -> None:
        self.registry.register("alice", self.conn1, ("192.168.1.10", 50000), self.lock1, "token_alice")
        self.registry.register("bob", self.conn2, ("192.168.1.20", 50001), self.lock2, "token_bob")

        # Manually backdate alice's last heartbeat
        session_alice = self.registry.get_session("alice")
        session_alice.last_heartbeat = time.monotonic() - 100.0

        expired = self.registry.remove_stale(timeout_seconds=30.0)
        self.assertEqual(len(expired), 1)
        self.assertEqual(expired[0][0], "alice")

        # Alice is gone, Bob remains
        self.assertNotIn("alice", self.registry.list_online())
        self.assertIn("bob", self.registry.list_online())


class AuthenticationTests(unittest.TestCase):
    """Test username normalization and input sanitation rules."""

    def test_normalize_username_valid_names(self) -> None:
        self.assertEqual(normalize_username("alice"), "alice")
        self.assertEqual(normalize_username("  bob_123  "), "bob_123")
        self.assertEqual(normalize_username("USER_NAME_99"), "USER_NAME_99")

    def test_normalize_username_length_boundaries(self) -> None:
        # Minimum length is 3
        with self.assertRaises(ValueError):
            normalize_username("ab")

        # Maximum length is 20
        with self.assertRaises(ValueError):
            normalize_username("a" * 21)

        # 3 and 20 are valid
        self.assertEqual(len(normalize_username("abc")), 3)
        self.assertEqual(len(normalize_username("a" * 20)), 20)

    def test_normalize_username_rejects_non_ascii_and_symbols(self) -> None:
        # Non-ASCII unicode
        with self.assertRaises(ValueError):
            normalize_username("người_dùng")

        # Special characters or spaces in the middle
        with self.assertRaises(ValueError):
            normalize_username("alice smith")
        with self.assertRaises(ValueError):
            normalize_username("alice@hcmut")
        with self.assertRaises(ValueError):
            normalize_username("alice-dev")

    def test_normalize_username_rejects_non_string(self) -> None:
        with self.assertRaises(ValueError):
            normalize_username(12345)
        with self.assertRaises(ValueError):
            normalize_username(None)


class ProtocolFramingTests(unittest.TestCase):
    """Test 4-byte header framing, chunked stream reassembly, and payload size bounds."""

    def test_send_and_recv_message_roundtrip(self) -> None:
        sock = FakeSocket()
        message = {"type": "TEST_MSG", "payload": "xin chào", "count": 42}
        send_message(sock, message)

        # Feed the generated outgoing frame back as incoming
        recv_sock = FakeSocket(incoming_data=sock.outgoing)
        decoded = recv_message(recv_sock)
        self.assertEqual(decoded, message)

    def test_partial_tcp_chunks_reassembly(self) -> None:
        """Verify that fragmented TCP packets (small chunks) are correctly reassembled by recv_exact."""
        sock = FakeSocket()
        message = {"type": "FRAGMENT_TEST", "body": "a" * 500}
        send_message(sock, message)

        # Deliver the packet fragmented in tiny 3-byte chunks
        frag_sock = FakeSocket(incoming_data=sock.outgoing, chunk_size=3)
        decoded = recv_message(frag_sock)
        self.assertEqual(decoded, message)

    def test_recv_exact_early_eof_raises_error(self) -> None:
        # Header specifies 10 bytes payload, but only 3 bytes are available before stream terminates
        header = struct.pack(HEADER_FORMAT, 10)
        sock = FakeSocket(incoming_data=header + b"123")
        with self.assertRaises(EOFError):
            recv_frame(sock)

    def test_malformed_json_raises_decode_error(self) -> None:
        raw_garbage = b"{invalid json content...}"
        header = struct.pack(HEADER_FORMAT, len(raw_garbage))
        sock = FakeSocket(incoming_data=header + raw_garbage)
        with self.assertRaises(json.JSONDecodeError):
            recv_message(sock)

    def test_oversized_payload_rejected(self) -> None:
        huge_bytes = b"X" * (MAX_PAYLOAD_SIZE + 1)
        sock = FakeSocket()
        with self.assertRaises(ValueError):
            send_frame(sock, huge_bytes)

    def test_send_message_validates_dict_and_type(self) -> None:
        sock = FakeSocket()
        with self.assertRaises(ValueError):
            send_message(sock, ["not", "a", "dict"])

        with self.assertRaises(ValueError):
            send_message(sock, {"missing_type": 123})

        with self.assertRaises(ValueError):
            send_message(sock, {"type": ""})


class MessageDispatcherTests(unittest.TestCase):
    """Test registration and routing of messages to handlers in MessageDispatcher."""

    def setUp(self) -> None:
        self.dispatcher = MessageDispatcher()
        self.received_events: list[tuple[str, dict]] = []

    def test_dispatcher_routes_to_registered_handler(self) -> None:
        def sample_handler(context: ClientContext, message: dict) -> None:
            self.received_events.append((context.username, message))

        self.dispatcher.register("CUSTOM_MSG", sample_handler)

        context = Mock()
        context.username = "alice"
        msg = {"type": "CUSTOM_MSG", "data": 100}

        handled = self.dispatcher.dispatch(context, msg)
        self.assertTrue(handled)
        self.assertEqual(len(self.received_events), 1)
        self.assertEqual(self.received_events[0], ("alice", msg))

    def test_dispatcher_returns_false_for_unknown_type(self) -> None:
        context = Mock()
        handled = self.dispatcher.dispatch(context, {"type": "UNREGISTERED_TYPE"})
        self.assertFalse(handled)

    def test_dispatcher_rejects_duplicate_registration(self) -> None:
        self.dispatcher.register("SAMPLE", lambda c, m: None)
        with self.assertRaises(ValueError):
            self.dispatcher.register("SAMPLE", lambda c, m: None)

    def test_dispatcher_rejects_empty_type(self) -> None:
        with self.assertRaises(ValueError):
            self.dispatcher.register("", lambda c, m: None)


if __name__ == "__main__":
    unittest.main()

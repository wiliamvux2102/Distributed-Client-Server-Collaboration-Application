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

    def test_refresh_heartbeat_signed_hmac_and_anti_replay(self) -> None:
        from infrastructure.user_registry import compute_heartbeat_hmac

        self.registry.register("alice", self.conn1, ("192.168.1.10", 50000), self.lock1, "secret_token")
        now_ms = int(time.time() * 1000)
        sig = compute_heartbeat_hmac("secret_token", "alice", now_ms)

        # Valid signed heartbeat succeeds
        self.assertTrue(self.registry.refresh_heartbeat_signed("alice", now_ms, sig, "192.168.1.10"))

        # Exact replay with same timestamp fails
        self.assertFalse(self.registry.refresh_heartbeat_signed("alice", now_ms, sig, "192.168.1.10"))

        # Wrong signature fails
        self.assertFalse(self.registry.refresh_heartbeat_signed("alice", now_ms + 1, "bad_sig", "192.168.1.10"))

        # Wrong source IP fails
        sig2 = compute_heartbeat_hmac("secret_token", "alice", now_ms + 2)
        self.assertFalse(self.registry.refresh_heartbeat_signed("alice", now_ms + 2, sig2, "10.0.0.1"))

        # Expired timestamp outside max_skew_seconds fails
        old_ms = now_ms - 60_000
        old_sig = compute_heartbeat_hmac("secret_token", "alice", old_ms)
        self.assertFalse(self.registry.refresh_heartbeat_signed("alice", old_ms, old_sig, "192.168.1.10"))

    def test_send_heartbeats_sends_hmac_without_plaintext_token(self) -> None:
        import json
        from unittest.mock import MagicMock, patch
        from infrastructure import client_connection
        from infrastructure.user_registry import compute_heartbeat_hmac

        sent_packets: list[tuple[bytes, tuple[str, int]]] = []
        stop_event = threading.Event()

        mock_sock = MagicMock()

        def fake_sendto(data: bytes, addr: tuple[str, int]) -> int:
            sent_packets.append((data, addr))
            stop_event.set()
            return len(data)

        mock_sock.sendto.side_effect = fake_sendto
        mock_sock.__enter__.return_value = mock_sock
        mock_sock.__exit__.return_value = False

        with patch("infrastructure.client_connection.socket.socket", return_value=mock_sock):
            client_connection.send_heartbeats(
                "127.0.0.1",
                "alice",
                "top_secret_tok",
                stop_event,
                lambda msg: None,
            )

        self.assertEqual(len(sent_packets), 1)
        data, addr = sent_packets[0]
        self.assertEqual(addr[0], "127.0.0.1")
        payload = json.loads(data.decode("utf-8"))
        self.assertNotIn("token", payload)
        self.assertEqual(payload["username"], "alice")
        self.assertIsInstance(payload["ts"], int)
        self.assertEqual(
            payload["sig"],
            compute_heartbeat_hmac("top_secret_tok", "alice", payload["ts"]),
        )


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


class InfrastructureRegressionTests(unittest.TestCase):
    """Regression tests for heartbeat input validation, UDP parsing, client guards, and reconnect isolation."""

    def test_refresh_heartbeat_signed_rejects_bool_timestamp_and_non_str(self) -> None:
        import hmac
        import hashlib

        registry = UserRegistry()
        sock = Mock()
        token = "secret-token"
        self.assertTrue(registry.register("alice", sock, ("127.0.0.1", 50001), threading.Lock(), token))

        now_ms = int(time.time() * 1000)
        valid_sig = hmac.new(
            token.encode("utf-8"),
            f"alice:{now_ms}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        self.assertFalse(registry.refresh_heartbeat_signed("alice", True, valid_sig, "127.0.0.1"))  # type: ignore[arg-type]
        self.assertFalse(registry.refresh_heartbeat_signed(123, now_ms, valid_sig, "127.0.0.1"))  # type: ignore[arg-type]
        self.assertFalse(registry.refresh_heartbeat_signed("alice", now_ms, 999, "127.0.0.1"))  # type: ignore[arg-type]
        self.assertFalse(registry.refresh_heartbeat_signed("alice", now_ms, "chữ_unicode", "127.0.0.1"))
        self.assertFalse(registry.refresh_heartbeat_signed("alice", now_ms, "é" * 64, "127.0.0.1"))
        self.assertTrue(registry.refresh_heartbeat_signed("alice", now_ms, valid_sig, "127.0.0.1"))

    def test_udp_worker_survives_malformed_unicode_heartbeat(self) -> None:
        import socket
        from unittest.mock import patch
        from infrastructure import udp_service

        registry = UserRegistry()
        token = "secret-token"
        registry.register("alice", Mock(), ("127.0.0.1", 50001), threading.Lock(), token)

        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            udp_port = probe.getsockname()[1]

        stop_event = threading.Event()
        with (
            patch.object(udp_service, "UDP_BIND_HOST", "127.0.0.1"),
            patch.object(udp_service, "UDP_DISCOVERY_PORT", udp_port),
            patch.object(udp_service, "UDP_WAIT_INTERVAL", 0.05),
            patch.object(udp_service, "user_registry", registry),
        ):
            worker = threading.Thread(target=udp_service.serve_discovery, args=(stop_event,), daemon=True)
            worker.start()
            try:
                time.sleep(0.08)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client_sock:
                    client_sock.settimeout(1.5)
                    bad_hb = json.dumps({
                        "type": mt.UDP_HEARTBEAT,
                        "username": "alice",
                        "ts": int(time.time() * 1000),
                        "sig": "chữ_unicode_không_ascii",
                    }).encode("utf-8")
                    client_sock.sendto(bad_hb, ("127.0.0.1", udp_port))
                    time.sleep(0.05)

                    # Discovery must still work because malformed heartbeat did not stop UDP worker.
                    discover_req = json.dumps({
                        "type": mt.UDP_DISCOVER,
                        "protocol_version": udp_service.PROTOCOL_VERSION,
                    }).encode("utf-8")
                    client_sock.sendto(discover_req, ("127.0.0.1", udp_port))
                    resp_bytes, _ = client_sock.recvfrom(2048)
                    resp = json.loads(resp_bytes.decode("utf-8"))
                    self.assertEqual(resp["type"], mt.UDP_DISCOVERY_RESPONSE)
                    self.assertTrue(worker.is_alive())
            finally:
                stop_event.set()
                worker.join(timeout=1.5)

    def test_app_config_strict_validation_and_fallback(self) -> None:
        import shutil
        import tempfile
        from common.app_config import EXAMPLE_CONFIG_FILE, load_config

        tmp_dir = Path(tempfile.mkdtemp())
        try:
            # Missing local config.json falls back to config.example.json
            missing_local = tmp_dir / "missing_config.json"
            fallback_cfg = load_config(example_file=EXAMPLE_CONFIG_FILE, local_file=missing_local)
            self.assertEqual(fallback_cfg["server"]["tcp_port"], 5000)
            self.assertEqual(fallback_cfg["udp"]["port"], 5001)

            # Complete local config.json loads directly
            full_data = json.loads(EXAMPLE_CONFIG_FILE.read_text(encoding="utf-8"))
            full_data["server"]["tcp_port"] = 6123
            full_local = tmp_dir / "config.json"
            full_local.write_text(json.dumps(full_data), encoding="utf-8")
            loaded = load_config(example_file=EXAMPLE_CONFIG_FILE, local_file=full_local)
            self.assertEqual(loaded["server"]["tcp_port"], 6123)

            # Incomplete local config.json must fail with ValueError instead of merging
            partial_local = tmp_dir / "partial_config.json"
            partial_local.write_text(
                json.dumps({"server": {"tcp_port": 6123}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_config(example_file=EXAMPLE_CONFIG_FILE, local_file=partial_local)

            # Out-of-range port in full config must fail with ValueError
            bad_port_data = json.loads(EXAMPLE_CONFIG_FILE.read_text(encoding="utf-8"))
            bad_port_data["server"]["tcp_port"] = 70000
            bad_port_local = tmp_dir / "bad_config.json"
            bad_port_local.write_text(json.dumps(bad_port_data), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_config(example_file=EXAMPLE_CONFIG_FILE, local_file=bad_port_local)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def test_client_session_connect_and_logout_guards(self) -> None:
        from infrastructure.client_connection import ClientSession

        with self.assertRaises(ValueError):
            ClientSession.connect("alice", lambda s: None, lambda m: None, server_host="   ", server_port=5000)
        with self.assertRaises(ValueError):
            ClientSession.connect("alice", lambda s: None, lambda m: None, server_host="127.0.0.1", server_port=True)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ClientSession.connect("alice", lambda s: None, lambda m: None, server_host="127.0.0.1", server_port=70000)

        sock = Mock()
        sock.sendall.side_effect = OSError("broken pipe")
        session = ClientSession(
            sock,
            "127.0.0.1",
            "alice",
            "tok",
            on_error=lambda s: None,
            on_unhandled=lambda m: None,
        )
        # Closing/half-closed socket during logout must not raise
        session.logout(timeout=0.1)
        session.close()

    def test_udp_discovery_ignores_boolean_protocol_version_and_tcp_port(self) -> None:
        import socket
        from unittest.mock import patch
        from infrastructure import client_connection

        bad_payload = json.dumps({
            "type": mt.UDP_DISCOVERY_RESPONSE,
            "protocol_version": True,
            "tcp_port": 5000,
        }).encode("utf-8")
        bad_port_payload = json.dumps({
            "type": mt.UDP_DISCOVERY_RESPONSE,
            "protocol_version": 1,
            "tcp_port": True,
        }).encode("utf-8")

        class DummyUdpSock:
            def __init__(self) -> None:
                self._responses = [
                    (bad_payload, ("127.0.0.1", 5001)),
                    (bad_port_payload, ("127.0.0.1", 5001)),
                ]

            def setsockopt(self, *args, **kwargs) -> None:
                pass

            def settimeout(self, timeout) -> None:
                pass

            def sendto(self, data, addr) -> None:
                pass

            def recvfrom(self, size):
                if self._responses:
                    return self._responses.pop(0)
                raise socket.timeout("done")

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

        with (
            patch("infrastructure.client_connection.socket.socket", return_value=DummyUdpSock()),
            patch("infrastructure.client_connection._get_discovery_targets", return_value=["127.0.0.1"]),
        ):
            with self.assertRaises(TimeoutError):
                client_connection.discover_server()

    def test_stale_disconnect_does_not_evict_reconnected_user_transfers(self) -> None:
        from unittest.mock import patch
        from transfer.server_handler import FileTransferServerHandler

        registry = UserRegistry()
        stale_sock = Mock()
        new_sock = Mock()
        registry.register("alice", new_sock, ("127.0.0.1", 50002), threading.Lock(), "tok2")

        ft_handler = FileTransferServerHandler(
            send_to_user_fn=lambda u, m: True,
            is_user_online_fn=lambda u: registry.get_session(u) is not None,
        )
        # Only when remove_if_same returns True should disconnect fire on_client_disconnect
        with patch.object(ft_handler, "on_client_disconnect") as disc_spy:
            if registry.remove_if_same("alice", stale_sock):
                ft_handler.on_client_disconnect("alice")
            disc_spy.assert_not_called()
            self.assertIsNotNone(registry.get_session("alice"))

            if registry.remove_if_same("alice", new_sock):
                ft_handler.on_client_disconnect("alice")
            disc_spy.assert_called_once_with("alice")
            self.assertIsNone(registry.get_session("alice"))

    def test_live_tls_udp_loopback_end_to_end_session(self) -> None:
        """Full live loopback over real UDP discovery/heartbeat and TLS TCP server/client."""
        import shutil
        import socket
        import tempfile
        from unittest.mock import patch
        from chat.client_chat import ClientChat
        from infrastructure import client_connection, server as infra_server, udp_service
        from transfer.sender import ClientFileTransferManager

        # Clear any lingering shared user_registry entries before loopback test
        for user in infra_server.user_registry.list_online():
            session_obj = infra_server.user_registry.get_session(user)
            if session_obj is not None:
                infra_server.user_registry.remove_if_same(user, session_obj.connection)

        # Allocate free ephemeral UDP port cleanly
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe_udp:
            probe_udp.bind(("127.0.0.1", 0))
            udp_port = probe_udp.getsockname()[1]

        tls_ctx = infra_server.create_server_context()
        dispatcher = infra_server.create_message_dispatcher()

        tcp_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        tcp_sock.bind(("127.0.0.1", 0))
        tcp_sock.listen(8)
        tcp_sock.settimeout(0.2)
        tcp_port = tcp_sock.getsockname()[1]

        stop_event = threading.Event()
        client_threads: list[threading.Thread] = []

        def accept_loop() -> None:
            while not stop_event.is_set():
                try:
                    conn, addr = tcp_sock.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                t = threading.Thread(
                    target=infra_server.handle_client,
                    args=(conn, addr, tls_ctx, dispatcher),
                    daemon=True,
                )
                t.start()
                client_threads.append(t)

        accept_thread = threading.Thread(target=accept_loop, daemon=True)
        accept_thread.start()

        tmp_dir = Path(tempfile.mkdtemp())

        try:
            with (
                patch.object(udp_service, "UDP_BIND_HOST", "127.0.0.1"),
                patch.object(udp_service, "UDP_DISCOVERY_PORT", udp_port),
                patch.object(udp_service, "TCP_SERVER_PORT", tcp_port),
                patch.object(client_connection, "DISCOVERY_PORT", udp_port),
                patch.object(client_connection, "_get_discovery_targets", return_value=["127.0.0.1"]),
            ):
                udp_thread = threading.Thread(
                    target=udp_service.serve_discovery,
                    args=(stop_event,),
                    daemon=True,
                )
                udp_thread.start()
                time.sleep(0.08)

                host, port = client_connection.discover_server()
                self.assertEqual(port, tcp_port)

                alice_msgs: list[tuple[str, str, bool]] = []
                bob_msgs: list[tuple[str, str, bool]] = []
                alice_events: list[dict] = []
                bob_events: list[dict] = []
                alice_ft_holder: list[ClientFileTransferManager] = []
                bob_ft_holder: list[ClientFileTransferManager] = []

                def make_unhandled(ft_ref):
                    def on_unhandled(msg: dict) -> None:
                        mtype = msg.get("type")
                        if mtype in {mt.FILE_INIT, mt.FILE_ACCEPT, mt.FILE_CHUNK, mt.FILE_END, mt.FILE_RESULT, mt.FILE_ERROR} and ft_ref:
                            ft_ref[0].handle_incoming(msg)
                    return on_unhandled

                alice = client_connection.ClientSession.connect(
                    "alice",
                    lambda err: None,
                    make_unhandled(alice_ft_holder),
                    server_host=host,
                    server_port=port,
                )
                bob = client_connection.ClientSession.connect(
                    "bob",
                    lambda err: None,
                    make_unhandled(bob_ft_holder),
                    server_host=host,
                    server_port=port,
                )

                alice_chat = ClientChat(alice.send, lambda s, t, b: alice_msgs.append((s, t, b)), lambda *a: None)
                bob_chat = ClientChat(bob.send, lambda s, t, b: bob_msgs.append((s, t, b)), lambda *a: None)
                alice_chat.register_handlers(alice.register_handler)
                bob_chat.register_handlers(bob.register_handler)

                alice_ft = ClientFileTransferManager("alice", alice.send, lambda s: None, on_event=alice_events.append)
                bob_ft = ClientFileTransferManager("bob", bob.send, lambda s: None, on_event=bob_events.append)
                bob_ft.receiver.download_dir = tmp_dir / "bob_dl"
                bob_ft.receiver.download_dir.mkdir(parents=True, exist_ok=True)
                alice_ft_holder.append(alice_ft)
                bob_ft_holder.append(bob_ft)

                alice.start()
                bob.start()

                try:
                    time.sleep(0.15)
                    self.assertIsNotNone(infra_server.user_registry.get_session("alice"))
                    self.assertIsNotNone(infra_server.user_registry.get_session("bob"))

                    # Test broadcast + direct message over live TLS
                    alice_chat.send_broadcast("hello room")
                    bob_chat.send_direct("alice", "hi alice")

                    deadline = time.time() + 3.0
                    while time.time() < deadline and not (bob_msgs and alice_msgs):
                        time.sleep(0.02)
                    self.assertIn(("alice", "hello room", True), bob_msgs)
                    self.assertIn(("bob", "hi alice", False), alice_msgs)

                    # Test live TLS file transfer with SHA-256 verification
                    src_file = tmp_dir / "sample_e2e.bin"
                    src_bytes = b"E2E-TLS-DATA-" * 6000  # ~78 KB -> multi-chunk over real TLS
                    src_file.write_bytes(src_bytes)
                    alice_ft.send_file("bob", src_file)

                    deadline = time.time() + 5.0
                    while time.time() < deadline:
                        alice_done = any(e.get("event") == "completed" for e in alice_events)
                        bob_done = any(e.get("event") == "completed" for e in bob_events)
                        if alice_done and bob_done:
                            break
                        time.sleep(0.03)

                    self.assertTrue(any(e.get("event") == "completed" for e in alice_events))
                    self.assertTrue(any(e.get("event") == "completed" for e in bob_events))
                    self.assertEqual((bob_ft.receiver.download_dir / "sample_e2e.bin").read_bytes(), src_bytes)
                finally:
                    try:
                        alice_ft.cleanup("test done")
                    except Exception:
                        pass
                    try:
                        bob_ft.cleanup("test done")
                    except Exception:
                        pass
                    alice.logout(timeout=1.0)
                    bob.logout(timeout=1.0)
                    alice.close()
                    bob.close()
        finally:
            stop_event.set()
            tcp_sock.close()
            accept_thread.join(timeout=1.0)
            for t in client_threads:
                t.join(timeout=1.0)
            shutil.rmtree(tmp_dir, ignore_errors=True)
            for user in infra_server.user_registry.list_online():
                session_obj = infra_server.user_registry.get_session(user)
                if session_obj is not None:
                    infra_server.user_registry.remove_if_same(user, session_obj.connection)


if __name__ == "__main__":
    unittest.main()

"""Optional direct TCP P2P messaging (AES-256-GCM) with automatic server-relay fallback."""

from __future__ import annotations

from collections.abc import Callable
import secrets
import socket
import threading
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common import message_types as mt
from transfer.p2p import (
    get_local_p2p_hosts,
    make_p2p_cipher,
    recv_p2p_binary_frame,
    recv_p2p_json,
    send_p2p_binary_frame,
    send_p2p_json,
)

P2P_MAGIC_MSG = b"CMSG"


class P2PChatManager:
    """Manages optional AES-256-GCM direct TCP channels for 1-to-1 DM chat."""

    def __init__(
        self,
        username: str,
        send_signal_fn: Callable[[dict[str, Any]], None],
        on_message: Callable[[str, str], None],
        enabled: bool = False,
    ) -> None:
        self.username = username
        self._send_signal = send_signal_fn
        self._on_message = on_message
        self.enabled = bool(enabled)
        self._lock = threading.Lock()
        self._peers: dict[str, tuple[socket.socket, AESGCM, threading.Lock]] = {}
        self._pending_offers: dict[str, tuple[threading.Event, dict[str, Any] | None]] = {}
        self._listeners: list[socket.socket] = []

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable P2P direct chat; disabling closes cached peer sockets."""
        with self._lock:
            self.enabled = bool(enabled)
        if not enabled:
            self.close_all()

    def register_handlers(self, register_handler: Callable[[str, Callable[[dict], None]], None]) -> None:
        """Register P2P signaling message handlers on ClientSession."""
        register_handler(mt.P2P_CHAT_OFFER, self._handle_offer)
        register_handler(mt.P2P_CHAT_ACCEPT, self._handle_accept)

    def send_direct(self, recipient: str, text: str) -> bool:
        """Attempt direct AES-256-GCM P2P delivery; return False to fall back to Server Relay."""
        if not self.enabled:
            return False

        peer_entry = self._get_peer(recipient)
        if peer_entry is None:
            peer_entry = self._negotiate_peer(recipient)
            if peer_entry is None:
                return False

        sock, cipher, send_lock = peer_entry
        try:
            with send_lock:
                send_p2p_binary_frame(sock, P2P_MAGIC_MSG, 0, text.encode("utf-8"), cipher=cipher)
            return True
        except Exception:
            self._drop_peer(recipient, sock)
            return False

    def _get_peer(self, peer: str) -> tuple[socket.socket, AESGCM, threading.Lock] | None:
        with self._lock:
            return self._peers.get(peer)

    def _drop_peer(self, peer: str, sock: socket.socket | None = None) -> None:
        removed: socket.socket | None = None
        with self._lock:
            current = self._peers.get(peer)
            if current is not None and (sock is None or current[0] is sock):
                removed = self._peers.pop(peer)[0]
        if removed is not None:
            try:
                removed.close()
            except OSError:
                pass

    def _negotiate_peer(self, recipient: str) -> tuple[socket.socket, AESGCM, threading.Lock] | None:
        ev = threading.Event()
        with self._lock:
            self._pending_offers[recipient] = (ev, None)
        try:
            self._send_signal({"type": mt.P2P_CHAT_OFFER, "recipient": recipient})
        except Exception:
            with self._lock:
                self._pending_offers.pop(recipient, None)
            return None

        if not ev.wait(timeout=2.0):
            with self._lock:
                self._pending_offers.pop(recipient, None)
            return None

        with self._lock:
            _, response = self._pending_offers.pop(recipient, (ev, None))
        if not response or not response.get("accepted"):
            return None

        hosts = response.get("p2p_hosts")
        port = response.get("p2p_port")
        token = response.get("p2p_token")
        if (
            not isinstance(hosts, list)
            or not isinstance(port, int)
            or isinstance(port, bool)
            or not (1 <= port <= 65535)
            or not isinstance(token, str)
            or not token
        ):
            return None

        for host in hosts:
            if not isinstance(host, str) or not host:
                continue
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection((host, port), timeout=1.5)
                sock.settimeout(None)
                send_p2p_json(sock, {"username": self.username, "p2p_token": token})
                ack = recv_p2p_json(sock)
                if ack.get("ok") is True:
                    cipher = make_p2p_cipher(token)
                    entry = (sock, cipher, threading.Lock())
                    with self._lock:
                        old = self._peers.get(recipient)
                        self._peers[recipient] = entry
                    if old is not None:
                        try:
                            old[0].close()
                        except OSError:
                            pass
                    threading.Thread(
                        target=self._reader_loop,
                        args=(recipient, sock, cipher),
                        name=f"p2p-chat-{recipient}",
                        daemon=True,
                    ).start()
                    return entry
            except Exception:
                pass
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        return None

    def _handle_offer(self, message: dict[str, Any]) -> None:
        sender = message.get("sender")
        if not isinstance(sender, str) or not sender:
            return
        if not self.enabled:
            try:
                self._send_signal(
                    {"type": mt.P2P_CHAT_ACCEPT, "recipient": sender, "accepted": False}
                )
            except Exception:
                pass
            return

        srv_sock: socket.socket | None = None
        try:
            srv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            srv_sock.bind(("0.0.0.0", 0))
            srv_sock.listen(1)
            srv_sock.settimeout(5.0)
            port = int(srv_sock.getsockname()[1])
            token = secrets.token_hex(32)
            with self._lock:
                self._listeners.append(srv_sock)
            threading.Thread(
                target=self._accept_worker,
                args=(sender, srv_sock, token),
                name=f"p2p-chat-acc-{sender}",
                daemon=True,
            ).start()
            self._send_signal(
                {
                    "type": mt.P2P_CHAT_ACCEPT,
                    "recipient": sender,
                    "accepted": True,
                    "p2p_hosts": get_local_p2p_hosts(),
                    "p2p_port": port,
                    "p2p_token": token,
                }
            )
        except Exception:
            if srv_sock is not None:
                try:
                    srv_sock.close()
                except OSError:
                    pass

    def _handle_accept(self, message: dict[str, Any]) -> None:
        sender = message.get("sender")
        if not isinstance(sender, str):
            return
        with self._lock:
            pending = self._pending_offers.get(sender)
            if pending is None:
                return
            ev, _ = pending
            self._pending_offers[sender] = (ev, message)
        ev.set()

    def _accept_worker(self, expected_peer: str, srv_sock: socket.socket, expected_token: str) -> None:
        conn: socket.socket | None = None
        try:
            conn, _ = srv_sock.accept()
            conn.settimeout(None)
            hs = recv_p2p_json(conn)
            if hs.get("username") != expected_peer or not secrets.compare_digest(
                str(hs.get("p2p_token", "")), expected_token
            ):
                send_p2p_json(conn, {"ok": False})
                conn.close()
                return
            send_p2p_json(conn, {"ok": True})
            cipher = make_p2p_cipher(expected_token)
            entry = (conn, cipher, threading.Lock())
            with self._lock:
                old = self._peers.get(expected_peer)
                self._peers[expected_peer] = entry
            if old is not None:
                try:
                    old[0].close()
                except OSError:
                    pass
            self._reader_loop(expected_peer, conn, cipher)
        except Exception:
            if conn is not None:
                try:
                    conn.close()
                except OSError:
                    pass
        finally:
            with self._lock:
                if srv_sock in self._listeners:
                    self._listeners.remove(srv_sock)
            try:
                srv_sock.close()
            except OSError:
                pass

    def _reader_loop(self, peer: str, sock: socket.socket, cipher: AESGCM) -> None:
        try:
            while True:
                magic, _idx, payload = recv_p2p_binary_frame(sock, cipher=cipher)
                if magic == P2P_MAGIC_MSG:
                    text = payload.decode("utf-8")
                    self._on_message(peer, text)
        except Exception:
            pass
        finally:
            self._drop_peer(peer, sock)

    def close_all(self) -> None:
        """Close all open P2P chat connections and listening sockets."""
        with self._lock:
            peers = list(self._peers.values())
            self._peers.clear()
            listeners = list(self._listeners)
            self._listeners.clear()
            for ev, _ in self._pending_offers.values():
                ev.set()
            self._pending_offers.clear()
        for sock, _, _ in peers:
            try:
                sock.close()
            except OSError:
                pass
        for lsock in listeners:
            try:
                lsock.close()
            except OSError:
                pass

"""Direct TCP peer-to-peer transport helpers with binary framing and AES-256-GCM."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import struct
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.protocol import recv_exact

P2P_MAGIC_CHUNK = b"CHNK"
P2P_MAGIC_END = b"FEND"
_BINARY_HEADER_STRUCT = struct.Struct("!4sII")
_NONCE_SIZE = 12
_TAG_SIZE = 16
_MAX_P2P_FRAME_BYTES = 2 * 1024 * 1024


def make_p2p_cipher(p2p_token: str) -> AESGCM:
    """Derive a 256-bit AES-GCM cipher from the TLS-exchanged one-time P2P token."""
    key = hashlib.sha256(p2p_token.encode("utf-8")).digest()
    return AESGCM(key)


def get_local_p2p_hosts() -> list[str]:
    """Return candidate IPv4 addresses for direct P2P connections (LAN IPs first, then loopback)."""
    candidates: list[str] = []

    def _add(ip: str) -> None:
        if ip and ip != "0.0.0.0" and ip not in candidates:
            candidates.append(ip)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            _add(probe.getsockname()[0])
    except OSError:
        pass

    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if not ip.startswith("127."):
                _add(ip)
    except OSError:
        pass

    _add("127.0.0.1")
    return candidates


def send_p2p_json(sock: socket.socket, payload: dict[str, Any]) -> None:
    """Send a 4-byte length-prefixed JSON control message over a P2P socket."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sock.sendall(struct.pack("!I", len(body)) + body)


def recv_p2p_json(sock: socket.socket) -> dict[str, Any]:
    """Receive a 4-byte length-prefixed JSON control message from a P2P socket."""
    (length,) = struct.unpack("!I", recv_exact(sock, 4))
    if not 1 <= length <= 65536:
        raise ValueError(f"Invalid P2P control frame length: {length}")
    raw = recv_exact(sock, length)
    parsed = json.loads(raw.decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("P2P control payload must be a JSON object.")
    return parsed


def send_p2p_binary_frame(
    sock: socket.socket,
    magic: bytes,
    chunk_index: int,
    payload: bytes = b"",
    cipher: AESGCM | None = None,
) -> None:
    """Send a binary P2P frame (encrypted with AES-256-GCM when ``cipher`` is provided)."""
    wire_payload = payload
    if cipher is not None:
        nonce = os.urandom(_NONCE_SIZE)
        aad = magic + struct.pack("!I", chunk_index)
        wire_payload = nonce + cipher.encrypt(nonce, payload, aad)

    header = _BINARY_HEADER_STRUCT.pack(magic, chunk_index, len(wire_payload))
    if wire_payload:
        sock.sendall(header + wire_payload)
    else:
        sock.sendall(header)


def recv_p2p_binary_frame(
    sock: socket.socket,
    cipher: AESGCM | None = None,
) -> tuple[bytes, int, bytes]:
    """Receive a binary P2P frame and decrypt/authenticate it when ``cipher`` is provided."""
    raw_header = recv_exact(sock, _BINARY_HEADER_STRUCT.size)
    magic, chunk_index, length = _BINARY_HEADER_STRUCT.unpack(raw_header)
    if length > _MAX_P2P_FRAME_BYTES:
        raise ValueError(f"P2P binary frame too large: {length}")
    wire_payload = recv_exact(sock, length) if length > 0 else b""
    if cipher is not None:
        if len(wire_payload) < _NONCE_SIZE + _TAG_SIZE:
            raise ValueError("Encrypted P2P frame is truncated.")
        nonce = wire_payload[:_NONCE_SIZE]
        ciphertext = wire_payload[_NONCE_SIZE:]
        aad = magic + struct.pack("!I", chunk_index)
        payload = cipher.decrypt(nonce, ciphertext, aad)
    else:
        payload = wire_payload
    return magic, chunk_index, payload

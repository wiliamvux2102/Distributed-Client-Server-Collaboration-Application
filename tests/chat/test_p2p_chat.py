"""Tests for optional 2-mode switching (Client-Server default vs P2P AES-256-GCM DM)."""

from __future__ import annotations

from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from chat.p2p_chat import P2PChatManager
from chat.server_handler import handle_p2p_chat_signal
from transfer.receiver import FileReceiver
from transfer.sender import ClientFileTransferManager
from ui.gui.controller import SessionController


class P2PChatAndModeTests(unittest.TestCase):
    """Verify Client-Server is default, mode toggling works, and P2P DM encrypts + falls back."""

    def test_default_mode_is_client_server(self) -> None:
        recv = FileReceiver(username="alice", send_frame_fn=lambda h, p=b"": None)
        self.assertFalse(recv.enable_p2p)

        mgr = ClientFileTransferManager(
            username="alice",
            send_fn=lambda m: None,
            print_fn=lambda s: None,
        )
        self.assertFalse(mgr.receiver.enable_p2p)
        mgr.set_p2p_enabled(True)
        self.assertTrue(mgr.receiver.enable_p2p)

        ctrl = SessionController()
        self.assertFalse(ctrl.p2p_enabled)
        ctrl.set_p2p_mode(True)
        self.assertTrue(ctrl.p2p_enabled)
        ctrl.shutdown_sync(timeout=0.5)

    def test_p2p_direct_chat_aes_256_gcm_and_fallback(self) -> None:
        received_bob: list[tuple[str, str]] = []
        received_alice: list[tuple[str, str]] = []
        done = threading.Event()

        peers: dict[str, P2PChatManager] = {}

        def relay_signal(from_user: str, msg: dict) -> None:
            ctx = SimpleNamespace(
                username=from_user,
                send=lambda _m: None,
                send_to_user=lambda target, fwd: (
                    peers[target]._handle_offer(fwd)
                    if fwd.get("type") == "P2P_CHAT_OFFER"
                    else peers[target]._handle_accept(fwd)
                )
                or True,
                list_online=lambda: list(peers.keys()),
            )
            handle_p2p_chat_signal(ctx, msg)

        alice = P2PChatManager(
            username="alice",
            send_signal_fn=lambda m: relay_signal("alice", m),
            on_message=lambda s, t: received_alice.append((s, t)),
            enabled=True,
        )
        bob = P2PChatManager(
            username="bob",
            send_signal_fn=lambda m: relay_signal("bob", m),
            on_message=lambda s, t: (received_bob.append((s, t)), done.set()),
            enabled=True,
        )
        peers["alice"] = alice
        peers["bob"] = bob

        try:
            self.assertTrue(alice.send_direct("bob", "hello P2P AES-GCM!"))
            self.assertTrue(done.wait(timeout=3.0))
            self.assertEqual(received_bob, [("alice", "hello P2P AES-GCM!")])

            # When bob switches back to Client-Server mode, alice falls back immediately
            bob.set_enabled(False)
            alice.set_enabled(False)
            alice.set_enabled(True)
            self.assertFalse(alice.send_direct("bob", "should fall back to server"))
        finally:
            alice.close_all()
            bob.close_all()


if __name__ == "__main__":
    unittest.main()

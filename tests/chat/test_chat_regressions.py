"""Regression coverage for chat validation and CLI prompt rendering."""

import contextlib
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

_ROOT = str(Path(__file__).resolve().parent.parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from chat.client_chat import ClientChat
from chat.server_handler import (
    handle_broadcast_message,
    handle_direct_message,
)
from infrastructure.client_connection import _report_error_safely
from ui import cli


class ChatValidationTests(unittest.TestCase):
    def test_server_rejects_direct_message_to_self(self) -> None:
        context = Mock()
        context.username = "hen"

        handle_direct_message(
            context,
            {"recipient": "hen", "text": "self message"},
        )

        context.send_to_user.assert_not_called()
        context.send.assert_called_once_with({
            "type": "DELIVERY_STATUS",
            "ok": False,
            "code": "RECIPIENT_SELF",
            "message": "Không thể gửi tin riêng cho chính mình.",
        })

    def test_cli_rejects_direct_message_to_self_before_sending(self) -> None:
        session = Mock()
        session.username = "hen"
        chat = Mock()

        with contextlib.redirect_stdout(io.StringIO()):
            should_continue = cli._process_input(
                "/msg hen self message",
                session,
                chat,
            )

        self.assertTrue(should_continue)
        chat.send_direct.assert_not_called()

    def test_invalid_unicode_is_rejected_without_encoding_exception(self) -> None:
        context = Mock()
        context.username = "hen"

        handle_broadcast_message(
            context,
            {"text": "invalid surrogate: \ud800"},
        )
        context.send.assert_called_once_with({
            "type": "DELIVERY_STATUS",
            "ok": False,
            "code": "INVALID_TEXT",
            "message": "Nội dung tin nhắn không hợp lệ.",
        })

        with self.assertRaisesRegex(ValueError, "UTF-8"):
            ClientChat._validate_text("invalid surrogate: \ud800")

    def test_error_callback_failure_does_not_escape_network_worker(self) -> None:
        def broken_callback(message: str) -> None:
            raise RuntimeError("UI callback failed")

        with self.assertLogs(
            "infrastructure.client_connection",
            level="ERROR",
        ):
            _report_error_safely(broken_callback, "network failure")


class CliPromptTests(unittest.TestCase):
    def test_async_callback_leaves_prompt_redraw_to_prompt_session(self) -> None:
        with patch("builtins.print") as print_mock:
            cli._print_async("background event")

        print_mock.assert_called_once_with("background event", flush=True)


if __name__ == "__main__":
    unittest.main()

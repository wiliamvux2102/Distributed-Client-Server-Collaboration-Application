"""Client-side chat commands and incoming chat event handling."""

from collections.abc import Callable

from common import message_types as mt
from common.app_config import CONFIG


MAX_CHAT_MESSAGE_BYTES = CONFIG["protocol"]["max_direct_message_bytes"]

SendMessage = Callable[[dict], None]
RegisterHandler = Callable[[str, Callable[[dict], None]], None]
MessageCallback = Callable[[str, str, bool], None]
StatusCallback = Callable[[bool, str, str | None, int | None], None]


class ClientChat:
    """Build outgoing chat requests and translate server responses to events.

    The send callback is expected to use the shared client connection. Incoming
    callbacks run on whichever thread dispatches messages from that connection.
    They should hand events to the UI safely instead of reading from stdin.
    """

    def __init__(
        self,
        send_message: SendMessage,
        on_message: MessageCallback,
        on_status: StatusCallback,
    ) -> None:
        self._send_message = send_message
        self._on_message = on_message
        self._on_status = on_status

    def send_direct(self, recipient: str, text: str) -> None:
        """Send a private message request through the shared session."""

        if not isinstance(recipient, str):
            raise ValueError("Username người nhận phải là chuỗi.")

        normalized_recipient = recipient.strip()
        if not normalized_recipient:
            raise ValueError("Cần nhập username người nhận.")

        self._send_message({
            "type": mt.DIRECT_MESSAGE,
            "recipient": normalized_recipient,
            "text": self._validate_text(text),
        })

    def send_broadcast(self, text: str) -> None:
        """Send a broadcast request through the shared session."""

        self._send_message({
            "type": mt.BROADCAST_MESSAGE,
            "text": self._validate_text(text),
        })

    def handle_message(self, message: dict) -> None:
        """Convert a server chat/status message into a UI callback event."""

        message_type = message.get("type")

        if message_type in (mt.DIRECT_DELIVERY, mt.BROADCAST_DELIVERY):
            sender = message.get("sender")
            text = message.get("text")
            if not isinstance(sender, str) or not isinstance(text, str):
                raise ValueError(
                    f"Bản tin {message_type} thiếu sender hoặc text hợp lệ."
                )

            self._on_message(
                sender,
                text,
                message_type == mt.BROADCAST_DELIVERY,
            )
            return

        if message_type == mt.DELIVERY_STATUS:
            ok = message.get("ok")
            status_text = message.get("message", "")
            code = message.get("code")
            delivered_count = message.get("delivered_count")

            if not isinstance(ok, bool) or not isinstance(status_text, str):
                raise ValueError("DELIVERY_STATUS có cấu trúc không hợp lệ.")
            if code is not None and not isinstance(code, str):
                raise ValueError("DELIVERY_STATUS.code phải là chuỗi.")
            if delivered_count is not None and (
                isinstance(delivered_count, bool)
                or not isinstance(delivered_count, int)
                or delivered_count < 0
            ):
                raise ValueError(
                    "DELIVERY_STATUS.delivered_count phải là số nguyên không âm."
                )

            self._on_status(ok, status_text, code, delivered_count)

    def register_handlers(self, register_handler: RegisterHandler) -> None:
        """Register chat responses with the connection's receive dispatcher."""

        register_handler(mt.DIRECT_DELIVERY, self.handle_message)
        register_handler(mt.BROADCAST_DELIVERY, self.handle_message)
        register_handler(mt.DELIVERY_STATUS, self.handle_message)

    @staticmethod
    def _validate_text(text: str) -> str:
        """Reject empty or oversized text before sending it to the server."""

        if not isinstance(text, str) or text == "":
            raise ValueError("Nội dung tin nhắn không được để trống.")

        try:
            text_size = len(text.encode("utf-8"))
        except UnicodeEncodeError as error:
            raise ValueError("Nội dung tin nhắn không phải UTF-8 hợp lệ.") from error

        if text_size > MAX_CHAT_MESSAGE_BYTES:
            raise ValueError(
                f"Tin nhắn vượt quá giới hạn {MAX_CHAT_MESSAGE_BYTES} byte."
            )

        return text

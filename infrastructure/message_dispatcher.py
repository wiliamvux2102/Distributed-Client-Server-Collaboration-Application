from collections.abc import Callable
from dataclasses import dataclass
import socket
import threading

from common.protocol import send_message


@dataclass
class ClientContext:
    """Các khả năng mà một handler được phép dùng cho client hiện tại."""

    username: str
    connection: socket.socket
    send_lock: threading.Lock
    send_to_user: Callable[[str, dict], bool]
    list_online: Callable[[], list[str]]

    def send(self, message: dict) -> None:
        """Gửi một frame tới client hiện tại, có dùng send_lock chung."""

        with self.send_lock:
            send_message(self.connection, message)


MessageHandler = Callable[[ClientContext, dict], None]


class MessageDispatcher:
    """Ánh xạ loại thông điệp sang hàm xử lý tương ứng."""

    def __init__(self) -> None:
        self._handlers: dict[str, MessageHandler] = {}

    def register(
        self,
        message_type: str,
        handler: MessageHandler,
    ) -> None:
        if not isinstance(message_type, str) or not message_type:
            raise ValueError("message_type phải là chuỗi không rỗng.")

        if message_type in self._handlers:
            raise ValueError(
                f"Handler cho {message_type} đã được đăng ký."
            )

        self._handlers[message_type] = handler

    def dispatch(
        self,
        context: ClientContext,
        message: dict,
    ) -> bool:
        handler = self._handlers.get(message.get("type"))

        if handler is None:
            return False

        handler(context, message)
        return True
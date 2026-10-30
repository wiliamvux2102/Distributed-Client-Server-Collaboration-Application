"""Server-side handlers for private and broadcast chat messages."""

from common import message_types as mt
from common.app_config import CONFIG
from infrastructure.authentication import normalize_username
from infrastructure.message_dispatcher import ClientContext, MessageDispatcher


MAX_CHAT_MESSAGE_BYTES = CONFIG["protocol"]["max_direct_message_bytes"]


def _send_delivery_error(
    context: ClientContext,
    code: str,
    message: str,
) -> None:
    """Return a consistent delivery failure to the requesting client."""

    context.send({
        "type": mt.DELIVERY_STATUS,
        "ok": False,
        "code": code,
        "message": message,
    })


def _validated_text(message: dict) -> tuple[str | None, str | None]:
    """Validate chat text and return (text, error_code)."""

    text = message.get("text")

    if not isinstance(text, str) or text == "":
        return None, "INVALID_TEXT"

    if len(text.encode("utf-8")) > MAX_CHAT_MESSAGE_BYTES:
        return None, "MESSAGE_TOO_LARGE"

    return text, None


def handle_direct_message(
    context: ClientContext,
    message: dict,
) -> None:
    """Validate and route a private message to one online username."""

    try:
        recipient = normalize_username(message.get("recipient"))
    except ValueError:
        _send_delivery_error(
            context,
            "INVALID_RECIPIENT",
            "Username người nhận không hợp lệ.",
        )
        return

    text, error_code = _validated_text(message)
    if error_code == "INVALID_TEXT":
        _send_delivery_error(
            context,
            error_code,
            "Nội dung tin nhắn không hợp lệ.",
        )
        return
    if error_code == "MESSAGE_TOO_LARGE":
        _send_delivery_error(
            context,
            error_code,
            f"Tin nhắn vượt quá giới hạn {MAX_CHAT_MESSAGE_BYTES} byte.",
        )
        return

    # text is a string here because _validated_text returned no error.
    delivered = context.send_to_user(
        recipient,
        {
            "type": mt.DIRECT_DELIVERY,
            "sender": context.username,
            "text": text,
        },
    )

    if delivered:
        context.send({
            "type": mt.DELIVERY_STATUS,
            "ok": True,
            "message": f"Đã chuyển tin tới {recipient}.",
        })
    else:
        _send_delivery_error(
            context,
            "RECIPIENT_OFFLINE",
            f"{recipient} hiện không online.",
        )


def handle_broadcast_message(
    context: ClientContext,
    message: dict,
) -> None:
    """Validate and forward a broadcast to all other online users."""

    text, error_code = _validated_text(message)
    if error_code == "INVALID_TEXT":
        _send_delivery_error(
            context,
            error_code,
            "Nội dung tin nhắn không hợp lệ.",
        )
        return
    if error_code == "MESSAGE_TOO_LARGE":
        _send_delivery_error(
            context,
            error_code,
            f"Tin nhắn vượt quá giới hạn {MAX_CHAT_MESSAGE_BYTES} byte.",
        )
        return

    delivery = {
        "type": mt.BROADCAST_DELIVERY,
        "sender": context.username,
        "text": text,
    }

    delivered_count = 0
    for username in context.list_online():
        # The CLI renders the sender's own message locally, so the server
        # forwards this broadcast only to the other online users.
        if username == context.username:
            continue

        if context.send_to_user(username, delivery):
            delivered_count += 1

    context.send({
        "type": mt.DELIVERY_STATUS,
        "ok": True,
        "delivered_count": delivered_count,
        "message": (
            f"Đã phát tin tới {delivered_count} người dùng đang online."
        ),
    })


def register_chat_handlers(dispatcher: MessageDispatcher) -> None:
    """Register TV2's chat handlers with the shared server dispatcher.

    TV1 must remove its current DIRECT_MESSAGE registration before calling
    this function; MessageDispatcher rejects duplicate registrations.
    """

    dispatcher.register(mt.DIRECT_MESSAGE, handle_direct_message)
    dispatcher.register(mt.BROADCAST_MESSAGE, handle_broadcast_message)

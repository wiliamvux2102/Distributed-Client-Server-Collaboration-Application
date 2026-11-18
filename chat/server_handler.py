"""Server-side handlers for private and broadcast chat messages."""

from common import message_types as mt
from common.app_config import CONFIG
from infrastructure.authentication import normalize_username
from infrastructure.message_dispatcher import ClientContext, MessageDispatcher


MAX_CHAT_MESSAGE_BYTES = CONFIG["protocol"]["max_direct_message_bytes"]
_history_store = None


def set_history_store(store) -> None:
    """Attach an optional HistoryStore for persisting chat messages."""
    global _history_store
    _history_store = store


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

    try:
        text_size = len(text.encode("utf-8"))
    except UnicodeEncodeError:
        return None, "INVALID_TEXT"

    if text_size > MAX_CHAT_MESSAGE_BYTES:
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

    if recipient == context.username:
        _send_delivery_error(
            context,
            "RECIPIENT_SELF",
            "Không thể gửi tin riêng cho chính mình.",
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
        if _history_store is not None:
            try:
                _history_store.save_direct_message(context.username, recipient, text)
            except Exception:
                pass
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

    if _history_store is not None:
        try:
            _history_store.save_broadcast_message(context.username, text)
        except Exception:
            pass

    context.send({
        "type": mt.DELIVERY_STATUS,
        "ok": True,
        "delivered_count": delivered_count,
        "message": (
            f"Đã phát tin tới {delivered_count} người dùng đang online."
        ),
    })


def handle_history_request(
    context: ClientContext,
    message: dict,
) -> None:
    """Return recent persisted chat and transfer history for the authenticated user."""
    raw_limit = message.get("limit", 100)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit <= 0:
        limit = 100
    else:
        limit = min(raw_limit, 500)

    messages = []
    transfers = []
    if _history_store is not None:
        try:
            messages = _history_store.get_user_history(context.username, limit=limit)
            transfers = _history_store.get_transfer_logs(context.username, limit=30)
        except Exception:
            messages = []
            transfers = []

    context.send({
        "type": mt.HISTORY_SYNC,
        "messages": messages,
        "transfers": transfers,
    })


def register_chat_handlers(dispatcher: MessageDispatcher, history_store=None) -> None:
    """Register chat handlers with the shared server dispatcher."""
    set_history_store(history_store)

    dispatcher.register(mt.DIRECT_MESSAGE, handle_direct_message)
    dispatcher.register(mt.BROADCAST_MESSAGE, handle_broadcast_message)
    dispatcher.register(mt.HISTORY_REQUEST, handle_history_request)

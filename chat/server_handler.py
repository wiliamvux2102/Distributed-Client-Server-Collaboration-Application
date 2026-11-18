"""Server-side handlers for private and broadcast chat messages."""

from common import message_types as mt
from common.app_config import CONFIG
from chat.rooms import RoomManager, normalize_room_id
from infrastructure.authentication import normalize_username
from infrastructure.message_dispatcher import ClientContext, MessageDispatcher


MAX_CHAT_MESSAGE_BYTES = CONFIG["protocol"]["max_direct_message_bytes"]
_history_store = None
_room_manager = RoomManager()


def set_history_store(store) -> None:
    """Attach an optional HistoryStore for persisting chat messages and rooms."""
    global _history_store, _room_manager
    _history_store = store
    _room_manager = RoomManager(history_store=store)


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


def handle_room_create(context: ClientContext, message: dict) -> None:
    """Create or join a named chat room."""
    try:
        created, room_id, members = _room_manager.create_room(
            message.get("room_id"), context.username
        )
    except ValueError as err:
        context.send({
            "type": mt.ROOM_STATUS,
            "ok": False,
            "action": "create",
            "room_id": str(message.get("room_id", "")),
            "message": str(err),
            "members": [],
        })
        return

    msg = f"Đã tạo và vào phòng {room_id}." if created else f"Đã vào phòng {room_id}."
    context.send({
        "type": mt.ROOM_STATUS,
        "ok": True,
        "action": "create",
        "room_id": room_id,
        "message": msg,
        "members": members,
    })


def handle_room_join(context: ClientContext, message: dict) -> None:
    """Join an existing chat room (creating it if needed)."""
    try:
        existed, room_id, members = _room_manager.join_room(
            message.get("room_id"), context.username
        )
    except ValueError as err:
        context.send({
            "type": mt.ROOM_STATUS,
            "ok": False,
            "action": "join",
            "room_id": str(message.get("room_id", "")),
            "message": str(err),
            "members": [],
        })
        return

    msg = f"Đã tham gia phòng {room_id}." if existed else f"Đã tạo và tham gia phòng {room_id}."
    context.send({
        "type": mt.ROOM_STATUS,
        "ok": True,
        "action": "join",
        "room_id": room_id,
        "message": msg,
        "members": members,
    })


def handle_room_leave(context: ClientContext, message: dict) -> None:
    """Leave a chat room."""
    try:
        was_member, room_id, members = _room_manager.leave_room(
            message.get("room_id"), context.username
        )
    except ValueError as err:
        context.send({
            "type": mt.ROOM_STATUS,
            "ok": False,
            "action": "leave",
            "room_id": str(message.get("room_id", "")),
            "message": str(err),
            "members": [],
        })
        return

    context.send({
        "type": mt.ROOM_STATUS,
        "ok": was_member,
        "action": "leave",
        "room_id": room_id,
        "message": f"Đã rời phòng {room_id}." if was_member else f"Bạn chưa tham gia {room_id}.",
        "members": members,
    })


def handle_room_list(context: ClientContext, _message: dict) -> None:
    """Return list of all rooms and their members."""
    context.send({
        "type": mt.ROOM_LIST_RESULT,
        "rooms": _room_manager.list_rooms(context.username),
    })


def handle_room_message(context: ClientContext, message: dict) -> None:
    """Validate and deliver a message to all online members of a room."""
    try:
        room_id = normalize_room_id(message.get("room_id"))
    except ValueError as err:
        _send_delivery_error(context, "INVALID_ROOM", str(err))
        return

    if not _room_manager.is_member(room_id, context.username):
        _send_delivery_error(
            context,
            "NOT_ROOM_MEMBER",
            f"Bạn chưa tham gia phòng {room_id}.",
        )
        return

    text, error_code = _validated_text(message)
    if error_code == "INVALID_TEXT":
        _send_delivery_error(context, error_code, "Nội dung tin nhắn không hợp lệ.")
        return
    if error_code == "MESSAGE_TOO_LARGE":
        _send_delivery_error(
            context,
            error_code,
            f"Tin nhắn vượt quá giới hạn {MAX_CHAT_MESSAGE_BYTES} byte.",
        )
        return

    online_set = set(context.list_online())
    delivery = {
        "type": mt.ROOM_DELIVERY,
        "room_id": room_id,
        "sender": context.username,
        "text": text,
    }
    delivered_count = 0
    for member in _room_manager.get_members(room_id):
        if member == context.username or member not in online_set:
            continue
        if context.send_to_user(member, delivery):
            delivered_count += 1

    if _history_store is not None:
        try:
            _history_store.save_room_message(room_id, context.username, text)
        except Exception:
            pass

    context.send({
        "type": mt.DELIVERY_STATUS,
        "ok": True,
        "delivered_count": delivered_count,
        "message": f"Đã gửi tới {delivered_count} thành viên online trong {room_id}.",
    })


def register_chat_handlers(dispatcher: MessageDispatcher, history_store=None) -> None:
    """Register chat handlers with the shared server dispatcher."""
    set_history_store(history_store)

    dispatcher.register(mt.DIRECT_MESSAGE, handle_direct_message)
    dispatcher.register(mt.BROADCAST_MESSAGE, handle_broadcast_message)
    dispatcher.register(mt.HISTORY_REQUEST, handle_history_request)
    dispatcher.register(mt.ROOM_CREATE, handle_room_create)
    dispatcher.register(mt.ROOM_JOIN, handle_room_join)
    dispatcher.register(mt.ROOM_LEAVE, handle_room_leave)
    dispatcher.register(mt.ROOM_LIST, handle_room_list)
    dispatcher.register(mt.ROOM_MESSAGE, handle_room_message)

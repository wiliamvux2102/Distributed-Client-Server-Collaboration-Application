import socket
import threading
import secrets
import ssl

from common.protocol import recv_message, send_message
from common import message_types as mt
from common.console import configure_console_utf8
from infrastructure.authentication import normalize_username
from infrastructure.user_registry import user_registry
from infrastructure.udp_service import serve_discovery
from infrastructure.security import create_server_context
from infrastructure.message_dispatcher import (
    ClientContext,
    MessageDispatcher,
)
from chat.server_handler import register_chat_handlers
from transfer.server_handler import register_file_transfer_handlers, file_transfer_server_handler
from common.app_config import CONFIG

SERVER_CONFIG = CONFIG["server"]
PORT = SERVER_CONFIG["tcp_port"]
HOST = SERVER_CONFIG["tcp_bind_host"]

# Thời gian chờ của socket lắng nghe.
ACCEPT_TIMEOUT = SERVER_CONFIG["accept_timeout_seconds"]

# Thời gian chờ thao tác trên kết nối của từng client.
CLIENT_TIMEOUT = SERVER_CONFIG["pre_login_timeout_seconds"]

def send_locked(
    connection: socket.socket,
    send_lock: object,
    message: dict,
) -> None:
    """Gửi trọn một bản tin trong khi giữ lock của kết nối."""

    with send_lock:
        send_message(connection, message)

def handle_echo(context: ClientContext, message: dict) -> None:
    text = message.get("text")

    if not isinstance(text, str):
        context.send({
            "type": mt.ERROR,
            "code": "INVALID_TEXT",
            "message": "Trường text phải là chuỗi.",
        })
        return

    context.send({
        "type": mt.ECHO_RESULT,
        "text": text,
    })


def handle_list_online(
    context: ClientContext,
    message: dict,
) -> None:
    context.send({
        "type": mt.ONLINE_LIST,
        "users": context.list_online(),
    })


def create_message_dispatcher() -> MessageDispatcher:
    dispatcher = MessageDispatcher()
    dispatcher.register(mt.ECHO, handle_echo)
    dispatcher.register(mt.LIST_ONLINE, handle_list_online)
    register_chat_handlers(dispatcher)
    register_file_transfer_handlers(dispatcher)
    return dispatcher


def handle_client(
    connection: socket.socket,
    address: tuple[str, int],
    tls_context: ssl.SSLContext,
    dispatcher: MessageDispatcher,
) -> None:
    client_context: ClientContext | None = None
    username = None
    send_lock = threading.Lock()

    try:
        # Timeout áp dụng cho cả TLS handshake trước đăng nhập.
        connection.settimeout(CLIENT_TIMEOUT)

        # connection ban đầu là TCP socket thường.
        # Sau dòng này, nó là socket TLS.
        connection = tls_context.wrap_socket(
            connection,
            server_side=True,
        )

        with connection:
            print(f"[SERVER] Bắt đầu phục vụ {address}")

            send_locked(
                connection,
                send_lock,
                {
                    "type": mt.SERVER_READY,
                    "message": "Hãy gửi yêu cầu LOGIN.",
                },
            )

            while True:
                message = recv_message(connection)
                message_type = message["type"]

                if username is None:
                    if message_type != mt.LOGIN:
                        send_locked(
                            connection,
                            send_lock,
                            {
                                "type": mt.LOGIN_ERROR,
                                "code": "LOGIN_REQUIRED",
                                "message": "Bạn cần đăng nhập trước.",
                            },
                        )
                        continue

                    try:
                        requested_username = normalize_username(
                            message.get("username")
                        )
                    except ValueError as error:
                        send_locked(
                            connection,
                            send_lock,
                            {
                                "type": mt.LOGIN_ERROR,
                                "code": "INVALID_USERNAME",
                                "message": str(error),
                            },
                        )
                        continue

                    with send_lock:
                        heartbeat_token = secrets.token_urlsafe(32)
                        registered = user_registry.register(
                            requested_username,
                            connection,
                            address,
                            send_lock,
                            heartbeat_token,
                        )

                        if registered:
                            username = requested_username

                            print(f"[SERVER] {username} đã đăng nhập từ {address}")

                            send_message(
                                connection,
                                {
                                    "type": mt.LOGIN_OK,
                                    "username": username,
                                    "heartbeat_token": heartbeat_token,
                                },
                            )

                            connection.settimeout(None)

                            client_context = ClientContext(
                                username=username,
                                connection=connection,
                                send_lock=send_lock,
                                send_to_user=send_to_user,
                                list_online=user_registry.list_online,
                            )

                    if not registered:
                        send_locked(
                            connection,
                            send_lock,
                            {
                                "type": mt.LOGIN_ERROR,
                                "code": "USERNAME_TAKEN",
                                "message": "Username đang được sử dụng.",
                            },
                        )
                        continue

                    continue

                # Từ đây trở xuống, client đã đăng nhập. Request nghiệp vụ
                # được định tuyến thống nhất qua dispatcher.
                if message_type == mt.QUIT:
                    send_locked(
                        connection,
                        send_lock,
                        {
                            "type": mt.BYE,
                            "message": "Đã kết thúc phiên.",
                        },
                    )
                    break

                if client_context is None:
                    raise RuntimeError(
                        "Client đã đăng nhập nhưng chưa có ClientContext."
                    )

                handled = dispatcher.dispatch(client_context, message)

                if not handled:
                    send_locked(
                        connection,
                        send_lock,
                        {
                            "type": mt.ERROR,
                            "code": "UNKNOWN_TYPE",
                            "message": "Loại bản tin chưa được hỗ trợ.",
                        },
                    )

    except socket.timeout:
        print(f"[SERVER] {address}: quá thời gian chờ.")

    except EOFError as error:
        print(f"[SERVER] {address}: kết nối kết thúc: {error}")

    except ssl.SSLError as error:
        print(f"[TLS] Bắt tay TLS thất bại với {address}: {error}")

    except (OSError, ValueError) as error:
        print(f"[SERVER] {address}: lỗi xử lý: {error}")

    finally:
        if username is not None:
            removed = user_registry.remove_if_same(
                username,
                connection,
            )

            if removed:
                print(f"[SERVER] {username} đã offline.")
            file_transfer_server_handler.on_client_disconnect(username)


        try:
            connection.close()
        except OSError:
            pass

        print(f"[SERVER] Kết thúc phiên {address}")


def send_to_user(username: str, message: dict) -> bool:
    """Gửi một bản tin tới người đang online có username tương ứng."""

    session = user_registry.get_session(username)

    if session is None:
        return False

    try:
        # Giữ khóa trong suốt lúc gửi frame để tránh xen byte.
        send_locked(
            session.connection,
            session.send_lock,
            message,
        )

        return True

    except OSError:
        # Gửi thất bại: phiên có thể đã mất kết nối.
        try:
            session.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

        # Chỉ dọn nếu registry vẫn chứa đúng phiên này.
        user_registry.remove_if_same(
            username,
            session.connection,
        )

        return False

def main() -> None:
    configure_console_utf8()

    # Nạp chứng chỉ và tạo context một lần trước khi nhận client.
    server_tls_context = create_server_context()
    message_dispatcher = create_message_dispatcher()
    udp_stop_event = threading.Event()

    udp_worker = threading.Thread(
        target=serve_discovery,
        args=(udp_stop_event,),
        name="udp-discovery",
        daemon=True,
    )
    udp_worker.start()

    # Listener TCP phải nhận kết nối từ các giao diện mạng,
    # để client LAN kết nối được sau khi tìm thấy server.
    host = HOST

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
            server_socket.bind((host, PORT))
            server_socket.listen()
            server_socket.settimeout(ACCEPT_TIMEOUT)

            print(f"[SERVER] Đang lắng nghe TCP tại {host}:{PORT}")

            while True:
                try:
                    connection, address = server_socket.accept()
                except socket.timeout:
                    continue

                worker = threading.Thread(
                    target=handle_client,
                    args=(
                        connection,
                        address,
                        server_tls_context,
                        message_dispatcher,
                    ),
                    name=f"client-{address[0]}:{address[1]}",
                    daemon=True,
                )
                worker.start()

    finally:
        udp_stop_event.set()
        udp_worker.join(timeout=1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SERVER] Dừng bằng Ctrl+C.")

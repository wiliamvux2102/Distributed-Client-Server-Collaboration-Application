import socket
import threading

from common.protocol import recv_message, send_message
from common import message_types as mt
from infrastructure.authentication import normalize_username
from infrastructure.user_registry import user_registry

HOST = "127.0.0.1"
PORT = 5000

# Thời gian chờ của socket lắng nghe.
ACCEPT_TIMEOUT = 0.5

# Thời gian chờ thao tác trên kết nối của từng client.
CLIENT_TIMEOUT = 60.0


def handle_client(
    connection: socket.socket,
    address: tuple[str, int],
) -> None:
    username = None

    try:
        with connection:
            connection.settimeout(CLIENT_TIMEOUT)

            print(f"[SERVER] Bắt đầu phục vụ {address}")

            send_message(
                connection,
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
                        send_message(
                            connection,
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
                        send_message(
                            connection,
                            {
                                "type": mt.LOGIN_ERROR,
                                "code": "INVALID_USERNAME",
                                "message": str(error),
                            },
                        )
                        continue

                    registered = user_registry.register(
                        requested_username,
                        connection,
                        address,
                    )

                    if not registered:
                        send_message(
                            connection,
                            {
                                "type": mt.LOGIN_ERROR,
                                "code": "USERNAME_TAKEN",
                                "message": "Username đang được sử dụng.",
                            },
                        )
                        continue

                    # Chỉ đánh dấu đã đăng nhập sau khi registry chấp nhận.
                    username = requested_username

                    print(f"[SERVER] {username} đã đăng nhập từ {address}")

                    send_message(
                        connection,
                        {
                            "type": mt.LOGIN_OK,
                            "username": username,
                        },
                    )
                    continue

                # Từ đây trở xuống, client đã đăng nhập.
                if message_type == mt.LIST_ONLINE:
                    online_users = user_registry.list_online()

                    send_message(
                        connection,
                        {
                            "type": mt.ONLINE_LIST,
                            "users": online_users,
                        },
                    )
                    continue

                if message_type == mt.ECHO:
                    text = message.get("text")

                    if not isinstance(text, str):
                        send_message(
                            connection,
                            {
                                "type": mt.ERROR,
                                "code": "INVALID_TEXT",
                                "message": "Trường text phải là chuỗi.",
                            },
                        )
                        continue

                    send_message(
                        connection,
                        {
                            "type": mt.ECHO_RESULT,
                            "text": text,
                        },
                    )
                    continue

                if message_type == mt.QUIT:
                    send_message(
                        connection,
                        {
                            "type": mt.BYE,
                            "message": "Đã kết thúc phiên.",
                        },
                    )
                    break

                send_message(
                    connection,
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

        print(f"[SERVER] Kết thúc phiên {address}")
        

def send_to_user(username: str, message: dict) -> bool:
    """Gửi một bản tin tới người đang online có username tương ứng."""

    session = user_registry.get_session(username)

    if session is None:
        return False

    try:
        # Giữ khóa trong suốt lúc gửi frame để tránh xen byte.
        with session.send_lock:
            send_message(session.connection, message)

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
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
        server_socket.bind((HOST, PORT))
        server_socket.listen()
        server_socket.settimeout(ACCEPT_TIMEOUT)

        print(f"[SERVER] Đang lắng nghe tại {HOST}:{PORT}")

        while True:
            try:
                connection, address = server_socket.accept()

            except socket.timeout:
                continue

            print(f"[SERVER] Tiếp nhận kết nối từ {address}")

            worker = threading.Thread(
                target=handle_client,
                args=(connection, address),
                name=f"client-{address[0]}:{address[1]}",
                daemon=True,
            )

            worker.start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[SERVER] Dừng bằng Ctrl+C.")
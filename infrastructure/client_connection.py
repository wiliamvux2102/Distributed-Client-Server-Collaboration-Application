import socket

from common.protocol import recv_message, send_message
from common import message_types as mt


SERVER_HOST = "127.0.0.1"
SERVER_PORT = 5000

SERVER_TIMEOUT = 5.0


def main() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(SERVER_TIMEOUT)
        connection.connect((SERVER_HOST, SERVER_PORT))

        greeting = recv_message(connection)

        if greeting["type"] != mt.SERVER_READY:
            raise ValueError("Server chưa sẵn sàng.")

        print(f"[CLIENT] Server: {greeting.get('message')}")

        username = input("Username: ").strip()

        send_message(
            connection,
            {
                "type": mt.LOGIN,
                "username": username,
            },
        )

        login_response = recv_message(connection)

        if login_response["type"] != mt.LOGIN_OK:
            print(
                f"[CLIENT] Đăng nhập lỗi "
                f"{login_response.get('code')}: "
                f"{login_response.get('message')}"
            )
            return

        print(
            f"[CLIENT] Đăng nhập thành công với tên "
            f"{login_response['username']}."
        )

        print("[CLIENT] Nhập nội dung; /quit để thoát.")

        while True:
            text = input("Bạn: ")

            if text == "":
                print("[CLIENT] Hãy nhập nội dung.")
                continue

            if text == "/quit":
                request = {"type": mt.QUIT}

            elif text == "/online":
                request = {"type": mt.LIST_ONLINE}

            else:
                request = {
                    "type": mt.ECHO,
                    "text": text,
                }

            send_message(connection, request)
            response = recv_message(connection)

            if response["type"] == mt.ONLINE_LIST:
                print(f"[CLIENT] Đang online: {response['users']}")

            elif response["type"] == mt.ECHO_RESULT:
                print(f"[CLIENT] Echo: {response.get('text')}")

            elif response["type"] == mt.ERROR:
                print(
                    f"[CLIENT] Lỗi {response.get('code')}: "
                    f"{response.get('message')}"
                )

            elif response["type"] == mt.BYE:
                print(f"[CLIENT] Server: {response.get('message')}")
                break

            else:
                raise ValueError(
                    f"Phản hồi không mong đợi: {response['type']}"
                )

    print("[CLIENT] Đã đóng kết nối.")


if __name__ == "__main__":
    try:
        main()

    except socket.timeout:
        print("[CLIENT] Quá thời gian chờ server.")

    except EOFError as error:
        print(f"[CLIENT] Luồng nhận kết thúc: {error}")

    except (OSError, ValueError) as error:
        print(f"[CLIENT] Lỗi: {error}")

    except KeyboardInterrupt:
        print("\n[CLIENT] Dừng bằng Ctrl+C.")
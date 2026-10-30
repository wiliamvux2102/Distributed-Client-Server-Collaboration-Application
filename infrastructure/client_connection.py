import socket
import threading
import json
import ssl
from common.protocol import recv_message, send_message
from common import message_types as mt

from infrastructure.security import (
    TLS_SERVER_NAME,
    create_client_context,
)
from common.app_config import CONFIG

SERVER_CONFIG = CONFIG["server"]
UDP_CONFIG = CONFIG["udp"]
HEARTBEAT_CONFIG = CONFIG["heartbeat"]

SERVER_TIMEOUT = SERVER_CONFIG["tcp_connect_timeout_seconds"]

DISCOVERY_TARGET = UDP_CONFIG["discovery_target"]
DISCOVERY_PORT = UDP_CONFIG["port"]
DISCOVERY_TIMEOUT = UDP_CONFIG["discovery_timeout_seconds"]
DISCOVERY_ATTEMPTS = UDP_CONFIG["discovery_attempts"]
PROTOCOL_VERSION = UDP_CONFIG["protocol_version"]
MAX_DATAGRAM_SIZE = 2048

HEARTBEAT_INTERVAL = HEARTBEAT_CONFIG["interval_seconds"]

def discover_server() -> tuple[str, int]:
    """Tìm server qua UDP broadcast và trả về (IP, TCP port)."""

    request = {
        "type": mt.UDP_DISCOVER,
        "protocol_version": PROTOCOL_VERSION,
    }
    request_bytes = json.dumps(request).encode("utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1,
        )
        udp_socket.settimeout(DISCOVERY_TIMEOUT)

        for attempt in range(1, DISCOVERY_ATTEMPTS + 1):
            udp_socket.sendto(
                request_bytes,
                (DISCOVERY_TARGET, DISCOVERY_PORT),
            )

            try:
                datagram, server_address = udp_socket.recvfrom(
                    MAX_DATAGRAM_SIZE
                )
            except socket.timeout:
                print(
                    f"[CLIENT] Chưa nhận phản hồi discovery "
                    f"(lần {attempt}/{DISCOVERY_ATTEMPTS})."
                )
                continue

            try:
                response = json.loads(datagram.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue

            if not isinstance(response, dict):
                continue

            if response.get("type") != mt.UDP_DISCOVERY_RESPONSE:
                continue

            if response.get("protocol_version") != PROTOCOL_VERSION:
                continue

            tcp_port = response.get("tcp_port")

            if not isinstance(tcp_port, int) or not 1 <= tcp_port <= 65535:
                continue

            # IP server là IP nguồn của datagram phản hồi.
            return server_address[0], tcp_port

    raise TimeoutError("Không tìm thấy server qua UDP discovery.")

def connect_tls(server_host: str, server_port: int) -> ssl.SSLSocket:
    """Kết nối tới IP tìm được và xác minh chứng chỉ server bằng TLS."""

    raw_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    raw_socket.settimeout(SERVER_TIMEOUT)

    try:
        raw_socket.connect((server_host, server_port))

        tls_socket = create_client_context().wrap_socket(
            raw_socket,
            server_hostname=TLS_SERVER_NAME,
        )
        return tls_socket

    except Exception:
        raw_socket.close()
        raise

def send_heartbeats(
    server_host: str,
    token: str,
    stop_event: threading.Event,
) -> None:
    """Gửi heartbeat UDP định kỳ trong lúc client còn hoạt động."""

    heartbeat = json.dumps({
        "type": mt.UDP_HEARTBEAT,
        "token": token,
    }).encode("utf-8")

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        while not stop_event.is_set():
            try:
                udp_socket.sendto(
                    heartbeat,
                    (server_host, DISCOVERY_PORT),
                )
            except OSError as error:
                print(f"[CLIENT] Không gửi được heartbeat: {error}")

            if stop_event.wait(HEARTBEAT_INTERVAL):
                break


def receive_messages(
    connection: socket.socket,
    stop_event: threading.Event,
) -> None:
    """Thread duy nhất đọc bản tin server gửi về."""

    while not stop_event.is_set():
        try:
            message = recv_message(connection)

        except (EOFError, OSError, ValueError) as error:
            if not stop_event.is_set():
                print(f"\n[CLIENT] Kết nối kết thúc: {error}")

            stop_event.set()
            return

        message_type = message["type"]

        if message_type == mt.ONLINE_LIST:
            print(f"\n[CLIENT] Đang online: {message['users']}")

        elif message_type == mt.ECHO_RESULT:
            print(f"\n[CLIENT] Echo: {message.get('text')}")

        elif message_type == mt.ERROR:
            print(
                f"\n[CLIENT] Lỗi {message.get('code')}: "
                f"{message.get('message')}"
            )

        elif message_type == mt.BYE:
            print(f"\n[CLIENT] Server: {message.get('message')}")
            stop_event.set()
            return

        elif message_type == mt.DIRECT_DELIVERY:
            print(
                f"\n[TIN RIÊNG] {message['sender']}: "
                f"{message['text']}"
            )

        elif message_type == mt.DELIVERY_STATUS:
            if message.get("ok"):
                print(f"\n[SERVER] {message.get('message')}")
            else:
                print(
                    f"\n[SERVER] Lỗi {message.get('code')}: "
                    f"{message.get('message')}"
                )

        else:
            print(f"\n[CLIENT] Bản tin chưa xử lý: {message_type}")


def main() -> None:
    stop_event = threading.Event()
    receiver = None
    heartbeat_sender = None

    server_host, server_port = discover_server()
    print(f"[CLIENT] Tìm thấy server tại {server_host}:{server_port}")

    # connect_tls() kết nối TCP rồi hoàn tất TLS handshake.
    with connect_tls(server_host, server_port) as connection:
        try:
            # Chỉ đọc SERVER_READY sau khi TLS handshake hoàn tất.
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

            heartbeat_token = login_response.get("heartbeat_token")

            if not isinstance(heartbeat_token, str):
                raise ValueError(
                    "Server không gửi heartbeat_token hợp lệ."
                )

            print(
                f"[CLIENT] Đăng nhập thành công với tên "
                f"{login_response['username']}."
            )

            # Giữ kết nối TCP chờ dữ liệu; heartbeat xử lý liveness.
            connection.settimeout(None)

            # Thread receiver là luồng duy nhất đọc TCP sau đăng nhập.
            receiver = threading.Thread(
                target=receive_messages,
                args=(connection, stop_event),
                name="server-receiver",
                daemon=True,
            )
            receiver.start()

            # Heartbeat vẫn chạy qua UDP, độc lập với TLS/TCP.
            heartbeat_sender = threading.Thread(
                target=send_heartbeats,
                args=(server_host, heartbeat_token, stop_event),
                name="heartbeat-sender",
                daemon=True,
            )
            heartbeat_sender.start()

            print(
                "[CLIENT] Nhập nội dung; "
                "/online để xem danh sách; /quit để thoát."
            )

            while not stop_event.is_set():
                text = input("Bạn: ")

                # Receiver có thể đã phát hiện server ngắt kết nối
                # trong lúc main đang chờ nhập.
                if stop_event.is_set():
                    break

                if text == "":
                    print("[CLIENT] Hãy nhập nội dung.")
                    continue

                if text == "/quit":
                    send_message(
                        connection,
                        {
                            "type": mt.QUIT,
                        },
                    )

                    # Receiver nhận BYE rồi đặt stop_event.
                    if not stop_event.wait(timeout=5.0):
                        print("[CLIENT] Server chưa xác nhận thoát.")

                    break

                if text == "/online":
                    request = {
                        "type": mt.LIST_ONLINE,
                    }

                elif text.startswith("/msg "):
                    parts = text.split(" ", 2)

                    if len(parts) != 3:
                        print("Cách dùng: /msg <username> <nội dung>")
                        continue

                    request = {
                        "type": mt.DIRECT_MESSAGE,
                        "recipient": parts[1],
                        "text": parts[2],
                    }

                else:
                    request = {
                        "type": mt.ECHO,
                        "text": text,
                    }

                # Main chỉ gửi TCP; receiver là luồng duy nhất nhận TCP.
                send_message(connection, request)

        finally:
            # Dừng receiver và heartbeat sender khi rời khỏi phiên.
            stop_event.set()

            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                # Socket có thể đã đóng bởi server.
                pass

            if receiver is not None:
                receiver.join(timeout=1.0)

            if heartbeat_sender is not None:
                heartbeat_sender.join(timeout=1.0)

    print("[CLIENT] Đã đóng kết nối.")


if __name__ == "__main__":
    try:
        main()

    except socket.timeout:
        print("[CLIENT] Quá thời gian chờ server.")

    except EOFError as error:
        print(f"[CLIENT] Luồng nhận kết thúc: {error}")

    except ssl.SSLError as error:
        print(f"[TLS] Không thiết lập hoặc xác minh được TLS: {error}")

    except (OSError, ValueError) as error:
        print(f"[CLIENT] Lỗi: {error}")

    except KeyboardInterrupt:
        print("\n[CLIENT] Dừng bằng Ctrl+C.")

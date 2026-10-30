# TV1: Xử lý UDP discovery, heartbeat và timeout.
import json
import socket
import threading
from infrastructure.user_registry import user_registry
from common import message_types as mt
from common.app_config import CONFIG


UDP_CONFIG = CONFIG["udp"]
SERVER_CONFIG = CONFIG["server"]
HEARTBEAT_CONFIG = CONFIG["heartbeat"]

UDP_BIND_HOST = UDP_CONFIG["bind_host"]
UDP_DISCOVERY_PORT = UDP_CONFIG["port"]
TCP_SERVER_PORT = SERVER_CONFIG["tcp_port"]
PROTOCOL_VERSION = UDP_CONFIG["protocol_version"]
UDP_WAIT_INTERVAL = UDP_CONFIG["wait_interval_seconds"]
MAX_DATAGRAM_SIZE = 2048
HEARTBEAT_TIMEOUT = HEARTBEAT_CONFIG["timeout_seconds"]

def disconnect_expired_sessions() -> None:
    expired_sessions = user_registry.remove_stale(HEARTBEAT_TIMEOUT)

    for username, session in expired_sessions:
        try:
            session.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Socket có thể đã được đóng bởi luồng TCP.
            pass

        print(f"[UDP] {username} hết heartbeat, đã đóng phiên.")

def serve_discovery(stop_event: threading.Event) -> None:
    """Nghe yêu cầu discovery và heartbeat qua UDP."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.bind((UDP_BIND_HOST, UDP_DISCOVERY_PORT))
        udp_socket.settimeout(UDP_WAIT_INTERVAL)

        print(
            f"[UDP] Đang nghe discovery và heartbeat tại "
            f"{UDP_BIND_HOST}:{UDP_DISCOVERY_PORT}"
        )

        while not stop_event.is_set():
            # Kiểm tra timeout mỗi vòng, kể cả khi recvfrom hết thời gian chờ.
            disconnect_expired_sessions()

            try:
                datagram, client_address = udp_socket.recvfrom(
                    MAX_DATAGRAM_SIZE
                )
            except socket.timeout:
                continue

            try:
                request = json.loads(datagram.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue

            if not isinstance(request, dict):
                continue

            request_type = request.get("type")

            if request_type == mt.UDP_HEARTBEAT:
                token = request.get("token")

                if isinstance(token, str):
                    user_registry.refresh_heartbeat(
                        token,
                        client_address[0],
                    )

                # Heartbeat là tín hiệu một chiều, không cần phản hồi UDP.
                continue

            if request_type != mt.UDP_DISCOVER:
                continue

            if request.get("protocol_version") != PROTOCOL_VERSION:
                continue

            response = {
                "type": mt.UDP_DISCOVERY_RESPONSE,
                "protocol_version": PROTOCOL_VERSION,
                "tcp_port": TCP_SERVER_PORT,
            }

            response_bytes = json.dumps(response).encode("utf-8")

            try:
                udp_socket.sendto(response_bytes, client_address)
                print(f"[UDP] Đã trả discovery cho {client_address}")
            except OSError as error:
                print(f"[UDP] Gửi phản hồi thất bại: {error}")

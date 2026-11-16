import json
import logging
import socket
import sys
import threading
from infrastructure.user_registry import user_registry
from common import message_types as mt
from common.app_config import CONFIG


logger = logging.getLogger(__name__)


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

def _disable_udp_connreset(sock: socket.socket) -> None:
    """Trên Windows, vô hiệu hóa WSAECONNRESET khi nhận ICMP Port Unreachable."""
    if sys.platform == "win32":
        try:
            import ctypes
            SIO_UDP_CONNRESET = 0x9800000C
            val = ctypes.c_ulong(0)
            cb = ctypes.c_ulong(0)
            ctypes.windll.ws2_32.WSAIoctl(
                sock.fileno(),
                SIO_UDP_CONNRESET,
                ctypes.byref(val),
                ctypes.sizeof(val),
                None,
                0,
                ctypes.byref(cb),
                None,
                None,
            )
        except Exception:
            pass

def disconnect_expired_sessions() -> None:
    expired_sessions = user_registry.remove_stale(HEARTBEAT_TIMEOUT)

    for username, session in expired_sessions:
        try:
            session.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            # Socket có thể đã được đóng bởi luồng TCP.
            pass

        print(f"[UDP] {username} hết heartbeat, đã đóng phiên.")
        try:
            from transfer.server_handler import file_transfer_server_handler
            file_transfer_server_handler.on_client_disconnect(username)
        except Exception:
            logger.exception("[UDP] Lỗi dọn dẹp phiên truyền file của '%s'.", username)

    try:
        from transfer.server_handler import file_transfer_server_handler
        pruned = file_transfer_server_handler.prune_stale_sessions(timeout_seconds=60.0)
        for sid in pruned:
            print(f"[TRANSFER] Đã dọn dẹp phiên file bị treo do timeout: {sid}")
    except Exception:
        logger.exception("[UDP] Lỗi quét phiên truyền file quá hạn.")

def serve_discovery(stop_event: threading.Event) -> None:
    """Nghe yêu cầu discovery và heartbeat qua UDP."""

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        _disable_udp_connreset(udp_socket)
        udp_socket.bind((UDP_BIND_HOST, UDP_DISCOVERY_PORT))
        udp_socket.settimeout(UDP_WAIT_INTERVAL)

        print(
            f"[UDP] Đang nghe discovery và heartbeat tại "
            f"{UDP_BIND_HOST}:{UDP_DISCOVERY_PORT}"
        )

        while not stop_event.is_set():
            try:
                # Kiểm tra timeout mỗi vòng, kể cả khi recvfrom hết thời gian chờ.
                disconnect_expired_sessions()

                try:
                    datagram, client_address = udp_socket.recvfrom(
                        MAX_DATAGRAM_SIZE
                    )
                except (socket.timeout, ConnectionResetError, OSError):
                    continue

                try:
                    request = json.loads(datagram.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue

                if not isinstance(request, dict):
                    continue

                request_type = request.get("type")

                if request_type == mt.UDP_HEARTBEAT:
                    username = request.get("username")
                    ts = request.get("ts")
                    sig = request.get("sig")

                    if (
                        isinstance(username, str)
                        and isinstance(ts, int)
                        and not isinstance(ts, bool)
                        and isinstance(sig, str)
                    ):
                        user_registry.refresh_heartbeat_signed(
                            username,
                            ts,
                            sig,
                            client_address[0],
                        )

                    # Heartbeat là tín hiệu một chiều, không cần phản hồi UDP.
                    continue

                if request_type != mt.UDP_DISCOVER:
                    continue

                req_version = request.get("protocol_version")
                if (
                    isinstance(req_version, bool)
                    or not isinstance(req_version, int)
                    or req_version != PROTOCOL_VERSION
                ):
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
            except Exception:
                logger.exception("[UDP] Lỗi không mong muốn khi xử lý gói tin UDP.")

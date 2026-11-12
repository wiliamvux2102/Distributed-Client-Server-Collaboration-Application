import socket
import threading
import json
import ssl
import logging
from collections.abc import Callable
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

MessageHandler = Callable[[dict], None]
ErrorHandler = Callable[[str], None]


def _report_error_safely(handler: ErrorHandler, message: str) -> None:
    """Keep a faulty UI error callback from stopping a network worker."""

    try:
        handler(message)
    except Exception:
        logging.getLogger(__name__).exception(
            "Client error callback raised an exception."
        )


def _get_discovery_targets() -> list[str]:
    """Lấy danh sách các địa chỉ đích broadcast tìm server trên tất cả card mạng."""
    targets = [DISCOVERY_TARGET]
    if DISCOVERY_TARGET == "255.255.255.255":
        try:
            _, _, ips = socket.gethostbyname_ex(socket.gethostname())
            for ip in ips:
                parts = ip.split(".")
                if len(parts) == 4 and not parts[0].startswith("127"):
                    directed = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if directed not in targets:
                        targets.append(directed)
        except Exception:
            pass
    return targets


def discover_server() -> tuple[str, int]:
    """Tìm server qua UDP broadcast và trả về (IP, TCP port)."""

    request = {
        "type": mt.UDP_DISCOVER,
        "protocol_version": PROTOCOL_VERSION,
    }
    request_bytes = json.dumps(request).encode("utf-8")
    targets = _get_discovery_targets()

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
        udp_socket.setsockopt(
            socket.SOL_SOCKET,
            socket.SO_BROADCAST,
            1,
        )
        udp_socket.settimeout(DISCOVERY_TIMEOUT)

        for _ in range(DISCOVERY_ATTEMPTS):
            for target in targets:
                try:
                    udp_socket.sendto(
                        request_bytes,
                        (target, DISCOVERY_PORT),
                    )
                except OSError:
                    continue

            try:
                datagram, server_address = udp_socket.recvfrom(
                    MAX_DATAGRAM_SIZE
                )
            except socket.timeout:
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
    on_error: ErrorHandler,
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
                _report_error_safely(
                    on_error,
                    f"Không gửi được heartbeat: {error}",
                )

            if stop_event.wait(HEARTBEAT_INTERVAL):
                break


class ClientSession:
    """Authenticated client connection with one TCP reader and callbacks."""

    def __init__(
        self,
        connection: ssl.SSLSocket,
        server_host: str,
        username: str,
        heartbeat_token: str,
        on_error: ErrorHandler,
        on_unhandled: MessageHandler,
    ) -> None:
        self._connection = connection
        self.server_host = server_host
        self.username = username
        self._heartbeat_token = heartbeat_token
        self._on_error = on_error
        self._on_unhandled = on_unhandled
        self._stop_event = threading.Event()
        self._bye_event = threading.Event()
        self._send_lock = threading.Lock()
        self._handlers_lock = threading.Lock()
        self._handlers: dict[str, list[MessageHandler]] = {}
        self._receiver_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

    @classmethod
    def connect(
        cls,
        username: str,
        on_error: ErrorHandler,
        on_unhandled: MessageHandler,
    ) -> "ClientSession":
        """Discover, connect with TLS, authenticate, and return a session."""

        if not isinstance(username, str) or not username.strip():
            raise ValueError("Username không được để trống.")

        server_host, server_port = discover_server()
        connection = connect_tls(server_host, server_port)

        try:
            greeting = recv_message(connection)
            if greeting.get("type") != mt.SERVER_READY:
                raise ValueError("Server chưa sẵn sàng.")

            send_message(connection, {
                "type": mt.LOGIN,
                "username": username.strip(),
            })
            login_response = recv_message(connection)

            if login_response.get("type") != mt.LOGIN_OK:
                raise ValueError(
                    f"Đăng nhập lỗi {login_response.get('code')}: "
                    f"{login_response.get('message')}"
                )

            heartbeat_token = login_response.get("heartbeat_token")
            authenticated_username = login_response.get("username")
            if not isinstance(heartbeat_token, str):
                raise ValueError("Server không gửi heartbeat_token hợp lệ.")
            if not isinstance(authenticated_username, str):
                raise ValueError("Server không gửi username hợp lệ.")

            connection.settimeout(None)
            return cls(
                connection=connection,
                server_host=server_host,
                username=authenticated_username,
                heartbeat_token=heartbeat_token,
                on_error=on_error,
                on_unhandled=on_unhandled,
            )
        except Exception:
            connection.close()
            raise

    def register_handler(
        self,
        message_type: str,
        handler: MessageHandler,
    ) -> None:
        """Register a callback for a server message type."""

        if not isinstance(message_type, str) or not message_type:
            raise ValueError("message_type phải là chuỗi không rỗng.")

        with self._handlers_lock:
            self._handlers.setdefault(message_type, []).append(handler)

    def send(self, message: dict) -> None:
        """Send a message through the shared connection lock."""

        if self._stop_event.is_set():
            raise RuntimeError("Client session đã đóng.")

        with self._send_lock:
            send_message(self._connection, message)

    def start(self) -> None:
        """Start the single TCP receiver and UDP heartbeat threads."""

        if self._receiver_thread is not None:
            raise RuntimeError("Client session đã được khởi động.")

        self._receiver_thread = threading.Thread(
            target=self._receive_loop,
            name="client-receiver",
            daemon=True,
        )
        self._heartbeat_thread = threading.Thread(
            target=send_heartbeats,
            args=(
                self.server_host,
                self._heartbeat_token,
                self._stop_event,
                self._on_error,
            ),
            name="client-heartbeat",
            daemon=True,
        )
        self._receiver_thread.start()
        self._heartbeat_thread.start()

    @property
    def is_closed(self) -> bool:
        """Whether the session has stopped receiving/sending messages."""

        return self._stop_event.is_set()

    def logout(self, timeout: float = 5.0) -> bool:
        """Request QUIT and wait for the server's BYE response."""

        if self.is_closed:
            return True

        self.send({"type": mt.QUIT})
        if self._bye_event.wait(timeout):
            return True

        _report_error_safely(
            self._on_error,
            "Server chưa xác nhận thoát trong thời gian chờ.",
        )
        return False

    def close(self) -> None:
        """Stop background workers and close the TLS socket."""

        self._stop_event.set()
        try:
            self._connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._connection.close()
        except OSError:
            pass

        current_thread = threading.current_thread()
        for worker in (self._receiver_thread, self._heartbeat_thread):
            if worker is not None and worker is not current_thread:
                worker.join(timeout=1.0)

    def _receive_loop(self) -> None:
        """Read TCP frames and dispatch each message to registered callbacks."""

        while not self._stop_event.is_set():
            try:
                message = recv_message(self._connection)
            except (EOFError, OSError, ValueError, ssl.SSLError) as error:
                if not self._stop_event.is_set():
                    _report_error_safely(
                        self._on_error,
                        f"Kết nối kết thúc: {error}",
                    )
                self._stop_event.set()
                return

            message_type = message["type"]
            with self._handlers_lock:
                handlers = tuple(self._handlers.get(message_type, ()))

            if handlers:
                for handler in handlers:
                    try:
                        handler(message)
                    except Exception as error:
                        _report_error_safely(
                            self._on_error,
                            f"Lỗi xử lý bản tin {message_type}: {error}"
                        )
            else:
                try:
                    self._on_unhandled(message)
                except Exception as error:
                    _report_error_safely(
                        self._on_error,
                        f"Lỗi xử lý bản tin chưa đăng ký "
                        f"{message_type}: {error}"
                    )

            if message_type == mt.BYE:
                self._bye_event.set()
                self._stop_event.set()
                return


def main() -> None:
    """Compatibility entry point; the actual CLI lives in ui.cli."""

    from ui.cli import main as run_cli

    run_cli()


if __name__ == "__main__":
    main()

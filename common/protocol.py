import socket
import struct
import json
from common.app_config import CONFIG


# "!I":
# ! = thứ tự byte mạng, tức big-endian
# I = số nguyên không dấu dài 4 byte
HEADER_FORMAT = "!I"

HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

# Giới hạn nội dung mỗi frame: 1 MiB.
MAX_PAYLOAD_SIZE = CONFIG["protocol"]["max_payload_bytes"]


def recv_exact(connection: socket.socket, size: int) -> bytes:
    """Đọc đủ size byte hoặc báo lỗi nếu kết nối kết thúc sớm."""

    received = bytearray()

    while len(received) < size:
        # Chỉ yêu cầu số byte còn thiếu.
        remaining = size - len(received)

        chunk = connection.recv(remaining)

        if chunk == b"":
            raise EOFError(
                f"Kết nối đã đóng khi mới nhận "
                f"{len(received)}/{size} byte."
            )

        received.extend(chunk)

    return bytes(received)


def send_frame(connection: socket.socket, payload: bytes) -> None:
    """Tạo header độ dài và gửi một frame hoàn chỉnh."""

    payload_size = len(payload)

    if not 1 <= payload_size <= MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Độ dài nội dung phải từ 1 đến "
            f"{MAX_PAYLOAD_SIZE} byte."
        )

    # Chuyển độ dài từ số nguyên thành 4 byte.
    header = struct.pack(HEADER_FORMAT, payload_size)

    # Header đứng trước, nội dung đứng sau.
    connection.sendall(header + payload)


def recv_frame(connection: socket.socket) -> bytes:
    """Nhận một frame và trả về phần nội dung."""

    # Bước 1: đọc đủ header.
    header = recv_exact(connection, HEADER_SIZE)

    # Bước 2: chuyển header thành số nguyên.
    payload_size = struct.unpack(HEADER_FORMAT, header)[0]

    # Bước 3: kiểm tra trước khi đọc nội dung.
    if not 1 <= payload_size <= MAX_PAYLOAD_SIZE:
        raise ValueError(
            f"Frame khai báo độ dài không hợp lệ: {payload_size}."
        )

    # Bước 4: đọc đủ phần nội dung.
    return recv_exact(connection, payload_size)


def send_message(
    connection: socket.socket,
    message: dict,
) -> None:
    """Chuyển dictionary thành JSON và gửi trong một frame."""

    if not isinstance(message, dict):
        raise ValueError("Bản tin gửi phải là dictionary.")

    message_type = message.get("type")

    if not isinstance(message_type, str) or not message_type:
        raise ValueError("Bản tin phải có type là chuỗi không rỗng.")

    # Dictionary → văn bản JSON.
    json_text = json.dumps(
        message,
        ensure_ascii=False,
    )

    # Văn bản JSON → byte UTF-8.
    payload = json_text.encode("utf-8")

    # Dùng lại cơ chế framing đã có.
    send_frame(connection, payload)


def recv_message(connection: socket.socket) -> dict:
    """Nhận một frame, giải mã JSON và kiểm tra cấu trúc chung."""

    payload = recv_frame(connection)

    # Byte UTF-8 → văn bản JSON.
    json_text = payload.decode("utf-8")

    # Văn bản JSON → đối tượng Python.
    message = json.loads(json_text)

    if not isinstance(message, dict):
        raise ValueError("Nội dung bản tin phải là JSON object.")

    message_type = message.get("type")

    if not isinstance(message_type, str) or not message_type:
        raise ValueError("Bản tin phải có type là chuỗi không rỗng.")

    return message

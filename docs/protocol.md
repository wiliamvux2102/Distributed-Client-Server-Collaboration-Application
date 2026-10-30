# Đặc tả giao thức ứng dụng

## Quy ước chung

TCP dùng frame độ dài cố định ở phần header, theo định dạng:

```text
+----------------------+------------------------------+
| Length: 4 bytes      | Payload: Length bytes        |
| unsigned big-endian  | UTF-8 JSON object            |
+----------------------+------------------------------+
```

- Header được đóng/mở bằng `struct` format `!I`: số nguyên không dấu 4 byte theo network byte order (big-endian).
- Payload phải có độ dài từ 1 byte tới `1,048,576` byte (1 MiB).
- Payload là JSON UTF-8 object và phải có trường `type` là chuỗi không rỗng.
- TCP là stream; bên nhận phải đọc đủ header và đủ payload theo độ dài header. Một lần `recv()` không đồng nghĩa với một message.
- TLS handshake diễn ra trước message ứng dụng đầu tiên. Sau TLS handshake, `SERVER_READY` và các frame TCP đều được truyền trong TLS.
- UDP discovery và heartbeat là JSON datagram riêng, không dùng framing TCP.

## Các message TCP hiện có

| Type | Hướng | Các trường chính | Ý nghĩa |
|---|---|---|---|
| `SERVER_READY` | Server → client | `message` | Server đã hoàn tất TLS handshake và sẵn sàng nhận LOGIN. |
| `LOGIN` | Client → server | `username` | Yêu cầu đăng nhập. Username dài 3–20 ký tự ASCII, gồm chữ, số và `_`. |
| `LOGIN_OK` | Server → client | `username`, `heartbeat_token` | Đăng nhập thành công; token gắn với session. |
| `LOGIN_ERROR` | Server → client | `code`, `message` | LOGIN không hợp lệ hoặc username đang được dùng. |
| `ECHO` | Client → server | `text` | Yêu cầu server phản hồi lại nội dung. |
| `ECHO_RESULT` | Server → client | `text` | Nội dung echo. |
| `LIST_ONLINE` | Client → server | — | Yêu cầu danh sách username online. |
| `ONLINE_LIST` | Server → client | `users` | Danh sách username online. |
| `DIRECT_MESSAGE` | Client → server | `recipient`, `text` | Gửi tin riêng tới username khác. `text` tối đa 64 KiB theo số byte UTF-8. |
| `DIRECT_DELIVERY` | Server → người nhận | `sender`, `text` | Tin riêng được chuyển tới người nhận. |
| `BROADCAST_MESSAGE` | Client → server | `text` | Gửi tin chung. Server lấy username người gửi từ session đã đăng nhập. `text` tối đa 64 KiB theo số byte UTF-8. |
| `BROADCAST_DELIVERY` | Server → các client khác đang online | `sender`, `text` | Tin chat chung được chuyển tới người nhận; server không gửi lại cho người gửi. |
| `DELIVERY_STATUS` | Server → người gửi | `ok`, `message`; có thể có `code`, `delivered_count` | Kết quả chuyển tin riêng hoặc chat chung. `ok` cho biết thao tác gửi được server xử lý; chat chung có thể thành công với `delivered_count: 0`. |
| `QUIT` | Client → server | — | Yêu cầu kết thúc session. |
| `BYE` | Server → client | `message` | Xác nhận kết thúc session. |
| `ERROR` | Server → client | `code`, `message` | Lỗi request hoặc loại message không hỗ trợ. |

### Mã lỗi đang được dùng

- Đăng nhập: `LOGIN_REQUIRED`, `INVALID_USERNAME`, `USERNAME_TAKEN`.
- Chat riêng: `INVALID_RECIPIENT`, `INVALID_TEXT`, `MESSAGE_TOO_LARGE`, `RECIPIENT_OFFLINE`.
- Chat chung: `INVALID_TEXT`, `MESSAGE_TOO_LARGE`.
- Request chung: `UNKNOWN_TYPE`; handler echo có thể trả `INVALID_TEXT`.

## UDP discovery

Client gửi datagram JSON tới server UDP cổng 5001:

```json
{"type":"UDP_DISCOVER","protocol_version":1}
```

Server trả:

```json
{
  "type": "UDP_DISCOVERY_RESPONSE",
  "protocol_version": 1,
  "tcp_port": 5000
}
```

Client lấy địa chỉ IP server từ IP nguồn của datagram phản hồi; IP không nằm trong JSON response. Client gửi tối đa ba lần, mỗi lần chờ một giây.

## UDP heartbeat

Sau LOGIN_OK, client gửi định kỳ:

```json
{"type":"UDP_HEARTBEAT","token":"<session token>"}
```

- Chu kỳ gửi hiện tại: 10 giây.
- Server xem session stale sau 35 giây không nhận heartbeat.
- Server xác minh token và so khớp IP nguồn với IP của TCP session.
- Heartbeat là một chiều, không có ACK.
- UDP không được TLS bảo vệ; token và datagram hiện truyền plaintext.

## Trạng thái triển khai

`ECHO`, `LIST_ONLINE`, `DIRECT_MESSAGE` và `BROADCAST_MESSAGE` được đăng ký trong `MessageDispatcher`. Các handler chat trong `chat/server_handler.py` dùng interface `(ClientContext, message)`. Client dùng `ClientSession` để giữ một luồng nhận TCP, đăng ký handler theo `type` và gửi qua send lock chung. `ui/cli.py` sở hữu vòng lặp lệnh. `QUIT` vẫn được xử lý trực tiếp trong connection handler để kết thúc vòng đời phiên. Các loại message truyền file sẽ được bổ sung khi tích hợp handler của TV3.

# Hợp đồng tích hợp chat của thành viên 2

## Mục đích và trạng thái

Tài liệu này ghi lại giao diện hạ tầng và điểm nối giữa hạ tầng của TV1 với chat/CLI của TV2. Các quyết định ban đầu đã được nhóm xác nhận tạm thời; điểm nối server và client chat đã được triển khai trên nhánh `feature/chat-ui`.

Mục tiêu là giữ nguyên framing, TLS, kết nối và session của TV1; TV2 bổ sung nghiệp vụ chat và giao diện theo phân công.

## Hợp đồng hiện có đã xác nhận

### Framing và bản tin

- TCP dùng header độ dài 4 byte big-endian theo `!I`, theo sau là payload JSON UTF-8.
- Mỗi bản tin là một JSON object có trường cấp cao nhất `type` và các trường dữ liệu cấp cao nhất khác. Hiện không có object `payload` bọc ngoài.
- Kết nối TCP được bảo vệ bằng TLS. UDP discovery và heartbeat dùng datagram riêng.
- Dùng `common.protocol.send_message()` và `recv_message()`; module chat không tự đóng/mở frame hoặc gọi `recv()` trực tiếp.

### Dispatcher phía server

- Đăng ký handler bằng `dispatcher.register(message_type, handler)`.
- Handler nhận `(context: ClientContext, message: dict)`.
- `ClientContext` hiện cung cấp:
  - `context.username`: username lấy từ session đã xác thực.
  - `context.send(message)`: gửi cho client hiện tại, dùng send lock của session.
  - `context.send_to_user(username, message) -> bool`: gửi cho một người đang online.
  - `context.list_online() -> list[str]`: lấy danh sách username đang online.
- `send_to_user()` trả `True` nếu gửi frame vào socket thành công; đây không phải xác nhận người nhận đã đọc tin.
- `infrastructure.server.create_message_dispatcher()` đăng ký `ECHO`, `LIST_ONLINE` và gọi `register_chat_handlers()` để thêm các handler chat. `QUIT` được connection handler xử lý trực tiếp.

### Chat riêng phía server hiện có

- Client gửi `DIRECT_MESSAGE` với các trường `recipient` và `text`.
- Server kiểm tra username người nhận, nội dung và giới hạn byte; dùng `context.username` làm danh tính người gửi.
- Nếu gửi được, server chuyển `DIRECT_DELIVERY` với `sender` và `text`; nếu không, trả `DELIVERY_STATUS` với `ok: false` và `code: RECIPIENT_OFFLINE`.
- Server trả `DELIVERY_STATUS` với `ok: true` khi hàm gửi tới socket của người nhận thành công.

### Client session và CLI hiện có

- `ClientSession.connect(username, ...)` dò server, thiết lập TLS và đăng nhập; nó trả về session đã xác thực.
- `session.register_handler(type, callback)` cho phép nhiều callback đăng ký theo loại bản tin.
- `session.start()` khởi chạy một luồng duy nhất đọc TCP và một luồng heartbeat UDP. Callback lỗi và bản tin chưa đăng ký được chuyển tới CLI.
- `session.send(message)` dùng khóa gửi chung cho các module trên kết nối.
- `session.logout()` gửi `QUIT` và chờ `BYE`; `session.close()` dừng luồng nền và đóng socket.
- `ui/cli.py` sở hữu vòng lặp lệnh và đăng ký callback hiển thị online list, lỗi, tin chat và trạng thái.

## Giao thức chat chung đã triển khai

Giữ định dạng JSON phẳng hiện tại và thêm hai loại bản tin. Không đổi framing, không thêm envelope mới và không tạo kết nối TCP khác.

### Client gửi chat chung

```json
{
  "type": "BROADCAST_MESSAGE",
  "text": "Chào cả nhóm"
}
```

Server lấy danh tính từ `context.username`, không nhận `sender` do client tự khai.

### Server chuyển chat chung

```json
{
  "type": "BROADCAST_DELIVERY",
  "sender": "hen",
  "text": "Chào cả nhóm"
}
```

Server chuyển tin tới các client đang online, trừ người gửi; CLI hiển thị tin đã gửi ở phía người gửi. Server trả `DELIVERY_STATUS` cho người gửi kèm `delivered_count`.

### Handler đăng ký phía server

TV2 cung cấp `register_chat_handlers(dispatcher)` trong `chat/server_handler.py`, đăng ký `DIRECT_MESSAGE` và `BROADCAST_MESSAGE`. `infrastructure.server.create_message_dispatcher()` gọi hàm này; handler chat riêng cũ đã được chuyển sang module chat để tránh đăng ký trùng.

TV1 sở hữu điểm gọi trong `infrastructure/server.py:create_message_dispatcher()` và các hằng số trong `common/message_types.py`. Điểm nối server đã được tích hợp: `create_message_dispatcher()` gọi `register_chat_handlers()`.

## Điểm nối phía client đã triển khai

`infrastructure.client_connection.ClientSession` cung cấp session dùng chung cho CLI/chat/transfer:

```python
session.send(message: dict) -> None
session.register_handler(message_type: str, callback) -> None
```

- `send()` dùng send lock chung của kết nối để các frame TCP không xen byte khi nhiều module cùng gửi.
- `register_handler()` đăng ký callback nhận nguyên dictionary của bản tin. Có thể đăng ký nhiều callback cho cùng loại bản tin.
- TV2 dùng `chat/client_chat.py` để tạo/xử lý bản tin chat và `ui/cli.py` để đọc lệnh, hiển thị sự kiện.
- TV1 sở hữu kết nối, TLS, đăng nhập, heartbeat và luồng đọc; TV2 không tự tạo reader thứ hai trên cùng socket.

## Ranh giới trách nhiệm

| Phần | Chủ sở hữu | Phạm vi |
|---|---|---|
| Framing, message transport, message type constants, kết nối/TLS, đăng nhập, heartbeat, registry | TV1 | `common/*`, `infrastructure/*` |
| Handler chat riêng/chung và logic phía client | TV2 | `chat/*` |
| CLI, lệnh chat, hiển thị tin/lỗi/trạng thái | TV2 | `ui/cli.py` |
| Metadata, chunk, hash, truyền/nhận file và sự kiện transfer | TV3 | `transfer/*` |

TV1 chịu trách nhiệm nối handler server và callback nhận client vào hạ tầng. TV2 cung cấp handler/callback; TV3 cung cấp API và sự kiện truyền file để CLI tích hợp.

## Trạng thái các quyết định

1. Đã xác nhận tạm thời: dùng `BROADCAST_MESSAGE` và `BROADCAST_DELIVERY`; broadcast không gửi lại người gửi.
2. Đã tích hợp: `register_chat_handlers(dispatcher)` đăng ký chat riêng và chat chung; handler chat riêng cũ đã được chuyển khỏi `infrastructure/server.py`.
3. Đã tích hợp: `ClientSession` cung cấp `send()` và `register_handler()`; receiver tiếp tục là luồng duy nhất đọc TCP.
4. Đã xác nhận tạm thời: `DELIVERY_STATUS.ok` phản ánh việc server gửi được frame tới socket đích, không đảm bảo client đích đã hiển thị tin.

Phần server và client chat đã dùng chung protocol hiện tại mà không thay đổi framing. Phần truyền file sẽ được nối khi TV3 bàn giao API và event của module transfer.

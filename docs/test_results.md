# Kết quả kiểm thử hệ thống

## Môi trường

- Windows, chạy lệnh bằng PowerShell (`.venv`).
- Python 3.13, raw sockets (`socket`), TLS qua Python `ssl`, JSON framing dùng chung, `prompt_toolkit` (CLI) và `PySide6` (Desktop GUI).

## Kiểm thử tự động (91/91 Đạt)

Chạy bằng `python -m unittest discover -s tests -t . -v` hoặc `python -m pytest -q` → **91/91 bài test ĐẠT (OK)**:

- `tests/infrastructure/test_server_infrastructure.py` (19 tests): Đăng ký user, danh sách online, từ chối login trùng (`USERNAME_TAKEN`), xác thực heartbeat token/IP, timeout dọn dẹp stale sessions; chuẩn hóa username; đóng frame TCP 4-byte header, ghép mảnh TCP bị phân mẩu (`3-byte chunks`), bắt lỗi EOF/malformed JSON; định tuyến dispatcher.
- `tests/chat/test_chat_regressions.py` (5 tests): Chặn gửi tin nhắn cho chính mình, xử lý callback an toàn, từ chối ký tự Unicode lỗi, tích hợp CLI.
- `tests/transfer/test_integrity.py` (9 tests): Tính SHA-256 theo chunk, trích xuất metadata file thường/file rỗng, phát hiện lệch kích thước và lệch mã băm SHA-256.
- `tests/transfer/test_sender.py` (15 tests): Khởi tạo `FILE_INIT`, truyền chunk ở luồng nền sau `FILE_ACCEPT`, xử lý đường dẫn có dấu ngoặc kép/khoảng trắng, hủy truyền, thanh tiến trình CLI và callback sự kiện có cấu trúc (`on_event`).
- `tests/transfer/test_receiver.py` (10 tests): Tạo file `.part`, chống Path Traversal (`../../`), kiểm tra thứ tự chunk, tự động đánh số `file (1).ext` khi trùng tên, xóa `.part` khi sai hash hoặc ngắt kết nối, khóa file và quét dọn `.part` rác khi khởi động.
- `tests/transfer/test_server_handler.py` (15 tests): Định tuyến `FILE_INIT`/`FILE_CHUNK`/`FILE_END`/`FILE_RESULT`, kiểm tra metadata, từ chối trùng `session_id`, State Machine nghiêm ngặt, dọn phiên treo (`prune_stale_sessions`) và báo `ERR_PEER_DISCONNECTED` khi một phía rớt mạng.
- `tests/transfer/test_transfer.py` (6 tests): Kiểm thử End-to-End 5 kịch bản (file rỗng 0B, file nhỏ 1 chunk, file lớn đa chunk, file nhị phân raw bytes `0..255`, sửa đổi dữ liệu và ngắt kết nối đột ngột).
- `tests/ui/test_gui_session_and_models.py` (12 tests): Kiểm thử `ChatSessionStore` (routing `#all`/DM, unread), `MessageListModel` (giới hạn bộ nhớ), trạng thái `AWAITING_VERIFICATION` → `COMPLETED`, chống double-connect, throttle progress, lọc callback phiên cũ (`session_gen`), phân biệt lỗi handler với ngắt kết nối đột ngột của `client-receiver`, và gửi file broadcast (`#all` / `/sendfile all`).

## Kết quả

| Kịch bản | Kết quả | Ghi chú |
|---|---|---|
| UDP discovery qua loopback | Đạt | Client tìm được server và nhận TCP port 5000. |
| UDP discovery qua địa chỉ interface `192.168.56.1` | Đạt | Client tìm được server tại địa chỉ này và đăng nhập. Đây chưa phải bằng chứng kiểm thử giữa hai máy vật lý. |
| Login, echo, danh sách online, tin riêng | Đạt | Các luồng cơ bản đã được chạy thủ công. |
| Heartbeat khi client còn hoạt động | Đạt theo kiểm thử thủ công | Client để idle vẫn giữ session online nhờ heartbeat. |
| Heartbeat stale | Đạt theo kiểm thử thủ công | Khi heartbeat ngừng, server xóa username khỏi registry và đóng TCP sau timeout. Cấu hình hiện tại: gửi 10 giây/lần, timeout 35 giây. |
| TLS client-server | Đạt theo kiểm thử thủ công | Ứng dụng trao đổi được qua TLS. `openssl verify -CAfile certs/dev-ca.pem certs/server-cert.pem` trả `certs/server-cert.pem: OK`. |
| Xác minh hostname trong chứng chỉ | Đạt theo kiểm tra chứng chỉ | Chứng chỉ được xác minh với tên `collab-server.local`. |
| Truyền file lớn & tương tác GUI ↔ CLI | Đạt | Truyền file **24 MB** giữa GUI ↔ GUI song song với chat; truyền file 2 chiều GUI ↔ CLI khớp `SHA-256`; kiểm tra file PDF `3.1 MB` và `163.0 MB` qua CLI/LAN. |
| Ngắt kết nối giữa lúc truyền file | Đạt | Ngắt socket khi đang truyền file 12 MB: GUI tự quay về màn hình Login, không báo hoàn tất giả và dọn sạch file `.part` trong `downloads/`. |
| Gửi file tới mọi người (`#all` / `/sendfile all`) | Đạt | Đã kiểm thử unit test (3 tests) và E2E fan-out qua server TLS tới tất cả peer đang online. |
| 20 client đồng thời gửi/nhận | Đạt | Đã xác minh cả bằng `python -m scripts.simulate_clients --host 127.0.0.1 --clients 20` và kịch bản E2E 20 client (GUI + CLI + session) phát/nhận broadcast thật. |
| Từ chối chứng chỉ không tin cậy | Đạt | Kết nối TLS không dùng CA tin cậy (`certs/dev-ca.pem`) bị từ chối với `TLSV1_ALERT_UNKNOWN_CA`. |

## Lệnh mô phỏng nhiều client

```powershell
python -m scripts.simulate_clients --host 127.0.0.1 --clients 20
```

Script tạo 20 client TLS đồng thời. Barrier giữ các session mở cho đến khi tất cả client đã đăng nhập và nhận phản hồi echo; sau đó mỗi client gửi QUIT và chờ BYE.

## Giới hạn & Hạng mục tự kiểm tra trên máy

- TLS mã hóa toàn bộ luồng TCP (đăng nhập, chat và truyền file); heartbeat token vẫn nằm trong UDP plaintext.
- Gõ tiếng Việt bằng bộ gõ IME (UniKey Telex/VNI) và hiển thị trên màn hình Windows DPI scaling `125%–200%` được kiểm tra trực tiếp trên máy người dùng.

# Kết quả kiểm thử hạ tầng

> Bảng hạ tầng bên dưới là các lần chạy được ghi nhận trước đó. Smoke test hiện tại của nhánh `feature/chat-ui` được ghi riêng ở phần sau.

## Smoke test trên nhánh `feature/chat-ui`

- Môi trường: Windows, Python 3.13.2, TLS với bộ chứng chỉ/khóa phát triển cục bộ.
- Server và client chạy cục bộ; discovery UDP mặc định, không thay đổi `config.json`.
- Hai client đăng nhập qua TLS, gửi/nhận chat riêng và chat chung, nhận `RECIPIENT_OFFLINE`, lấy danh sách online và đăng xuất qua `BYE`: đạt.
- CLI đăng nhập, chạy `/online`, nhận danh sách, chạy `/quit` và đóng session: đạt.
- Khởi động server/CLI với console UTF-8 mặc định: đạt.
- Smoke test dùng harness tạm thời; regression cases được bổ sung trong `tests/chat/test_chat_regressions.py`.
- Regression tests (Python `unittest`): server/CLI từ chối nhắn riêng cho chính mình; callback chỉ phát nội dung để `PromptSession`/`patch_stdout` quản lý prompt; Unicode lỗi bị từ chối; callback báo lỗi bị lỗi không làm thoát luồng mạng: 5/5 đạt.
- Lệnh chạy regression suite: `py -3.13 -m unittest discover -s tests -t . -v`.

## Môi trường

- Windows, chạy lệnh bằng PowerShell.
- Python 3.13.7.
- Server/client dùng raw sockets, TLS qua Python `ssl` và JSON framing dùng chung.

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
| 20 client đồng thời | Đạt | `python -m scripts.simulate_clients --host 127.0.0.1 --clients 20`; cả 20 client đăng nhập, nhận echo và thoát thành công. |
| Ngắt tiến trình client đột ngột | Đạt theo kiểm thử thủ công | Server phát hiện TCP đóng, dọn username; đăng nhập lại bằng username sau khi dọn được xác nhận. |
| Dispatcher ECHO/LIST_ONLINE | Đạt ở smoke check | Hai handler trả frame đúng; message không đăng ký trả về trạng thái chưa xử lý. Bài chạy 20 client cũng kiểm tra ECHO qua dispatcher. |

## Lệnh mô phỏng nhiều client

```powershell
python -m scripts.simulate_clients --host 127.0.0.1 --clients 20
```

Script tạo 20 client TLS đồng thời. Barrier giữ các session mở cho đến khi tất cả client đã đăng nhập và nhận phản hồi echo; sau đó mỗi client gửi QUIT và chờ BYE.

## Kiểm thử Toàn hệ thống (Infrastructure, Chat & File Transfer)

- Bộ kiểm thử tự động (`python -m unittest discover -s tests -t . -v`): **79/79 bài test ĐẠT (OK)** gồm:
  - `tests/infrastructure/test_server_infrastructure.py` (19 tests): Đăng ký user, danh sách online, từ chối login trùng (`USERNAME_TAKEN`), xác thực heartbeat token/IP, timeout dọn dẹp stale sessions; chuẩn hóa và kiểm tra username; đóng frame TCP 4-byte header, ghép mảnh TCP bị phân mẩu (`3-byte chunks`), bắt lỗi EOF/malformed JSON; định tuyến dispatcher.
  - `tests/chat/test_chat_regressions.py` (5 tests): Chặn gửi tin nhắn cho chính mình, xử lý callback an toàn, từ chối ký tự Unicode lỗi, tích hợp CLI.
  - `tests/transfer/test_integrity.py` (9 tests): Tính SHA-256 theo chunk, trích xuất metadata file thường/file rỗng, phát hiện lệch kích thước và lệch mã băm SHA-256.
  - `tests/transfer/test_sender.py` (15 tests): Khởi tạo `FILE_INIT`, truyền chunk ở luồng nền sau `FILE_ACCEPT`, xử lý đường dẫn có dấu ngoặc kép/khoảng trắng, hủy truyền, hiển thị thanh tiến trình thời gian thực trên CLI và chốt 1 dòng kết quả `[FILE DONE]`.
  - `tests/transfer/test_receiver.py` (10 tests): Tạo file `.part`, chống Path Traversal (`../../`), kiểm tra thứ tự chunk, tự động đánh số `file (1).ext` khi trùng tên, xóa `.part` khi sai hash hoặc ngắt kết nối, khóa file và quét dọn `.part` rác khi khởi động.
  - `tests/transfer/test_server_handler.py` (15 tests): Định tuyến `FILE_INIT`/`FILE_CHUNK`/`FILE_END`/`FILE_RESULT`, kiểm tra metadata `FILE_INIT`, từ chối trùng `session_id`, bắt buộc vai trò người gửi và State Machine nghiêm ngặt, tự động dọn dẹp các phiên file bị treo (`prune_stale_sessions`), và tự động báo `ERR_PEER_DISCONNECTED` khi một phía rớt mạng đột ngột.
  - `tests/transfer/test_transfer.py` (6 tests): Kiểm thử End-to-End đủ 5 kịch bản bắt buộc (file rỗng 0B, file nhỏ 1 chunk, file lớn đa chunk, file nhị phân raw bytes `0..255`, lỗi sửa đổi dữ liệu trên đường truyền và ngắt kết nối đột ngột).
- Kiểm thử thực tế trên CLI và giữa 2 máy qua mạng LAN/4G: Truyền thành công file PDF nhỏ (`3.1 MB`) và file PDF lớn (`163.0 MB`), thanh tiến trình `[=====>---------] %` chạy mượt theo thời gian thực ngay trên dòng `Bạn> `.

## Còn cần kiểm thử trước khi bàn giao cuối

- Thêm kiểm thử tự động trong `tests/infrastructure/` nếu cần mở rộng regression suite cho hạ tầng.
- Ghi thêm log/kết quả cụ thể vào đây nếu nhóm chạy lại trên môi trường khác.

## Giới hạn

TLS mã hóa toàn bộ luồng TCP (đăng nhập, chat và truyền file); heartbeat token vẫn nằm trong UDP plaintext.

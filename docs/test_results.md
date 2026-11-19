# Tổng hợp Kết quả Kiểm thử

Hệ thống kiểm thử tự động sử dụng thư viện chuẩn `unittest` của Python, kết hợp kiểm thử đơn vị cho từng thành phần, kiểm thử giao diện GUI ngoài màn hình (`offscreen`) và kiểm thử tích hợp loopback thực tế qua TLS + UDP.

## Môi trường & Lệnh thực thi

- Chạy toàn bộ bộ kiểm thử tự động:
  ```bash
  python -m unittest discover -s tests -t . -v
  ```
- Chạy kịch bản kiểm thử nhanh phân hệ truyền file cục bộ:
  ```bash
  python -m tests.transfer.quick_local_test
  ```
- Tổng số ca kiểm thử tự động: **148 tests** — kết quả **148/148 đạt (`OK`)** (không lỗi, không cảnh báo rò rỉ file handle).

## Phạm vi kiểm thử theo phân hệ

### 1. Hạ tầng mạng & Tích hợp End-to-End (`tests/infrastructure/test_server_infrastructure.py`) — 29 tests
- Kiểm tra chuẩn hóa username (`3..20` ký tự ASCII hợp lệ), tách frame TCP bị phân mảnh nhiều gói, từ chối frame vượt quá `1 MB` và JSON sai định dạng.
- Kiểm tra định tuyến trong `MessageDispatcher`, quản lý phiên `UserRegistry` (trùng tên, dọn phiên hết hạn, khóa đối chiếu định danh socket `remove_if_same`).
- Kiểm tra xác thực `UDP_HEARTBEAT` bằng HMAC-SHA256 (lệch đồng hồ, sai IP, replay timestamp cũ, sai kiểu dữ liệu/bool, từ chối chữ ký non-ASCII Unicode và bảo đảm luồng `udp_service` tiếp tục sống sau gói tin hỏng), lọc `protocol_version` dạng `bool` hoặc số thực `1.0` ở cả server và client discovery, cùng kiểm tra đọc `config.json` đầy đủ (fallback `config.example.json` khi thiếu tệp, báo `ValueError` khi thiếu khóa hoặc sai miền giá trị).
- Kiểm tra tính toàn vẹn của phiên vừa đăng nhập lại khi luồng kết nối cũ thoát muộn; kiểm tra đầu vào `ClientSession.connect` và đóng an toàn trong `logout`.
- **Kiểm thử Live TLS + UDP Loopback E2E:** Khởi chạy server thật với chứng chỉ TLS (`certs/`) và UDP service thật trên cổng ephemeral; chạy trọn quy trình `discover_server()`, đăng nhập 2 client `alice` và `bob`, giữ heartbeat HMAC, trao đổi tin nhắn broadcast/direct và truyền file nhị phân đa chunk (~78 KB) kèm kiểm chứng SHA-256 tệp đích.

### 2. Nghiệp vụ Chat, Phòng chat & Giao diện dòng lệnh (`tests/chat/`) — 15 tests
- Chặn gửi tin nhắn riêng cho chính bản thân ở cả server handler và CLI.
- Từ chối chuỗi Unicode surrogate không hợp lệ ở cả `ClientChat` và server mà không làm văng exception kết nối.
- Đảm bảo callback báo lỗi của UI không làm sập luồng đọc mạng nền; kiểm tra luồng in bất đồng bộ tương thích `prompt_toolkit`.
- Kiểm tra hướng dẫn cú pháp cho lệnh thiếu đối số (`/msg`, `/all`, `/sendfile`) và hoàn tác hàng đợi `/sendfile all` khi gửi yêu cầu `LIST_ONLINE` thất bại.
- `test_rooms.py`: Kiểm tra chuẩn hóa tên phòng, tạo/tham gia/rời/liệt kê phòng, chặn gửi tin khi chưa vào phòng (`NOT_ROOM_MEMBER`), lưu thành viên và lịch sử phòng vào SQLite, đẩy ngay `HISTORY_SYNC` khi vừa tham gia phòng, và đồng bộ `ROOM_DELIVERY` trên `ClientChat` và `ChatSessionStore`.
- `test_p2p_chat.py`: Kiểm tra chế độ mặc định là `Client–Server` (`enable_p2p=False`), truyền tin nhắn DM trực tiếp qua TCP mã hóa **AES-256-GCM** (`P2P_CHAT_OFFER` / `P2P_CHAT_ACCEPT`), từ chối khi đối phương tắt P2P để fallback về Server Relay, và đóng socket sạch khi tắt P2P.

### 3. Truyền tập tin & Toàn vẹn dữ liệu (`tests/transfer/`) — 79 tests
- `test_integrity.py`: Kiểm tra `SHA-256`, metadata tệp 0 byte / nhiều chunk, kiểm chứng size/hash và các hàm làm sạch/ràng buộc (`is_valid_transfer_id`, `is_safe_filename`, `sanitize_filename`, `parse_non_negative_int` chặn chặt `bool`/`float`/chuỗi/số âm).
- `test_receiver.py`: Kiểm tra tệp 0 byte, ghi nhiều chunk và kiểm tra SHA-256, chống trùng tên tệp đích, chặn path traversal POSIX/Windows/tên rỗng, chống ép kiểu `bool` ở `filesize`/`total_chunks`/`chunk_index`, xóa tệp `.part` mồ côi lúc khởi động nhưng giữ nguyên các file hoàn chỉnh có chứa `.part` như `archive.part1.rar`.
- `test_sender.py`: Kiểm tra phát `FILE_INIT`, stream nền theo `FILE_ACCEPT`, chống khởi tạo trùng luồng worker khi nhận nhiều `FILE_ACCEPT`, hủy phiên đang truyền, xóa sạch session trên `cleanup()`, kiểm tra đường dẫn rỗng ở `send_file()`, cô lập lỗi giải mã Base64 theo từng phiên để không ảnh hưởng lượt tải song song khác, giữ nguyên `direction`/`peer` trong sự kiện `FILE_ERROR`, và lan truyền cờ `is_broadcast` hai chiều khi gửi file qua `#all`.
- `test_server_handler.py`: Kiểm tra đăng ký toàn bộ `FILE_*` handlers, kiểm tra metadata (`filename`, `session_id`, `filesize`, `bool`, `is_broadcast` phải là `bool`, `receiver` rỗng, tự gửi cho bản thân), xác thực máy trạng thái & phân quyền vai trò (kể cả chặn `FILE_RESULT` sớm trước `FILE_END` đối với tệp $> 0$ byte trong khi vẫn giữ luồng tệp 0 byte), thu dọn `timeout` và ngắt kết nối (kể cả không gửi trùng lỗi sau khi phiên đã kết thúc bằng `FILE_RESULT`/`FILE_ERROR`), cô lập lỗi chuyển tiếp một phiên khi người nhận vẫn đang online để không làm gián đoạn các phiên truyền song song khác, cùng cô lập `ValueError` vs `OSError` trong bridge gửi dữ liệu.
- `test_transfer.py` & `quick_local_test.py`: Xác nhận truyền tải end-to-end (tệp rỗng 0 byte, tệp nhỏ, tệp nhiều chunk, dữ liệu nhị phân thô `0..255`, ngắt kết nối đột ngột dọn `.part`, sai lệch hash).
- `test_p2p_transfer.py`: Xác minh luồng truyền file P2P trực tiếp bỏ qua Server cho `FILE_CHUNK`/`FILE_END` với toàn vẹn SHA-256, tự động fallback về Server Relay khi cổng P2P bị chặn, từ chối token P2P sai và mã hóa/giải mã khung nhị phân bằng **AES-256-GCM**.

### 4. Giao diện Desktop PySide6 GUI (`tests/ui/test_gui_session_and_models.py`) — 21 tests
- Kiểm tra định tuyến và bộ đếm chưa đọc trong `ChatSessionStore`, giới hạn 1000 bản ghi trong `MessageListModel`, hoàn tất transfer chỉ xảy ra sau khi bên nhận xác nhận toàn vẹn (`FILE_RESULT`), và bọc từ cho chuỗi dài không khoảng trắng trong `MessageBubbleDelegate.sizeHint`.
- Kiểm tra `SessionController`: chặn đăng nhập trùng khi đang kết nối, giữ lại mốc `100%` và Completion bất chấp throttling ~15 Hz, ép kiểu an toàn payload tiến độ, dọn dẹp 1 lần khi mất kết nối bất ngờ hoặc nhận `BYE`, và từ chối an toàn các lệnh gọi sau `shutdown_sync()`.
- Kiểm tra `MainWindow` ở chế độ `QT_QPA_PLATFORM=offscreen` và TLS E2E: chuyển đổi dark/light theme, lọc tìm kiếm peer trên sidebar, hiển thị inline file bubble và bật/tắt panel inspector, mở thư mục `downloads/` thống nhất qua `DEFAULT_DOWNLOADS_DIR` (không lỗi `KeyError`), bỏ qua sự kiện thuộc generation kết nối cũ, gộp tiến độ gửi file broadcast vào một thẻ trong `#all` và định tuyến tệp nhận `is_broadcast=True` vào `#all` (kể cả kiểm thử 3 client live TLS E2E), chặn gửi tin nhắn khi mất kết nối hoặc vượt `65536` byte UTF-8 mà không làm mất nội dung đang soạn thảo, và làm sạch composer/error banner khi chuyển phiên kết nối.

### 5. Lưu trữ SQLite & Đồng bộ lịch sử (`tests/storage/test_history_store.py`) — 4 tests
- Kiểm tra lưu và lọc lịch sử tin nhắn `#all`/DM (cô lập DM giữa các cặp người dùng), giới hạn số lượng truy vấn, lưu nhật ký truyền tập tin `transfer_logs`, xử lý `HISTORY_REQUEST` / `HISTORY_SYNC` phía server và nạp lịch sử vào `ChatSessionStore` không làm tăng bộ đếm chưa đọc.

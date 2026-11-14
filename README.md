# Distributed Client Server Collaboration Application

Ứng dụng Python cho phép các client trong cùng mạng đăng nhập qua server, xem người dùng online, gửi tin riêng, chat chung và truyền file tin cậy. Server định tuyến tin nhắn và luồng chunk file qua TCP/TLS; UDP được dùng để tìm server và duy trì heartbeat.

## Trạng thái chức năng

- Đã hoàn thiện và tích hợp đầy đủ: UDP discovery/heartbeat, TCP framing bằng JSON, TLS, đăng nhập, danh sách online, chat riêng, chat chung, truyền file trọn luồng (`transfer/`) với kiểm tra toàn vẹn SHA-256 và giao diện CLI hiển thị thanh tiến trình thời gian thực.

## Yêu cầu

- Python 3.12 trở lên.
- Kết nối mạng cho TCP port `5000` và UDP port `5001`.
- Mạng dùng thư viện chuẩn Python (`socket`, `ssl`, `threading`, `json`); CLI dùng `prompt_toolkit` để giữ prompt và dòng đang nhập ổn định khi có tin đến.
- Server cần khóa riêng TLS khớp với `certs/server-cert.pem`. Mặc định cấu hình tìm khóa tại `certs/private/server-key.pem`. Khóa riêng không được đưa vào Git và không có trong checkout sạch. Nhận khóa phát triển từ người quản lý chứng chỉ của nhóm hoặc cấu hình một bộ CA/chứng chỉ/khóa phát triển mới đồng bộ cho server và client trước khi chạy.

## Cài đặt

Clone repository và mở terminal tại thư mục gốc dự án. Có thể tạo môi trường ảo riêng:

```powershell
# Thay 3.13 bằng phiên bản Python >= 3.12 có trên máy nếu cần.
py -3.13 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Trên macOS/Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Sau khi kích hoạt môi trường ảo, cài dependency của CLI:

```powershell
python -m pip install -r requirements.txt
```

Trên macOS/Linux dùng cùng lệnh `python -m pip install -r requirements.txt`.

## Cấu hình và TLS

Ứng dụng dùng `config.example.json` nếu chưa có `config.json`. Tạo bản cấu hình cục bộ khi cần đổi IP/port hoặc đường dẫn chứng chỉ:

```powershell
Copy-Item config.example.json config.json
```

macOS/Linux:

```bash
cp config.example.json config.json
```

`config.json` được `.gitignore` bỏ qua. Mặc định server bind vào `0.0.0.0:5000`; UDP discovery/heartbeat dùng port `5001`. Client tin CA tại `certs/dev-ca.pem` và xác minh tên `collab-server.local`.

Trước khi chạy server, kiểm tra rằng đường dẫn `tls.server_private_key` trong cấu hình tồn tại và khóa đó khớp với `tls.server_certificate`. Không commit khóa riêng TLS. Nếu nhóm tạo bộ chứng chỉ phát triển mới, cần cấu hình cùng CA tin cậy cho client và tên máy chủ có trong SAN của chứng chỉ.

## Chạy ứng dụng

Mở một terminal ở thư mục gốc và chạy server:

```powershell
python -m infrastructure.server
```

Trên macOS/Linux dùng `python3 -m infrastructure.server`.

Mở một terminal khác cho mỗi client:

```powershell
python -m ui.cli
```

Trên macOS/Linux dùng `python3 -m ui.cli`. Mỗi client cần một username duy nhất, dài 3–20 ký tự ASCII, chỉ gồm chữ, số và `_`.

Để thử trên hai máy trong cùng LAN hoặc 4G hotspot, chạy server trên một máy, cho phép TCP `5000` và UDP `5001` qua firewall (hoặc chuyển mạng sang Private), rồi chạy client trên các máy cùng mạng. Nếu môi trường mạng chặn UDP broadcast (như Wi-Fi trường học), CLI tự động hỗ trợ nhập IPv4 của server để kết nối trực tiếp.

## Lệnh CLI

| Lệnh | Tác dụng |
|---|---|
| `/online` | Xem username đang online. |
| `/msg <username> <nội dung>` | Gửi tin riêng. |
| `/all <nội dung>` | Gửi chat chung. |
| `/sendfile <username> <đường_dẫn_file>` | Gửi file tới người dùng khác. |
| `/help` | Hiện hướng dẫn. |
| `/quit` | Đăng xuất và đóng kết nối. |

Có thể nhập văn bản thường để gửi chat chung. Tin đến được nhận ở luồng nền khi CLI đang chờ nhập.
Server và CLI tự cấu hình console UTF-8 để hiển thị tiếng Việt trên Windows.

### Hướng dẫn truyền file (`/sendfile`)

- Cú pháp: `/sendfile <username> <đường_dẫn_file>` (hỗ trợ cả đường dẫn có khoảng trắng hoặc bọc trong dấu ngoặc kép `"..."`).
- Trong lúc truyền, thanh tiến trình `[=====>---------] %` hiển thị trực tiếp theo thời gian thực ngay trên dòng nhập lệnh `Bạn> ` ở cả máy gửi và máy nhận mà không làm trôi lịch sử chat.
- File nhận được ghi tạm vào `downloads/.<session_id>_<filename>.part`, kiểm tra kích thước và mã băm **SHA-256** trước khi đổi tên thành file hoàn chỉnh trong thư mục `downloads/` (tự động đánh số `file (1).ext` nếu trùng tên). Nếu mất kết nối hoặc sai mã băm, file `.part` sẽ tự động được dọn sạch.

## Kiểm thử tự động (Unit Tests)

Chạy toàn bộ bộ test suite (79 tests) bao phủ hạ tầng, chat và truyền file:

```powershell
python -m unittest discover -s tests -t . -v
```

## Đóng gói nộp bài (Packaging)

Tạo file ZIP nộp bài chuẩn quy cách (tự động loại trừ khóa riêng CA `dev-ca-key.pem`, `.venv`, cache và file tạm):

```powershell
python scripts/package_submission.py
```

File ZIP thành phẩm sẽ được lưu tại `dist/Distributed-Client-Server-Collaboration-Application.zip`.

## Cấu trúc liên quan

- `common/`: frame TCP, tên các loại bản tin và cấu hình console.
- `infrastructure/`: server, kết nối client, TLS, UDP và session.
- `chat/`: handler chat phía server và logic chat phía client.
- `ui/cli.py`: lệnh, hiển thị CLI và thanh tiến trình truyền file thời gian thực.
- `transfer/`: module truyền file trọn luồng (`sender.py`, `receiver.py`, `server_handler.py`, `integrity.py`).
- `docs/protocol.md`: đặc tả bản tin và framing.
- `docs/architecture.md`: thành phần, sequence diagram chat/truyền file và luồng xử lý.
- `docs/diagrams/`: ảnh biểu đồ sequence diagram (`chat_sequence.png`, `file_transfer_sequence.png`).
- `docs/chat_integration_contract.md`: ranh giới tích hợp TV1/TV2/TV3.
- `docs/test_results.md`: kết quả kiểm thử hạ tầng, chat và truyền file.

## Tài liệu báo cáo

Báo cáo PDF cuối tổng hợp kiến trúc, đặc tả protocol, sequence diagram chat/truyền file và các vấn đề kỹ thuật đã giải quyết. Các biểu đồ tuần tự đã được xuất ảnh sẵn trong `docs/diagrams/` và nhúng trực tiếp vào `docs/architecture.md`.

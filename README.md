# Distributed Client Server Collaboration Application

Ứng dụng Python cho phép các client trong cùng mạng đăng nhập qua server, xem người dùng online, gửi tin riêng và chat chung. Server định tuyến tin nhắn qua TCP/TLS; UDP được dùng để tìm server và duy trì heartbeat.

## Trạng thái chức năng

- Đã có: UDP discovery/heartbeat, TCP framing bằng JSON, TLS, đăng nhập, danh sách online, chat riêng, chat chung và CLI.
- Chưa tích hợp: truyền file. Các file trong `transfer/` hiện là placeholder; chờ TV3 bàn giao API trước khi hướng dẫn lệnh truyền file.

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
py -3.13 -m infrastructure.server
```

Trên macOS/Linux dùng `python3 -m infrastructure.server`.

Mở một terminal khác cho mỗi client:

```powershell
py -3.13 -m ui.cli
```

Trên macOS/Linux dùng `python3 -m ui.cli`. Mỗi client cần một username duy nhất, dài 3–20 ký tự ASCII, chỉ gồm chữ, số và `_`.

Để thử trên hai máy trong cùng LAN, chạy server trên một máy, cho phép TCP `5000` và UDP `5001` qua firewall, rồi chạy client trên các máy cùng mạng. Nếu client không tìm được server, kiểm tra UDP broadcast và firewall.

## Lệnh CLI

| Lệnh | Tác dụng |
|---|---|
| `/online` | Xem username đang online. |
| `/msg <username> <nội dung>` | Gửi tin riêng. |
| `/all <nội dung>` | Gửi chat chung. |
| `/help` | Hiện hướng dẫn. |
| `/quit` | Đăng xuất và đóng kết nối. |

Có thể nhập văn bản thường để gửi chat chung. Tin đến được nhận ở luồng nền khi CLI đang chờ nhập.
Server và CLI tự cấu hình console UTF-8 để hiển thị tiếng Việt trên Windows.

## Cấu trúc liên quan

- `common/`: frame TCP, tên các loại bản tin và cấu hình console.
- `infrastructure/`: server, kết nối client, TLS, UDP và session.
- `chat/`: handler chat phía server và logic chat phía client.
- `ui/cli.py`: lệnh và hiển thị CLI.
- `transfer/`: module truyền file, chưa tích hợp.
- `docs/protocol.md`: đặc tả bản tin và framing.
- `docs/architecture.md`: thành phần và luồng xử lý.
- `docs/chat_integration_contract.md`: ranh giới tích hợp TV1/TV2/TV3.
- `docs/test_results.md`: kết quả được ghi nhận và giới hạn kiểm thử.

## Tài liệu báo cáo

Báo cáo PDF cuối cần tổng hợp kiến trúc, đặc tả protocol, sequence diagram chat/truyền file và các vấn đề gặp phải. Phần truyền file cần cập nhật sau khi TV3 hoàn tất module và nhóm kiểm thử tích hợp.

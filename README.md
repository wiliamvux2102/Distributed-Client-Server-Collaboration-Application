# Ứng dụng Cộng tác Client–Server Phân tán

Hệ thống chat và truyền tập tin theo mô hình Client–Server hỗ trợ phát hiện máy chủ tự động qua UDP broadcast, bảo mật kênh điều khiển bằng TLS và kiểm tra toàn vẹn dữ liệu qua SHA-256.

## Tính năng chính

- **Khám phá máy chủ tự động (LAN Discovery):** Client tự động dò tìm máy chủ trong mạng LAN qua `UDP_DISCOVER` hoặc cho phép chỉ định trực tiếp `<host>:<port>`.
- **Bảo mật kênh truyền & Phiên hoạt động:** Kết nối TCP được bọc TLS (`>= TLSv1.2`, xác thực CA riêng và SNI `collab-server.local`); cơ chế `UDP_HEARTBEAT` sử dụng chữ ký HMAC-SHA256 kèm mốc thời gian Unix mili-giây tăng nghiêm ngặt để phát hiện mất kết nối và chống giả mạo/replay.
- **Nhắn tin thời gian thực:** Hỗ trợ cả chat nhóm công khai (`BROADCAST_MESSAGE`) và nhắn tin riêng (`DIRECT_MESSAGE`) kèm phản hồi trạng thái giao nhận (`DELIVERY_STATUS`).
- **Truyền tập tin an toàn:** Truyền đa khối (streaming theo chunk 64 KB), băm toàn vẹn SHA-256, ghi tệp tạm `.part` cô lập và tự động thu dọn khi hủy hoặc ngắt kết nối; hỗ trợ truyền 1–1 lẫn phát tới mọi người dùng đang online (`all` / `#all`).
- **Hai chế độ giao diện Client:**
  - **Desktop GUI (`PySide6`):** Quản lý danh sách hội thoại (`#all` và từng người dùng), đếm tin chưa đọc, khung soạn thảo đa dòng, bọc từ cho chuỗi dài và khung theo dõi tiến độ truyền file theo thời gian thực.
  - **CLI (`prompt_toolkit`):** Hỗ trợ đầy đủ lệnh tương tác với thanh trạng thái tiến độ một dòng ở đáy màn hình, không làm gián đoạn luồng đọc tin nhắn.

## Yêu cầu môi trường & Cài đặt

- **Python:** 3.12 trở lên (khuyến nghị chạy trong virtual environment `.venv`).
- **Thư viện phụ thuộc:** Khai báo trong [requirements.txt](requirements.txt) gồm `prompt_toolkit>=3.0,<4` và `PySide6>=6.7,<7`.

```bash
python -m venv .venv
# Windows (PowerShell):
.\.venv\Scripts\Activate.ps1
# Linux / macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

Nếu cần tùy chỉnh cổng hoặc đường dẫn chứng chỉ cục bộ, sao chép [config.example.json](config.example.json) thành `config.json` trước khi chạy (chi tiết xem [docs/configuration.md](docs/configuration.md)).

> **Chứng chỉ TLS phát triển:** Gói nộp ZIP tạo từ `scripts/package_submission.py` giữ sẵn `certs/dev-ca.pem`, `certs/server-cert.pem` và `certs/private/server-key.pem` (chỉ loại `certs/private/dev-ca-key.pem`) để chạy thử nghiệm cục bộ. Nếu clone từ Git (thư mục `certs/private/` bị bỏ qua theo `.gitignore`), có thể tạo lại cặp CA và chứng chỉ server phát triển bằng OpenSSL:
>
> ```bash
> mkdir -p certs/private
> openssl genrsa -out certs/private/dev-ca-key.pem 2048
> openssl req -x509 -new -nodes -key certs/private/dev-ca-key.pem -sha256 \
>   -days 365 -subj "/CN=Collab-Dev-CA" -out certs/dev-ca.pem
> openssl genrsa -out certs/private/server-key.pem 2048
> openssl req -new -key certs/private/server-key.pem \
>   -subj "/CN=collab-server.local" -out certs/private/server.csr
> openssl x509 -req -in certs/private/server.csr -CA certs/dev-ca.pem \
>   -CAkey certs/private/dev-ca-key.pem -CAcreateserial \
>   -out certs/server-cert.pem -days 365 -sha256 -extfile certs/server.ext
> ```

## Hướng dẫn sử dụng

### 1. Khởi chạy Server

```bash
python -m infrastructure.server
```

Server mở đồng thời cổng lắng nghe TCP bọc TLS (mặc định `5000`) và cổng UDP Discovery/Heartbeat (mặc định `5001`).

### 2. Khởi chạy Client

- **Giao diện Desktop (PySide6):**
  ```bash
  python -m ui.gui
  ```
- **Giao diện dòng lệnh (CLI):**
  ```bash
  python -m ui.cli
  ```

Các lệnh trong CLI (hoặc nhập văn bản trực tiếp để gửi chat chung):

| Lệnh | Chức năng |
| :--- | :--- |
| `/online` | Xem danh sách người dùng đang trực tuyến |
| `/msg <user> <nội dung>` | Gửi tin nhắn riêng cho `<user>` |
| `/all <nội dung>` | Gửi tin nhắn chung tới mọi người |
| `/sendfile <user> <path>` | Gửi tập tin cho một người dùng cụ thể |
| `/sendfile all <path>` | Gửi tập tin tới toàn bộ người dùng khác đang trực tuyến |
| `/help` | Hiển thị hướng dẫn lệnh |
| `/quit` | Đăng xuất an toàn và thoát chương trình |

### 3. Công cụ phụ trợ

- **Mô phỏng nhiều client cùng lúc:**
  ```bash
  python -m scripts.simulate_clients
  ```
- **Đóng gói mã nguồn sạch (loại bỏ tệp rác, khóa riêng CA, bản build tạm):**
  ```bash
  python scripts/package_submission.py
  ```

## Kiểm thử tự động

Chạy toàn bộ **129** unit test và integration test (bao gồm kiểm thử loopback trực tiếp qua UDP + TLS):

```bash
python -m unittest discover -s tests -t . -v
```

Chạy riêng kịch bản kiểm thử nhanh luồng truyền file cục bộ:

```bash
python -m tests.transfer.quick_local_test
```

## Cấu trúc dự án

```text
.
├── certs/            # Chứng chỉ CA phát triển và cặp khóa/chứng chỉ TLS của Server
├── chat/             # Định tuyến, kiểm tra hợp lệ và xử lý tin nhắn chat (Client & Server)
├── common/           # Định nghĩa thông điệp, cấu hình tập trung, TCP framing và console UTF-8
├── docs/             # Tài liệu kiến trúc, giao thức, cấu hình, kết quả kiểm thử và các sơ đồ
├── downloads/        # Thư mục mặc định lưu tệp nhận về phía Client
├── infrastructure/   # Lõi mạng: Server TCP/TLS, UDP Discovery/Heartbeat, UserRegistry, Auth, Dispatcher
├── scripts/          # Script đóng gói nộp bài và mô phỏng tải nhiều client
├── tests/            # Bộ kiểm thử tự động theo từng phân hệ (chat, infrastructure, transfer, ui)
├── transfer/         # Phân hệ truyền tập tin: toàn vẹn SHA-256, Sender, Receiver, Server relay
└── ui/               # Giao diện người dùng (CLI và Desktop PySide6 GUI)
```

## Tài liệu liên quan

- Kiến trúc hệ thống: [docs/architecture.md](docs/architecture.md)
- Đặc tả giao thức & thông điệp: [docs/protocol.md](docs/protocol.md)
- Tham số cấu hình hệ thống: [docs/configuration.md](docs/configuration.md)
- Tổng hợp kết quả kiểm thử: [docs/test_results.md](docs/test_results.md)

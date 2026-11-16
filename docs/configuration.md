# Tài liệu Cấu hình Hệ thống

Hệ thống nạp cấu hình tập trung tại [common/app_config.py](../common/app_config.py). Khi khởi động, chương trình đọc tệp `config.json` ở thư mục gốc dự án (chỉ dùng [config.example.json](../config.example.json) làm cấu hình mặc định khi chưa có `config.json`). Khi sử dụng `config.json`, tệp phải khai báo đầy đủ các nhóm và tham số hợp lệ; nếu thiếu khóa hoặc sai kiểu/miền giá trị (cổng, timeout, kích thước bản tin), hệ thống sẽ báo lỗi `ValueError` ngay khi khởi động.

## Các nhóm tham số cấu hình

### 1. `server` — Máy chủ TCP/TLS

| Khóa | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `tcp_bind_host` | `"0.0.0.0"` | Địa chỉ bind của socket TCP Server (`0.0.0.0` lắng nghe mọi card mạng) |
| `tcp_port` | `5000` | Cổng TCP phục vụ kết nối client bọc TLS |
| `tcp_connect_timeout_seconds` | `5` | Timeout phía client khi thiết lập kết nối TCP/TLS và đăng nhập |
| `accept_timeout_seconds` | `0.5` | Chu kỳ chờ vòng lặp `accept()` của server để kiểm tra tín hiệu dừng |
| `pre_login_timeout_seconds` | `60` | Thời gian tối đa chờ client hoàn tất TLS handshake và bản tin `LOGIN` |

### 2. `udp` — Khám phá mạng LAN & Heartbeat

| Khóa | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `bind_host` | `"0.0.0.0"` | Địa chỉ bind của UDP Discovery/Heartbeat Service |
| `port` | `5001` | Cổng UDP lắng nghe `UDP_DISCOVER` và `UDP_HEARTBEAT` |
| `discovery_target` | `"255.255.255.255"` | Địa chỉ broadcast mặc định khi client dò tìm server |
| `discovery_attempts` | `3` | Số lần client phát lại gói tin `UDP_DISCOVER` khi chưa nhận phản hồi |
| `discovery_timeout_seconds` | `1` | Thời gian chờ mỗi lần thử `UDP_DISCOVER` |
| `wait_interval_seconds` | `0.5` | Chu kỳ thức dậy trong vòng lặp UDP service để kiểm tra dừng/hết hạn phiên |
| `protocol_version` | `1` | Phiên bản giao thức UDP hợp lệ mà server và client đối chiếu |

### 3. `heartbeat` — Giám sát kết nối sống

| Khóa | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `interval_seconds` | `10` | Chu kỳ client gửi gói `UDP_HEARTBEAT` tới server |
| `timeout_seconds` | `35` | Ngưỡng không nhận được heartbeat hợp lệ trước khi server thu hồi phiên |

### 4. `tls` — Bảo mật kênh truyền

| Khóa | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `minimum_version` | `"TLSv1.2"` | Phiên bản TLS tối thiểu cho phép (`"TLSv1.2"` hoặc `"TLSv1.3"`) |
| `server_name` | `"collab-server.local"` | Tên máy chủ (SNI/SAN) dùng để kiểm chứng chứng chỉ server |
| `ca_certificate` | `"certs/dev-ca.pem"` | Đường dẫn chứng chỉ CA tin cậy phía client |
| `server_certificate` | `"certs/server-cert.pem"` | Đường dẫn chứng chỉ công khai của server |
| `server_private_key` | `"certs/private/server-key.pem"` | Đường dẫn khóa bí mật của server |

### 5. `protocol` — Giới hạn bản tin

| Khóa | Mặc định | Ý nghĩa |
| :--- | :--- | :--- |
| `max_payload_bytes` | `1048576` | Kích thước tối đa một khung TCP JSON (1 MB) |
| `max_direct_message_bytes` | `65536` | Giới hạn byte UTF-8 tối đa của một tin nhắn chat (64 KB) |

> [!NOTE]
> Khóa riêng phát triển của CA (`certs/private/dev-ca-key.pem`) không thuộc tập tin phân phối hay cấu hình chạy runtime; cần giữ bí mật khóa này khi phát hành chứng chỉ mới cho server.

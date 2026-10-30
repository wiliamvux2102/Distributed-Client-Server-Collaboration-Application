# Cấu hình ứng dụng

Ứng dụng đọc cấu hình tại `config.json` ở thư mục gốc dự án. Nếu file này
chưa tồn tại, ứng dụng dùng `config.example.json`. File `config.json` đã được
thêm vào `.gitignore`, nên mỗi thành viên có thể điều chỉnh môi trường riêng.

Để tạo cấu hình cục bộ, sao chép `config.example.json` thành `config.json`,
sau đó chỉnh các giá trị phù hợp. Đây là JSON thuần, không hỗ trợ chú thích
`//` hoặc dấu phẩy thừa ở cuối object.

Các nhóm thiết lập:

- `server`: địa chỉ bind TCP, port, timeout chờ kết nối và thời gian chờ đăng nhập.
- `udp`: địa chỉ bind và đích discovery, port, số lần thử, timeout, chu kỳ kiểm
  tra và phiên bản giao thức discovery.
- `heartbeat`: chu kỳ gửi heartbeat phía client và thời gian server chờ heartbeat.
- `tls`: phiên bản TLS tối thiểu, tên máy chủ dùng xác minh chứng chỉ và các
  đường dẫn chứng chỉ. Đường dẫn tương đối được tính từ thư mục gốc dự án.
- `protocol`: kích thước frame TCP tối đa và kích thước tin nhắn riêng tối đa.

Server bind vào `0.0.0.0` để nhận kết nối trên các giao diện mạng. Client tìm
server bằng UDP discovery; địa chỉ `127.0.0.1` không được cấu hình làm địa chỉ
server cố định ở client.

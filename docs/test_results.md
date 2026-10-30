# Kết quả kiểm thử hạ tầng

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

## Còn cần kiểm thử trước khi bàn giao cuối

- Kiểm thử từ hai máy vật lý trên cùng LAN; `192.168.56.1` có thể là địa chỉ mạng ảo và không chứng minh LAN vật lý.
- Kiểm thử mất mạng kiểu không nhận được TCP FIN/RST để xác nhận riêng thời gian stale heartbeat.
- Tích hợp và kiểm thử handler chat/file sau khi TV2/TV3 bàn giao implementation.
- Thêm kiểm thử tự động trong `tests/infrastructure/`; hiện thư mục này chưa có test case.
- Ghi thêm log/kết quả cụ thể vào đây nếu nhóm chạy lại trên môi trường hoặc phiên bản code khác.

## Giới hạn

Discovery, heartbeat và kiểm thử trên đây không chứng minh UDP được bảo vệ. TLS hiện chỉ mã hóa TCP; heartbeat token vẫn nằm trong UDP plaintext. Handler truyền file chưa được tích hợp trong phiên bản được kiểm tra.

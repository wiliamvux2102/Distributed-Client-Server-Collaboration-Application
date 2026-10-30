# Các loại bản tin truyền file (Message Types)
FILE_INIT = "FILE_INIT"         # Khởi tạo phiên, gửi kèm metadata + SHA-256
FILE_ACCEPT = "FILE_ACCEPT"     # Người nhận sẵn sàng nhận chunk
FILE_CHUNK = "FILE_CHUNK"       # Chứa mảnh dữ liệu nhị phân của file
FILE_END = "FILE_END"           # Báo hiệu đã gửi hết toàn bộ chunk
FILE_RESULT = "FILE_RESULT"     # Kết quả kiểm tra SHA-256 ở đầu nhận (OK/FAILED)
FILE_ERROR = "FILE_ERROR"       # Báo lỗi hoặc hủy phiên truyền giữa chừng

# Mã lỗi truyền file (Error Codes)
ERR_USER_OFFLINE = "ERR_USER_OFFLINE"
ERR_SESSION_NOT_FOUND = "ERR_SESSION_NOT_FOUND"
ERR_HASH_MISMATCH = "ERR_HASH_MISMATCH"
ERR_SIZE_MISMATCH = "ERR_SIZE_MISMATCH"
ERR_DISK_IO = "ERR_DISK_IO"
ERR_PEER_DISCONNECTED = "ERR_PEER_DISCONNECTED"
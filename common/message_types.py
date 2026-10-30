# TV1: Định nghĩa các loại bản tin và mã lỗi theo giao thức chung.

# Server xác nhận đã tiếp nhận kết nối.
SERVER_READY = "SERVER_READY"

# Client yêu cầu server phản hồi lại nội dung.
ECHO = "ECHO"

# Server trả kết quả echo.
ECHO_RESULT = "ECHO_RESULT"

# Client yêu cầu kết thúc phiên.
QUIT = "QUIT"

# Server xác nhận kết thúc phiên.
BYE = "BYE"

# Server thông báo yêu cầu không hợp lệ.
ERROR = "ERROR"

# Authentication
LOGIN = "LOGIN"

LOGIN_OK = "LOGIN_OK"

LOGIN_ERROR = "LOGIN_ERROR"

# ONLINE
LIST_ONLINE = "LIST_ONLINE"

ONLINE_LIST = "ONLINE_LIST"

# Unicast
DIRECT_MESSAGE = "DIRECT_MESSAGE"

DIRECT_DELIVERY = "DIRECT_DELIVERY"

DELIVERY_STATUS = "DELIVERY_STATUS"

# UDP
UDP_DISCOVER = "UDP_DISCOVER"

UDP_DISCOVERY_RESPONSE = "UDP_DISCOVERY_RESPONSE"

UDP_HEARTBEAT = "UDP_HEARTBEAT"
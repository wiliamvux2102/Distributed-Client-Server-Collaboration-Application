# TV1: Xử lý đăng nhập và kiểm tra username của client.

def normalize_username(raw_username: object) -> str:
    """Chuẩn hóa khoảng trắng và kiểm tra username."""

    if not isinstance(raw_username, str):
        raise ValueError("Username phải là chuỗi.")

    username = raw_username.strip()

    if not 3 <= len(username) <= 20:
        raise ValueError("Username phải dài từ 3 đến 20 ký tự.")

    if not username.isascii():
        raise ValueError("Username chỉ được dùng ký tự ASCII.")

    if not username.replace("_", "").isalnum():
        raise ValueError(
            "Username chỉ được gồm chữ cái, chữ số và dấu gạch dưới."
        )

    return username
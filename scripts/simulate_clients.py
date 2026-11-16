import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import secrets
import socket
import threading

from common import message_types as mt
from common.protocol import recv_message, send_message
from infrastructure.security import (
    TLS_SERVER_NAME,
    create_client_context,
)


def simulate_client(
    index: int,
    run_id: str,
    host: str,
    port: int,
    tls_context,
    login_barrier: threading.Barrier,
) -> str:
    username = f"sim_{run_id}_{index:02d}"

    try:
        with socket.create_connection(
            (host, port),
            timeout=15.0,
        ) as raw_connection:
            with tls_context.wrap_socket(
                raw_connection,
                server_hostname=TLS_SERVER_NAME,
            ) as connection:
                connection.settimeout(15.0)

                greeting = recv_message(connection)
                if greeting.get("type") != mt.SERVER_READY:
                    raise RuntimeError(
                        f"{username}: không nhận được SERVER_READY."
                    )

                send_message(
                    connection,
                    {
                        "type": mt.LOGIN,
                        "username": username,
                    },
                )

                login_response = recv_message(connection)
                if login_response.get("type") != mt.LOGIN_OK:
                    raise RuntimeError(
                        f"{username}: đăng nhập lỗi "
                        f"{login_response.get('code')}."
                    )

                send_message(
                    connection,
                    {
                        "type": mt.ECHO,
                        "text": f"ping-{username}",
                    },
                )

                echo_response = recv_message(connection)
                if (
                    echo_response.get("type") != mt.ECHO_RESULT
                    or echo_response.get("text") != f"ping-{username}"
                ):
                    raise RuntimeError(
                        f"{username}: echo không khớp."
                    )

                # Giữ các client đăng nhập đồng thời cho đến khi tất cả
                # đã kết nối, đăng nhập và nhận được echo.
                login_barrier.wait(timeout=15.0)

                send_message(
                    connection,
                    {
                        "type": mt.QUIT,
                    },
                )

                bye = recv_message(connection)
                if bye.get("type") != mt.BYE:
                    raise RuntimeError(
                        f"{username}: không nhận được BYE."
                    )

    except Exception:
        # Nếu một client lỗi trước barrier, giải phóng các client
        # khác đang đợi barrier.
        login_barrier.abort()
        raise

    return username


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Mô phỏng nhiều client TLS kết nối tới server."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--clients", type=int, default=20)
    args = parser.parse_args()

    if not 1 <= args.clients <= 20:
        parser.error("--clients phải nằm trong khoảng 1 đến 20.")

    tls_context = create_client_context()
    login_barrier = threading.Barrier(args.clients)
    run_id = secrets.token_hex(2)

    failures = 0

    with ThreadPoolExecutor(max_workers=args.clients) as executor:
        futures = [
            executor.submit(
                simulate_client,
                index,
                run_id,
                args.host,
                args.port,
                tls_context,
                login_barrier,
            )
            for index in range(args.clients)
        ]

        for future in as_completed(futures):
            try:
                username = future.result()
                print(f"[OK] {username}")
            except Exception as error:
                failures += 1
                print(f"[FAIL] {error}")

    if failures:
        print(f"[RESULT] {failures}/{args.clients} client thất bại.")
        return 1

    print(f"[RESULT] Cả {args.clients} client đều thành công.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
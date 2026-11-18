"""Command-line interface for the collaboration client."""

from pathlib import Path
import socket
import ssl
import threading

from prompt_toolkit import PromptSession
from prompt_toolkit.patch_stdout import patch_stdout

from common import message_types as mt
from common.console import configure_console_utf8
from chat.client_chat import ClientChat
from infrastructure.client_connection import ClientSession
from transfer.sender import ClientFileTransferManager


_OUTPUT_LOCK = threading.Lock()


def _print_async(text: str) -> None:
    """Print a background event; PromptSession redraws the active input."""
    with _OUTPUT_LOCK:
        print(text, flush=True)


def _make_chat_callbacks():
    """Create UI callbacks consumed by ClientChat."""

    def on_message(sender: str, text: str, is_broadcast: bool) -> None:
        label = "CHUNG" if is_broadcast else "RIÊNG"
        _print_async(f"[{label}] {sender}: {text}")

    def on_status(
        ok: bool,
        message: str,
        code: str | None,
        delivered_count: int | None,
    ) -> None:
        if ok:
            status_text = message or (
                f"Đã phát tin tới {delivered_count} người."
                if delivered_count is not None
                else "Server đã xử lý yêu cầu gửi."
            )
            _print_async(f"[ĐÃ GỬI] {status_text}")
        else:
            _print_async(f"[LỖI {code or 'DELIVERY_FAILED'}] {message}")

    return on_message, on_status


def _handle_online_list(message: dict) -> None:
    users = message.get("users")
    if not isinstance(users, list) or any(
        not isinstance(username, str) for username in users
    ):
        _print_async("[LỖI] ONLINE_LIST có cấu trúc không hợp lệ.")
        return

    _print_async(f"[ONLINE] {', '.join(users) if users else '(không có ai)'}")


def _handle_server_error(message: dict) -> None:
    _print_async(
        f"[LỖI SERVER {message.get('code', 'UNKNOWN')}] "
        f"{message.get('message', 'Server trả lỗi không có mô tả.')}"
    )


def _handle_bye(message: dict) -> None:
    _print_async(message.get("message", "Server đã kết thúc phiên."))


def _handle_unhandled(message: dict) -> None:
    _print_async(
        f"[BẢN TIN CHƯA XỬ LÝ {message.get('type', 'UNKNOWN')}]"
    )


def _print_help() -> None:
    print(
        "Lệnh sử dụng:\n"
        "  /online                 Xem danh sách người đang online\n"
        "  /history                Xem lại lịch sử chat gần đây\n"
        "  /msg <user> <nội dung>  Gửi tin riêng\n"
        "  /all <nội dung>         Gửi chat chung\n"
        "  /sendfile <user> <path> Gửi file tới người dùng khác\n"
        "  /sendfile all <path>    Gửi file tới tất cả người đang online\n"
        "  /help                   Xem hướng dẫn\n"
        "  /quit                   Thoát\n"
        "Có thể nhập văn bản thường để gửi chat chung."
    )


def _process_input(
    line: str,
    session: ClientSession,
    chat: ClientChat,
    ft_manager: ClientFileTransferManager | None = None,
    pending_broadcast_files: list[str] | None = None,
    pending_lock: threading.Lock | None = None,
) -> bool:
    """Process one user input line; return False when the CLI should exit."""

    if not line.strip():
        return True

    if line == "/quit":
        session.logout()
        return False

    if line == "/help":
        _print_help()
        return True

    if line == "/online":
        session.send({"type": mt.LIST_ONLINE})
        return True

    if line == "/history":
        chat.request_history(limit=20)
        return True

    if line == "/msg" or line.startswith("/msg "):
        parts = line.split(maxsplit=2)
        if len(parts) != 3 or not parts[1].strip():
            print("Cách dùng: /msg <username> <nội dung>")
            return True

        recipient = parts[1].strip()
        if recipient == session.username:
            print("[LỖI] Không thể gửi tin riêng cho chính mình.")
            return True

        with _OUTPUT_LOCK:
            chat.send_direct(recipient, parts[2])
            print(f"[RIÊNG tới {recipient}] {parts[2]}")
        return True

    if line == "/all" or line.startswith("/all "):
        text = line[len("/all "):] if line.startswith("/all ") else ""
        if not text.strip():
            print("Cách dùng: /all <nội dung>")
            return True
        with _OUTPUT_LOCK:
            chat.send_broadcast(text)
            print(f"[CHUNG bạn] {text}")
        return True

    if line == "/sendfile" or line.startswith("/sendfile "):
        parts = line.split(maxsplit=2)
        if len(parts) != 3:
            print("Cách dùng: /sendfile <username> <đường_dẫn_file>")
            return True

        recipient = parts[1].strip()
        filepath = parts[2].strip("\"' ")
        if not recipient or not filepath:
            print("Cách dùng: /sendfile <username> <đường_dẫn_file>")
            return True

        if recipient in ("all", "#all"):
            if ft_manager is None:
                print("[LỖI] Trình quản lý truyền file chưa được khởi tạo.")
                return True
            if not Path(filepath).is_file():
                print(f"[ERROR] File not found: {filepath}")
                return True
            if pending_broadcast_files is not None:
                def _with_pending_lock(action) -> None:
                    if pending_lock is not None:
                        with pending_lock:
                            action()
                    else:
                        action()

                def _rollback_pending() -> None:
                    for idx in range(len(pending_broadcast_files) - 1, -1, -1):
                        if pending_broadcast_files[idx] == filepath:
                            pending_broadcast_files.pop(idx)
                            break

                _with_pending_lock(lambda: pending_broadcast_files.append(filepath))
                try:
                    session.send({"type": mt.LIST_ONLINE})
                except Exception as exc:
                    _with_pending_lock(_rollback_pending)
                    print(f"[LỖI] Không thể lấy danh sách người online: {exc}")
                    return True
                print("[FILE] Đang lấy danh sách người online để gửi file...")
            else:
                print("[LỖI] Không thể gửi file broadcast trong ngữ cảnh này.")
            return True

        if recipient == session.username:
            print("[LỖI] Không thể gửi file cho chính mình.")
            return True

        if ft_manager is not None:
            ft_manager.send_file(recipient, filepath)
        else:
            print("[LỖI] Trình quản lý truyền file chưa được khởi tạo.")
        return True

    if line.startswith("/"):
        print("Lệnh không hợp lệ. Gõ /help để xem các lệnh.")
        return True

    with _OUTPUT_LOCK:
        chat.send_broadcast(line)
        print(f"[CHUNG bạn] {line}")
    return True


def main() -> None:
    """Connect, log in, start callbacks, and run the CLI input loop."""

    configure_console_utf8()
    prompt_session = PromptSession(erase_when_done=True)
    session: ClientSession | None = None
    ft_manager: ClientFileTransferManager | None = None

    try:
        username = input("Username: ").strip()
        try:
            session = ClientSession.connect(
                username,
                on_error=_print_async,
                on_unhandled=_handle_unhandled,
            )
        except TimeoutError:
            print("[CẢNH BÁO] Không tìm thấy server tự động qua UDP broadcast (có thể do Wi-Fi/4G chặn broadcast).")
            manual_host = input("Nhập IPv4 của máy chủ server (hoặc Enter để hủy): ").strip()
            if not manual_host:
                raise
            session = ClientSession.connect(
                username,
                on_error=_print_async,
                on_unhandled=_handle_unhandled,
                server_host=manual_host,
            )

        on_message, on_status = _make_chat_callbacks()
        def on_history(messages: list[dict], _transfers: list[dict]) -> None:
            if not messages:
                return
            lines = ["--- Lịch sử gần đây ---"]
            for item in messages:
                sender = item.get("sender", "?")
                recipient = item.get("recipient", "")
                text = item.get("text", "")
                if item.get("is_broadcast"):
                    lines.append(f"  [CHUNG {sender}] {text}")
                elif sender == session.username:
                    lines.append(f"  [RIÊNG tới {recipient}] {text}")
                else:
                    lines.append(f"  [RIÊNG từ {sender}] {text}")
            lines.append("-----------------------")
            _print_async("\n".join(lines))

        chat = ClientChat(
            send_message=session.send,
            on_message=on_message,
            on_status=on_status,
            on_history=on_history,
        )
        chat.register_handlers(session.register_handler)

        ft_manager = ClientFileTransferManager(
            username=session.username,
            send_fn=session.send,
            print_fn=_print_async,
            status_update_fn=prompt_session.app.invalidate,
        )
        ft_manager.register_handlers(session.register_handler)

        pending_broadcast_files: list[str] = []
        pending_lock = threading.Lock()

        def _on_online_list_for_cli(message: dict) -> None:
            users = message.get("users")
            if not isinstance(users, list) or any(
                not isinstance(u, str) for u in users
            ):
                _print_async("[LỖI] ONLINE_LIST có cấu trúc không hợp lệ.")
                return

            queued_path: str | None = None
            with pending_lock:
                if pending_broadcast_files:
                    queued_path = pending_broadcast_files.pop(0)

            if queued_path is not None:
                peers = [u for u in users if u != session.username]
                if not peers:
                    _print_async("[FILE] Không có người dùng nào khác đang online để gửi.")
                    return
                _print_async(
                    f"[FILE] Bắt đầu gửi '{Path(queued_path).name}' tới {len(peers)} người: {', '.join(peers)}"
                )
                for peer in peers:
                    ft_manager.send_file(peer, queued_path, is_broadcast=True)
                return

            _handle_online_list(message)

        session.register_handler(mt.ONLINE_LIST, _on_online_list_for_cli)
        session.register_handler(mt.ERROR, _handle_server_error)
        session.register_handler(mt.BYE, _handle_bye)

        def _build_prompt() -> str:
            status = ft_manager.get_active_status()
            if status:
                return f"{status}\nBạn> "
            return "Bạn> "

        # patch_stdout lets receiver-thread callbacks print above the active
        # prompt and lets PromptSession redraw the partially typed line.
        with patch_stdout():
            session.start()
            print(f"Đăng nhập thành công với username {session.username}.")
            _print_help()
            chat.request_history(limit=20)

            while not session.is_closed:
                try:
                    line = prompt_session.prompt(
                        _build_prompt,
                        refresh_interval=0.05,
                    )
                except EOFError:
                    session.logout()
                    break

                if session.is_closed:
                    break

                try:
                    if not _process_input(
                        line,
                        session,
                        chat,
                        ft_manager,
                        pending_broadcast_files=pending_broadcast_files,
                        pending_lock=pending_lock,
                    ):
                        break
                except (OSError, RuntimeError, ValueError) as error:
                    print(f"[LỖI] {error}")

    except socket.timeout:
        print("[LỖI] Quá thời gian chờ server.")
    except ssl.SSLError as error:
        print(f"[LỖI TLS] Không thiết lập hoặc xác minh được TLS: {error}")
    except (OSError, TimeoutError, ValueError) as error:
        print(f"[LỖI KẾT NỐI/ĐĂNG NHẬP] {error}")
    except KeyboardInterrupt:
        print("\nĐã nhận Ctrl+C, đang đóng phiên.")
    finally:
        if ft_manager is not None:
            ft_manager.cleanup("Client disconnected")
        if session is not None:
            session.close()
        print("Đã đóng kết nối.")


if __name__ == "__main__":
    main()

"""Console stream setup shared by the server and command-line client."""

import os
import sys


def configure_console_utf8() -> None:
    """Use UTF-8 for interactive streams and enable VT100 / ANSI escape sequences on Windows."""

    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # STD_OUTPUT_HANDLE = -11, STD_ERROR_HANDLE = -12
            for std_id in (-11, -12):
                handle = kernel32.GetStdHandle(std_id)
                mode = ctypes.c_ulong()
                if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                    # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
        except Exception:
            try:
                os.system("")
            except Exception:
                pass

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue

        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Captured or already-closed streams may not be reconfigurable.
            continue

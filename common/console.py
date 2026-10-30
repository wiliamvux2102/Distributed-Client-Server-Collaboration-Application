"""Console stream setup shared by the server and command-line client."""

import sys


def configure_console_utf8() -> None:
    """Use UTF-8 for interactive streams when the runtime supports it."""

    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue

        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Captured or already-closed streams may not be reconfigurable.
            continue

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REAL_CHAT = _ROOT / "chat"
if _REAL_CHAT.is_dir() and str(_REAL_CHAT) not in __path__:
    __path__.append(str(_REAL_CHAT))


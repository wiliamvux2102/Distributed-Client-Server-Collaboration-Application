# TV1: Kiểm thử kết nối, đăng nhập, trạng thái, ngắt đột ngột, UDP và TLS.
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REAL_INFRA = _ROOT / "infrastructure"
if _REAL_INFRA.is_dir() and str(_REAL_INFRA) not in __path__:
    __path__.append(str(_REAL_INFRA))


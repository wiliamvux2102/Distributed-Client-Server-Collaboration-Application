# TV3: Kiểm thử file rỗng, nhỏ, lớn, nhị phân, sai hash và ngắt giữa chừng.
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_REAL_TRANSFER = _ROOT / "transfer"
if _REAL_TRANSFER.is_dir() and str(_REAL_TRANSFER) not in __path__:
    __path__.append(str(_REAL_TRANSFER))


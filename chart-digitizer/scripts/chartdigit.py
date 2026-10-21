#!/usr/bin/env python3
"""chart-digitizer 可执行入口。

用法::

    python scripts/chartdigit.py doctor
    python scripts/chartdigit.py detect figure.png
    python scripts/chartdigit.py extract figure.png --xlim 0 10 --ylim -1 1 --format csv xlsx json

无需安装：脚本会把自身所在目录加入 ``sys.path``。
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from chartdigit.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())

"""验收套件的导入路径：让 `tests.fixtures` 在 pytest 的 prepend 模式下可见。

仓库的 `tests/` 不是常规包（无 `__init__.py`），而验收套件按包目录被收集；
这里显式把仓库根加入 sys.path，`tests.fixtures.v03_snow_inn` 以命名空间包方式导入。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

"""PyInstaller sidecar 入口（P9 T11，D9）：行为等同 ``python -m hastings.server``。"""

from hastings.server.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

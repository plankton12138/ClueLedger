"""对话测试公共固定项（Phase 19.3）。"""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

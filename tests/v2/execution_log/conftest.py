"""执行日志（V03-1.6）测试共享 fixture。"""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"

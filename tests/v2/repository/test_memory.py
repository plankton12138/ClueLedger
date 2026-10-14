"""内存后端契约测试入口（SQLite 侧见 test_sqlite.py）。"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from contract import ALL_CHECKS, ContractEnv


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda f: f.__name__)
def test_memory_contract(check: Callable[[ContractEnv], None],
                         make_env_memory: Callable[[], ContractEnv]) -> None:
    check(make_env_memory())

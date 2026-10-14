"""迁移器（Phase 12 T12.3）：空库创建、已知版本放行、未知较新版本拒绝打开。"""

from __future__ import annotations

import sqlite3

from hastings.application.errors import EnvironmentFailure
from hastings.infrastructure.repository.schema import SCHEMA_SQL, SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION_UNKNOWN", "migrate", "read_schema_version"]

# 有表但 user_version=0：版本标记丢失，版本不可判定
SCHEMA_VERSION_UNKNOWN = 0

_TOO_NEW_WHY = (
    f"数据库 schema 版本 {SCHEMA_VERSION} 比当前程序支持的更新，降级打开可能损坏数据，已拒绝"
)
_TOO_NEW_FIX = (
    f"用支持 schema 版本 {SCHEMA_VERSION} 的新版 hastings 打开，"
    "或从数据库备份恢复到受支持版本；不要手工改表结构"
)


def read_schema_version(conn: sqlite3.Connection) -> int:
    """读取 schema 版本标记（inspection 等只读工具复用，不触发迁移）。"""
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def migrate(conn: sqlite3.Connection) -> None:
    """把空库建到当前版本；版本不符时按三要素拒绝（计划 T12.3）。"""
    current = read_schema_version(conn)
    if current == SCHEMA_VERSION:
        return
    if current > SCHEMA_VERSION:
        raise EnvironmentFailure(
            code="REPO_SCHEMA_TOO_NEW",
            where="sqlite migrate",
            why=_TOO_NEW_WHY,
            fix=_TOO_NEW_FIX,
            details={"db_version": current, "supported": SCHEMA_VERSION},
        )
    if current == 0:
        # 空库（或无版本标记的全新文件）：一次性建到当前版本
        tables = conn.execute("SELECT count(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
        if tables:
            raise EnvironmentFailure(
                code="REPO_SCHEMA_UNKNOWN",
                where="sqlite migrate",
                why="数据库里有表但没有 schema 版本标记，无法判断如何升级",
                fix=_TOO_NEW_FIX,
                details={"supported": SCHEMA_VERSION},
            )
        conn.executescript(SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        return
    raise EnvironmentFailure(
        code="REPO_SCHEMA_UNKNOWN",
        where="sqlite migrate",
        why=f"数据库 schema 版本 {current} 已过时且无升级路径",
        fix=_TOO_NEW_FIX,
        details={"db_version": current, "supported": SCHEMA_VERSION},
    )

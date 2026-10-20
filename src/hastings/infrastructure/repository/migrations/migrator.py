"""迁移器（Phase 12 T12.3，收口修订）：空库建表、分步升级、过新版本拒绝打开。

升级路径是版本号存在的意义：schema 演化不再意味着「旧库作废重建」。
每步迁移声明为「从版本 N 升到 N+1 的 SQL 序列」，逐步提交并推进
user_version——中途崩溃时库停在某个一致版本，重开续跑即可。
"""

from __future__ import annotations

import sqlite3

from hastings.application.errors import EnvironmentFailure
from hastings.infrastructure.repository.schema import SCHEMA_SQL, SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION_UNKNOWN", "migrate", "read_schema_version"]

# 有表但 user_version=0：版本标记丢失，版本不可判定
SCHEMA_VERSION_UNKNOWN = 0

# 升级步：键 N 表示「把版本 N 的库升到 N+1」；新版本先在这里登记，再改 schema.py
_MIGRATION_STEPS: dict[int, tuple[str, ...]] = {
    1: (
        # v2：幂等记录带请求指纹——同键不同内容的重放显式冲突，
        # 不再静默返回旧结果。旧行指纹置空，重放时按冲突拒绝（无法验证内容）。
        "ALTER TABLE idempotency_records ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''",
    ),
    2: (
        # v3：关系行带请求内序号——SQL 不保证行返回顺序，同一 Commit 内
        # 多条关系的折叠顺序必须显式持久化。旧行序号 0（单行 Commit 无影响）。
        "ALTER TABLE relation_revisions ADD COLUMN position INTEGER NOT NULL DEFAULT 0",
    ),
    3: (
        # v4（V03-0.5）：仓库辅助表——副本/回执/操作记录与正文提交同事务。
        # 幂等：IF NOT EXISTS 保证重跑安全；已到 v4 的库不执行本步。
        """
        CREATE TABLE IF NOT EXISTS aux_records (
            project_id TEXT NOT NULL REFERENCES projects(id),
            bucket TEXT NOT NULL,
            key TEXT NOT NULL,
            payload TEXT NOT NULL,
            PRIMARY KEY (project_id, bucket, key)
        )
        """,
    ),
}

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
    """空库建到当前版本；旧库分步升级；版本不符时按三要素拒绝。"""
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
    # 0 < current < SCHEMA_VERSION：按注册的升级步逐版本推进，每步独立提交
    version = current
    while version < SCHEMA_VERSION:
        step = _MIGRATION_STEPS.get(version)
        if step is None:
            raise EnvironmentFailure(
                code="REPO_SCHEMA_UNKNOWN",
                where="sqlite migrate",
                why=f"数据库 schema 版本 {version} 没有已登记的升级路径",
                fix=_TOO_NEW_FIX,
                details={"db_version": version, "supported": SCHEMA_VERSION},
            )
        for statement in step:
            conn.execute(statement)
        version += 1
        conn.execute(f"PRAGMA user_version = {version}")
        conn.commit()

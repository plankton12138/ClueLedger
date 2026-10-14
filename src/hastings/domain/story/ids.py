"""Story Domain 的确定性 ID 生成（Phase 13 T13.2）。

迁移与导入必须可复现：同一 V1 源对象（同 kind + 同稳定键）在任意次导入中
生成同一个 ArtifactId，才能让 ImportReport 的映射表可核对、重跑幂等。
运行期新建对象由 IdGenerator 端口生成随机 ID；本模块只服务「从旧数据推导
稳定 ID」这一场景。
"""

from __future__ import annotations

import hashlib

__all__ = ["deterministic_story_id"]

_ID_PREFIX = "art"
_HEX_LEN = 24


def deterministic_story_id(kind: str, key: str) -> str:
    """由 (kind, key) 推导稳定 ArtifactId，形如 `art_<24位十六进制>`。

    key 是源对象在该 kind 内的稳定标识（V1 中的文件名、slug 或原始 id）。
    """
    digest = hashlib.sha256(f"{kind}\x00{key}".encode()).hexdigest()
    return f"{_ID_PREFIX}_{digest[:_HEX_LEN]}"

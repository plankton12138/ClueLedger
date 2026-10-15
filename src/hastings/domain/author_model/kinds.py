"""作者模型域的 Artifact kind 常量（Phase 14 T14.5）。

作者模型对象与正文/故事对象分域存放：`author_model.` 前缀标识本域持有，
Manuscript 用 `manuscript.`、Story 用 `story.`/`narrative.` 前缀。观察、偏好
与样例都是 Artifact，可独立版本化与撤销。
"""

from __future__ import annotations

AUTHOR_MODEL_PREFIX = "author_model."

KIND_OBSERVATION = "author_model.observation"
KIND_PREFERENCE = "author_model.preference"
KIND_EXAMPLE = "author_model.example"

KNOWN_AUTHOR_MODEL_KINDS = frozenset((KIND_OBSERVATION, KIND_PREFERENCE, KIND_EXAMPLE))


def is_author_model_kind(kind: str) -> bool:
    """判断某 Repository Artifact kind 是否由作者模型域持有。"""
    return kind.startswith(AUTHOR_MODEL_PREFIX)

"""Manuscript Domain 的 Artifact kind 常量（Phase 14 T14.1）。

正文与故事事实分开存放：`manuscript.` 前缀标识正文对象，Story Domain 用
`story.`/`narrative.` 前缀（见 domain/story/kinds.py）。章与段落都是 Artifact，
可独立版本化、独立谱系。
"""

from __future__ import annotations

MANUSCRIPT_PREFIX = "manuscript."

KIND_CHAPTER = "manuscript.chapter"
KIND_TEXT_UNIT = "manuscript.text_unit"
KIND_PATCH_PROPOSAL = "manuscript.patch_proposal"

KNOWN_MANUSCRIPT_KINDS = frozenset((KIND_CHAPTER, KIND_TEXT_UNIT, KIND_PATCH_PROPOSAL))


def is_manuscript_kind(kind: str) -> bool:
    """判断某 Repository Artifact kind 是否由 Manuscript Domain 持有。"""
    return kind.startswith(MANUSCRIPT_PREFIX)

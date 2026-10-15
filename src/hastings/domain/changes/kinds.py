"""Changes Domain 的 Artifact kind 常量。"""

from __future__ import annotations

CHANGES_PREFIX = "changes."

# ChangeSet：一次「我改了一个早期设定，后面怎么办」的工作流载体
KIND_CHANGE_SET = "changes.change_set"

CHANGES_KINDS = (KIND_CHANGE_SET,)


def is_changes_kind(kind: str) -> bool:
    """判断某 Repository Artifact kind 是否由 Changes Domain 持有。"""
    return kind.startswith(CHANGES_PREFIX)

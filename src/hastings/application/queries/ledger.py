"""账本只读查询（V03-3.3）：给试写与候选预览提供可引用的设定条目。

只读投影，不含任何写入端口：账本写入仍归领域命令与统一仓库事务
（V03-3.2 的直接保存与建议采用在后续任务接入）。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hastings.application.services.commit_service import CommitService
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.domain.story import kinds as story_kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry, display_name

__all__ = ["KIND_LABELS", "LedgerQueries", "entry_view"]

#: 可在试写里引用的账本种类与中文标签（节拍不在其中：V03-3.1 已退出活动路径）。
KIND_LABELS: dict[str, str] = {
    story_kinds.KIND_CHARACTER: "人物",
    story_kinds.KIND_PLACE: "地点",
    story_kinds.KIND_STORY_PREMISE: "故事前提",
    story_kinds.KIND_WORLD_RULE: "世界规则",
    story_kinds.KIND_CLUE: "线索",
    story_kinds.KIND_MYSTERY_TRUTH: "真相",
    story_kinds.KIND_RELATIONSHIP: "关系",
    story_kinds.KIND_TIMELINE_EVENT: "时间线事件",
    story_kinds.KIND_TRUTH_FACT: "事实",
    story_kinds.KIND_KNOWLEDGE: "认知 / 证词",
}

#: 摘要优先取的字段（按 kind）；都没有则取前三个非空字符串字段。
SUMMARY_FIELDS: dict[str, tuple[str, ...]] = {
    story_kinds.KIND_CHARACTER: ("identity", "goal", "need", "secret", "arc"),
    story_kinds.KIND_PLACE: ("description", "atmosphere"),
    story_kinds.KIND_STORY_PREMISE: ("premise", "genre", "tone"),
    story_kinds.KIND_WORLD_RULE: ("rule", "scope"),
    story_kinds.KIND_CLUE: ("statement", "source"),
    story_kinds.KIND_MYSTERY_TRUTH: ("truth", "solution"),
    story_kinds.KIND_RELATIONSHIP: ("public_state", "true_state", "evolution"),
    story_kinds.KIND_TIMELINE_EVENT: ("time", "description"),
    story_kinds.KIND_TRUTH_FACT: ("statement",),
    story_kinds.KIND_KNOWLEDGE: ("note",),
}

#: 认知模式的中文标签；证词与猜测保留原模式，不升级为客观事实。
KNOWLEDGE_LABELS: dict[str, str] = {
    "knows": "知道",
    "suspects": "怀疑",
    "misbelieves": "误信",
    "unaware": "不知道",
}

#: 前端候选列表里的稳定排序：先人物与前提，再线索与地点。
_ORDER = {
    story_kinds.KIND_STORY_PREMISE: 0,
    story_kinds.KIND_CHARACTER: 1,
    story_kinds.KIND_RELATIONSHIP: 2,
    story_kinds.KIND_CLUE: 3,
    story_kinds.KIND_MYSTERY_TRUTH: 4,
    story_kinds.KIND_PLACE: 5,
    story_kinds.KIND_WORLD_RULE: 6,
    story_kinds.KIND_TRUTH_FACT: 7,
    story_kinds.KIND_KNOWLEDGE: 8,
    story_kinds.KIND_TIMELINE_EVENT: 9,
}


def _display(entry: StoryEntry) -> str:
    name = display_name(entry.kind, entry.content)
    if name:
        return str(name)
    for value in entry.content.values():
        if isinstance(value, str) and value.strip():
            return value.strip()[:40]
    return entry.artifact_id


def _summarize(kind: str, content: Mapping[str, Any]) -> str:
    if kind == story_kinds.KIND_KNOWLEDGE:
        mode = str(content.get("mode", ""))
        label = KNOWLEDGE_LABELS.get(mode, mode or "认知")
        note = content.get("note")
        if isinstance(note, str) and note.strip():
            return f"{label}：{note.strip()}"
        return label
    fields = SUMMARY_FIELDS.get(kind)
    values: list[str] = []
    if fields:
        for field in fields:
            value = content.get(field)
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
    else:
        for key, value in content.items():
            if key in ("schema_version", "legacy_metadata"):
                continue
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
            if len(values) == 3:
                break
    return "；".join(values) if values else "（无描述）"


def entry_view(entry: StoryEntry) -> dict[str, str]:
    """一个账本条目的展示形状；试写提示词与前端引用列表共用同一口径。"""
    return {
        "id": entry.artifact_id,
        "kind": entry.kind,
        "kind_label": KIND_LABELS.get(entry.kind, entry.kind),
        "title": _display(entry),
        "summary": _summarize(entry.kind, entry.content),
    }


class LedgerQueries:
    def __init__(self, repo: CommitService) -> None:
        self._repo = repo

    def catalog(self, project_id: ProjectId) -> StoryCatalog:
        branch = self._repo.get_branch(project_id, "main")
        head = branch.head_commit_id if branch is not None else None
        if head is None:
            return StoryCatalog(commit_id=None, entries=())
        return StoryCatalog.from_snapshot(self._repo.get_snapshot(project_id, head))

    def entries(self, project_id: ProjectId) -> list[dict[str, str]]:
        """可引用条目：只列已知种类，按种类再按 ID 稳定排序。"""
        catalog = self.catalog(project_id)
        rows = [
            entry_view(entry)
            for entry in catalog.entries
            if entry.kind in KIND_LABELS
        ]
        rows.sort(key=lambda r: (_ORDER.get(r["kind"], 99), r["id"]))
        return rows

    def entries_at(self, project_id: ProjectId, commit_id: str) -> list[dict[str, str]]:
        """指定版本的可引用条目（试写按生成时版本取依据）。"""
        catalog = StoryCatalog.from_snapshot(
            self._repo.get_snapshot(project_id, CommitId(commit_id))
        )
        return [
            entry_view(entry) for entry in catalog.entries if entry.kind in KIND_LABELS
        ]

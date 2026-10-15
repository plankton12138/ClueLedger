"""CLI 的 V2 读路径（T17.5）：``/bible`` 在已迁移工作区走 application 查询。

职责边界：CLI 对 V2 项目只做**读**（StoryQueries）；写一律进 Web 端 V2 主流程
（ChangeCenter / AgentWorkbench）——CLI 的 V1 写工具在 V2 项目上已是拒绝桩
（T17.6），这里不提供第二套写入口，避免「CLI 绕过变更中心直写」的旁路。

项目解析：settings.yaml 的 ``v2_project_id``（迁移落盘时写入）是 slug → V2 项目
的唯一映射；``v2_db_path`` 指向服务启动时的 ``--v2-db``，CLI 据此开只读查询端。
两键缺失时给出三要素提示，不猜、不静默回落 V1 账本——已迁移工作区的 YAML 账本
不再是事实源，读它会把用户带回旧真相。
"""

from __future__ import annotations

from hastings.application.queries.story_queries import StoryQueries
from hastings.bible.store import StoryWorkspace
from hastings.domain.repository.ids import ProjectId
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, display_name
from hastings.infrastructure.repository.sqlite import build_sqlite_repository
from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator
from hastings.settings import v2_db_path, v2_project_id

__all__ = ["V2_LEDGER_KINDS", "v2_bible_output"]

# CLI /bible 的六账本类型 → V2 kind（与 V1 ENTITY_LABELS 的键对齐）
V2_LEDGER_KINDS: dict[str, str] = {
    "character": kinds.KIND_CHARACTER,
    "clue": kinds.KIND_CLUE,
    "foreshadow": kinds.KIND_FORESHADOW,
    "method": kinds.KIND_MYSTERY_TRUTH,
    "timeline": kinds.KIND_TIMELINE_EVENT,
    "outline": kinds.KIND_CHAPTER,
}

# 每账本一行的摘要字段（无则回落显示名）
_SUMMARY_FIELDS: dict[str, tuple[str, ...]] = {
    kinds.KIND_CHARACTER: ("identity", "goal"),
    kinds.KIND_CLUE: ("statement",),
    kinds.KIND_FORESHADOW: ("setup",),
    kinds.KIND_MYSTERY_TRUTH: ("method",),
    kinds.KIND_TIMELINE_EVENT: ("time",),
    kinds.KIND_CHAPTER: ("title",),
}

_LABELS: dict[str, str] = {
    "character": "角色",
    "clue": "线索",
    "foreshadow": "伏笔",
    "method": "手法",
    "timeline": "时间线",
    "outline": "章纲",
}

# sqlite 读取端按 db 路径缓存：REPL 内重复 /bible 不重开连接
_READERS: dict[str, StoryQueries] = {}


def _queries_for(workspace: StoryWorkspace) -> StoryQueries:
    db_path = v2_db_path(workspace)
    if not db_path:
        raise LookupError(
            "未记录 V2 数据库路径。为什么错：迁移时服务未以 --v2-db 启动，"
            "settings.yaml 没有 v2_db_path。"
            "怎样算对：用 --v2-db 启动服务并重新执行迁移，或在 CLI 所在机器"
            "确认该工作区 settings.yaml 的 v2_db_path 指向有效数据库。"
        )
    reader = _READERS.get(db_path)
    if reader is None:
        repo = build_sqlite_repository(db_path, SystemClock(), UuidIdGenerator())
        reader = StoryQueries(repo.service)
        _READERS[db_path] = reader
    return reader


def _summary(entity_type: str, content: dict[str, object]) -> str:
    fields = _SUMMARY_FIELDS[V2_LEDGER_KINDS[entity_type]]
    parts = [str(content.get(field, "")) for field in fields if content.get(field)]
    return "｜".join(parts)


def _ledger_lines(catalog: StoryCatalog, entity_type: str) -> tuple[str, list[str]]:
    kind = V2_LEDGER_KINDS[entity_type]
    entries = catalog.by_kind(kind)
    label = _LABELS[entity_type]
    if not entries:
        return f"{label}账本为空。", []
    lines = [f"{label}账本共 {len(entries)} 条："]
    for entry in entries:
        name = display_name(entry.kind, entry.content) or entry.artifact_id
        detail = _summary(entity_type, entry.content)
        suffix = f"｜{detail}" if detail else ""
        lines.append(f"- {name}{suffix}")
    return "\n".join(lines), [entry.artifact_id for entry in entries]


def v2_bible_output(
    workspace: StoryWorkspace,
    entity_type: str | None = None,
    *,
    queries: StoryQueries | None = None,
) -> str:
    """已迁移工作区的 ``/bible`` 输出：经 StoryQueries 读 V2 项目快照。

    ``queries`` 仅供测试注入；生产路径按 settings.yaml 的 v2_db_path 装配。
    """
    if entity_type is not None and entity_type not in V2_LEDGER_KINDS:
        known = "、".join(V2_LEDGER_KINDS)
        return f"未知账本类型：{entity_type}。可用值：{known}。"

    project_value = v2_project_id(workspace)
    if project_value is None:
        return (
            "该工作区尚未迁移到 V2。为什么错：settings.yaml 没有 v2_project_id。"
            "怎样算对：先在 Web 端执行迁移（POST /api/{slug}/v2-migration/apply），"
            "或继续用 V1 只读查看。"
        )
    try:
        reader = queries if queries is not None else _queries_for(workspace)
        project_id = ProjectId(project_value)
        head = reader.branch_head(project_id)
        if head is None:
            return (
                f"V2 项目 {project_value} 没有可读内容（main 分支为空）。"
                "为什么错：迁移未落成或项目被清空。"
                "怎样算对：检查 v2_project_id 是否指向迁移产出的项目。"
            )
        catalog = reader.catalog(project_id, head)
    except LookupError as exc:
        return str(exc)
    except OSError as exc:
        return (
            f"读取 V2 仓库失败：{exc}。为什么错：数据库文件缺失或被占用。"
            "怎样算对：确认服务未独占锁定该数据库后重试。"
        )

    types = [entity_type] if entity_type is not None else list(V2_LEDGER_KINDS)
    commit_label = (catalog.commit_id or "无提交")[:12]
    lines = [f"V2 项目 {project_value}（commit {commit_label}…）："]
    for ledger_type in types:
        section, _ids = _ledger_lines(catalog, ledger_type)
        lines.append(section)
    return "\n".join(lines)

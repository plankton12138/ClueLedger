"""Phase 17 T17.1–T17.3：V1 工作区迁移（扫描 → 幂等导入 → 校验）。

职责边界：本模块把「旧工作区目录」当作**只读源**，产出可审阅的报告、可提交的
提案、写入后的校验结论；它不写源文件，也不绕过 Commit Service 直连存储。

三段式（对应 plan §5 的 T17.1/T17.2/T17.3）：

1. **扫描（T17.1）**：`scan_v1_workspace` 覆盖工作区里的每一个文件，给每个源文件
   恰一个归类——``mapped / legacy / ambiguous / ignored / error``。旧账本交给 Phase 13
   的 ``v1_story_mapper``；正文与章节归档在此新增映射；会话/事件/生命周期文件归为
   ``ignored`` 并**必须写明原因**（无理由遗漏使报告失败——四透镜②：硬约束放层内，
   不指望调用方自觉兜底）。
2. **导入（T17.2）**：`V1WorkspaceImporter.apply` 以「源 manifest hash + schema
   version」为幂等键，把故事提案与正文章节**作为一次原子提交**交给 Commit Service。
   相同输入重试由 Commit Service 的幂等表返回原结果；源文件变化后本轮 dry-run 的
   ``run_id`` 不再匹配，显式要求重新 dry-run（不静默用旧报告覆盖新源）。
3. **校验（T17.3）**：`V1WorkspaceImporter.validate` 在新 Commit 的快照上重算数量、
   每章规范化正文 hash、悬空引用、旧 revision 保留策略与运行记录归档，产出逐项
   结论；任何一项不通过都如实报出，不用「测试全绿」替代业务核对。

设计取舍：章节正文**经领域命令** ``manuscript.commands.import_chapter`` 生成，但把
``new_id`` 换成确定性闭包（同源对象每次导入得到同一 ArtifactId）。迁移必须可复现、
可核对（plan T13.2 的确定性 ID 约定），而运行期命令默认生成随机 ID——这里用
「命令 + 确定性 ID 端口」同时拿到不变量检查与可复现性，不新开写后门。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.repository import CommitRepository, RepositoryReader
from hastings.bible.models import OutlineEntry
from hastings.bible.store import StoryWorkspace, load_ledger
from hastings.domain.manuscript import kinds as ms_kinds
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.commands import import_chapter
from hastings.domain.repository import error_codes as ec
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.repository.results import CommitResult
from hastings.domain.story import kinds as story_kinds
from hastings.domain.story.catalog import StoryCatalog
from hastings.domain.story.changes import DomainChange
from hastings.domain.story.ids import deterministic_story_id
from hastings.interfaces.importers.v1_story_mapper import ImportReport, map_v1_workspace
from hastings.manuscript.diff import split_paragraphs
from hastings.manuscript.store import revisions_dir

__all__ = [
    "IMPORT_SCHEMA_VERSION",
    "MIGRATION_OUTCOMES",
    "OUTCOME_AMBIGUOUS",
    "OUTCOME_ERROR",
    "OUTCOME_IGNORED",
    "OUTCOME_LEGACY",
    "OUTCOME_MAPPED",
    "MigrationPlan",
    "MigrationReport",
    "MigrationResult",
    "SourceCounts",
    "SourceItem",
    "V1WorkspaceImporter",
    "ValidationEntry",
    "ValidationReport",
    "import_key",
    "manifest_hash",
    "plan_migration",
    "scan_v1_workspace",
]

# 迁移报告与幂等键的 schema 版本：导入器语义一旦变化必须递增，让旧幂等记录失效
# （同 (manifest hash, schema version) 才视为同一逻辑导入）。
# v3：会话锁文件（.session.jsonl.lock / sessions/*.lock）与根级压缩归档会话
# （session-<stamp>.jsonl）由「未知文件报错」改为带原因的只读归档（真实工作区
# 核对发现，评审 P2 同类）；v2：根级 session.jsonl 改为只读归档。语义变化必须
# 递增版本号，使旧幂等记录失效。
IMPORT_SCHEMA_VERSION = 3

OUTCOME_MAPPED = "mapped"
OUTCOME_LEGACY = "legacy"
OUTCOME_AMBIGUOUS = "ambiguous"
OUTCOME_IGNORED = "ignored"
OUTCOME_ERROR = "error"
MIGRATION_OUTCOMES = frozenset(
    (OUTCOME_MAPPED, OUTCOME_LEGACY, OUTCOME_AMBIGUOUS, OUTCOME_IGNORED, OUTCOME_ERROR)
)

_EVENT_IMPORTED = "v1.workspace.imported"

# 运行记录（会话/事件/生命周期）不是故事事实，迁移只做只读归档；每条都必须带原因。
_SESSION_LOG_REASON = "会话日志属 Agent 运行记录，作为只读历史归档，不进入仓库事实"
_LEGACY_SESSION_REASON = (
    "旧单会话格式（多会话索引引入前，见 session_store.legacy_session_file）的会话日志"
    "属 Agent 运行记录，作为只读历史归档，不进入仓库事实"
)
_SESSION_LOCK_REASON = (
    "会话锁文件属运行期瞬态文件（进程互斥标记，非数据），作为只读残留归档；"
    "迁移前也可手工删除后重新 dry-run"
)
_SESSION_ARCHIVE_REASON = "压缩归档的旧会话日志属 Agent 运行记录，作为只读历史归档，不进入仓库事实"
_IGNORED_FILES: dict[str, str] = {
    "story.yaml": "工作区生命周期（stage/stage_history）不是故事事实，转为项目里程碑说明",
    "settings.yaml": "工作区设置属运行配置，不是故事事实，作为只读附件保留",
    "sessions.json": "会话索引属 Agent 运行记录，作为只读历史归档，不进入仓库事实",
    "session.jsonl": _LEGACY_SESSION_REASON,
    ".session.jsonl.lock": _SESSION_LOCK_REASON,
    "events.jsonl": "事件流属运行审计，作为只读历史归档，不重放为业务事件",
}

# 单文件账本文件名 → story mapper 的实体类型
_LEDGER_TYPES: dict[str, str] = {
    "clues.yaml": "clue",
    "foreshadows.yaml": "foreshadow",
    "method.yaml": "method",
    "timeline.yaml": "timeline",
    "outlines.yaml": "outline",
}


@dataclass(frozen=True, slots=True)
class SourceItem:
    """一个源文件在迁移中的归类结果（每个源文件恰一条）。

    ``ignored`` / ``error`` 必须带非空 detail（原因/三要素），否则报告失败。
    """

    path: str
    item_type: str
    outcome: str
    target: str = "-"
    detail: str = ""

    @property
    def is_unexplained(self) -> bool:
        return self.outcome in (OUTCOME_IGNORED, OUTCOME_ERROR) and not self.detail.strip()


@dataclass(frozen=True, slots=True)
class SourceCounts:
    """源侧汇总，作为导入后校验的对照基准。"""

    files: int
    story_entities: int
    chapters: int
    revisions: int
    chars: int


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """T17.1 dry-run 报告：逐文件归类 + 汇总 + 运行标识。"""

    workspace: str
    run_id: str
    schema_version: int
    items: tuple[SourceItem, ...]
    story: ImportReport
    counts: SourceCounts
    chapter_hashes: tuple[tuple[str, str], ...]

    @property
    def errors(self) -> tuple[SourceItem, ...]:
        return tuple(item for item in self.items if item.outcome == OUTCOME_ERROR)

    @property
    def ignored(self) -> tuple[SourceItem, ...]:
        return tuple(item for item in self.items if item.outcome == OUTCOME_IGNORED)

    @property
    def unexplained(self) -> tuple[SourceItem, ...]:
        return tuple(item for item in self.items if item.is_unexplained)

    @property
    def ok(self) -> bool:
        """报告通过的前提：无 error、无无理由遗漏、故事侧无阻断性映射错误。"""
        return not self.errors and not self.unexplained and not self.story.errors


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """dry-run 输出：报告 + 可提交提案（故事 DomainChange + 正文章节变化）。"""

    report: MigrationReport
    story_proposal: tuple[DomainChange, ...]
    manuscript_changes: tuple[ArtifactChange, ...]


@dataclass(frozen=True, slots=True)
class ValidationEntry:
    """单项校验结论：名字 + 是否通过 + 期望/实际（失败时用于定位）。"""

    check: str
    ok: bool
    expected: str
    actual: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """T17.3 校验报告；任何一项不通过都应阻断「标记 V2 为 active」。"""

    entries: tuple[ValidationEntry, ...]

    @property
    def ok(self) -> bool:
        return all(entry.ok for entry in self.entries)

    @property
    def failures(self) -> tuple[ValidationEntry, ...]:
        return tuple(entry for entry in self.entries if not entry.ok)


@dataclass(frozen=True, slots=True)
class MigrationResult:
    """一次导入的结论：报告 + Commit + 校验；``replayed`` 表示命中了幂等记录。"""

    report: MigrationReport
    validation: ValidationReport
    status: str
    commit_id: str | None
    new_head: str | None
    replayed: bool


# --------------------------------------------------------------------------
# 源侧读取（只读）
# --------------------------------------------------------------------------
def _iter_workspace_files(workspace: StoryWorkspace) -> list[tuple[str, Path]]:
    """story_dir 下除 manifest.json 外的全部文件，按相对路径排序（确定性）。"""
    root = workspace.story_dir
    pairs = [
        (path.relative_to(root).as_posix(), path)
        for path in root.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    ]
    return sorted(pairs, key=lambda pair: pair[0])


def manifest_hash(workspace: StoryWorkspace) -> str:
    """源 manifest hash：文件相对路径 + 内容的稳定哈希，作为幂等键的一半。

    只取「文件名 + 字节哈希」，不取 mtime——同一内容在不同机器/时刻必须得到同一
    run_id，否则迁移无法复现（四透镜④：幂等靠稳定契约而非环境细节）。
    """
    digest = hashlib.sha256()
    for rel, path in _iter_workspace_files(workspace):
        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        digest.update(f"{rel}\x00{file_hash}\x00".encode())
    return digest.hexdigest()


def import_key(run_id: str, schema_version: int = IMPORT_SCHEMA_VERSION) -> str:
    """幂等键 = schema version + 源 manifest hash（plan T17.2）。"""
    return f"v1-import:{schema_version}:{run_id}"


def _normalize_text(text: str) -> str:
    """正文规范化：按空行分段后以双换行重连，消除空行/行尾差异。"""
    return "\n\n".join(split_paragraphs(text))


def _text_hash(text: str) -> str:
    return hashlib.sha256(_normalize_text(text).encode("utf-8")).hexdigest()


def _non_space_chars(text: str) -> int:
    return sum(1 for ch in text if not ch.isspace())


def _chapter_number(stem: str) -> int | None:
    """从章文件名（chNN）取章号；取不到返回 None（不猜）。"""
    digits = "".join(ch for ch in stem if ch.isdigit())
    return int(digits) if digits else None


def _chapter_files(workspace: StoryWorkspace) -> list[Path]:
    """可迁移的章正文文件：**仅** ``chapters/`` 顶层的 ``*.md``。

    扫描计数、提案写入与校验三处都从这里取范围，保证三者一致；``chapters/`` 下
    的嵌套子目录（如 ``chapters/archive/``）不是受管布局，由分类器显式报错，
    不静默忽略也不假装导入（评审 P1：标为 mapped 却未导入是迁移底线违规）。
    """
    if not workspace.chapters_dir.exists():
        return []
    return sorted(path for path in workspace.chapters_dir.glob("*.md") if path.is_file())


def _is_top_level_chapter(rel: str) -> bool:
    """rel 是否为顶层章正文（``chapters/<name>.md``，无更深层级）。"""
    if not rel.startswith("chapters/") or not rel.endswith(".md"):
        return False
    return "/" not in rel[len("chapters/") :]


def _is_runtime_record(rel: str) -> bool:
    """rel 是否为「须只读归档的运行/生命周期记录」（会话索引、事件、会话日志、
    会话锁、压缩归档、生命周期与设置文件）。校验据此核对**源中实际存在**的记录，
    而非固定清单。"""
    if rel in _IGNORED_FILES:
        return True
    if rel.startswith("sessions/") and (rel.endswith(".jsonl") or rel.endswith(".lock")):
        return True
    # 根级压缩归档：session-<stamp>.jsonl（压缩归档命名见 session_store）
    return rel.startswith("session-") and rel.endswith(".jsonl")


def _outline_by_number(workspace: StoryWorkspace) -> dict[int, str]:
    """章号 → 章纲名，用于把正文章与叙事章对齐（不重放、只连引用）。"""
    return {o.chapter: o.name for o in load_ledger(workspace.outline_path, OutlineEntry)}


def _revision_records(workspace: StoryWorkspace, stem: str) -> list[dict[str, Any]]:
    """归档目录里的旧版本记录（NNN.md/.json）；缺目录即空。"""
    directory = revisions_dir(workspace, stem)
    records: list[dict[str, Any]] = []
    for md in sorted(directory.glob("*.md")):
        if not md.stem.isdigit():
            continue
        meta_path = directory / f"{md.stem}.json"
        meta: dict[str, Any] = (
            json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        )
        records.append(
            {
                "revision": int(md.stem),
                "sha256": hashlib.sha256(md.read_bytes()).hexdigest(),
                "ts": meta.get("ts"),
                "stats": meta.get("stats"),
            }
        )
    return records


def _deterministic_unit_ids(stem: str) -> Any:
    """构造确定性的 ``new_id`` 端口：同源章每次得到同一批段落 ID。"""
    counter = {"n": 0}

    def new_id(kind: str) -> str:
        del kind  # 段落 ID 前缀由 kind 常量决定，这里只保证稳定
        counter["n"] += 1
        return deterministic_story_id(
            ms_kinds.KIND_TEXT_UNIT, f"v1:chapter:{stem}:unit:{counter['n']:04d}"
        )

    return new_id


def _chapter_changes(
    workspace: StoryWorkspace, path: Path, outline_by_number: Mapping[int, str]
) -> tuple[ArtifactChange, str, str, tuple[ArtifactChange, ...]]:
    """把一个章文件映射为 Chapter + TextUnit 变化。

    返回 ``(chapter_change, chapter_id, normalized_hash, unit_changes)``。
    """
    stem = path.stem
    text = path.read_text(encoding="utf-8")
    number = _chapter_number(stem)
    outline_name = outline_by_number.get(number) if number is not None else None

    chapter_id = deterministic_story_id(ms_kinds.KIND_CHAPTER, f"v1:chapter:{stem}")
    narrative_id = (
        deterministic_story_id(story_kinds.KIND_CHAPTER, f"v1:outline:{outline_name}")
        if outline_name
        else None
    )
    change = import_chapter(
        ManuscriptCatalog(commit_id=None, entries=()),
        _deterministic_unit_ids(stem),
        chapter_id=chapter_id,
        title=outline_name or stem,
        text=text,
        order_key=f"{number:04d}" if number is not None else "0000",
        narrative_chapter_id=narrative_id,
    )
    # 旧 revision 归档与来源文件名收进 legacy_metadata：V2 不重放旧历史，但旧稿的
    # 哈希/统计作为只读附件随章保留（T17.3「旧 revision 是否按策略保留」）。
    chapter_change = next(c for c in change.artifact_changes if c.kind == ms_kinds.KIND_CHAPTER)
    content = dict(chapter_change.content)
    content["v1"] = {"source_file": path.name, "revisions": _revision_records(workspace, stem)}
    enriched = ArtifactChange(
        artifact_id=chapter_change.artifact_id,
        content=content,
        old_revision_id=None,
        kind=chapter_change.kind,
    )
    units = tuple(c for c in change.artifact_changes if c.artifact_id != chapter_change.artifact_id)
    return enriched, chapter_id, _text_hash(text), units


# --------------------------------------------------------------------------
# T17.1 扫描
# --------------------------------------------------------------------------
def scan_v1_workspace(workspace: StoryWorkspace) -> MigrationReport:
    """扫描整个 V1 工作区，逐文件归类；不写 Repository、不改源文件。"""
    story_plan = map_v1_workspace(workspace)
    story = story_plan.report
    outline_by_number = _outline_by_number(workspace)

    files = _iter_workspace_files(workspace)
    items: list[SourceItem] = []
    chapter_hashes: list[tuple[str, str]] = []
    chapter_chars = 0
    revision_count = 0

    # 章正文范围由唯一枚举决定（_chapter_files）；扫描计数、提案写入与校验都必须
    # 用它，避免「分类为 mapped 却不在导入范围」的漏导（评审 P1）。
    chapter_rels = {
        path.relative_to(workspace.story_dir).as_posix() for path in _chapter_files(workspace)
    }

    for rel, path in files:
        item = _classify_file(rel, path, workspace, story, outline_by_number)
        items.append(item)
        if rel in chapter_rels:
            text = path.read_text(encoding="utf-8")
            chapter_chars += _non_space_chars(text)
            chapter_hashes.append(
                (
                    deterministic_story_id(ms_kinds.KIND_CHAPTER, f"v1:chapter:{path.stem}"),
                    _text_hash(text),
                )
            )
        elif rel.startswith("chapters/.revisions/") and rel.endswith(".md") and path.stem.isdigit():
            revision_count += 1

    items.sort(key=lambda item: (item.path, item.outcome))
    counts = SourceCounts(
        files=len(files),
        story_entities=len(story.entities),
        chapters=len(chapter_rels),
        revisions=revision_count,
        chars=chapter_chars,
    )
    return MigrationReport(
        workspace=workspace.slug,
        run_id=manifest_hash(workspace),
        schema_version=IMPORT_SCHEMA_VERSION,
        items=tuple(items),
        story=story,
        counts=counts,
        chapter_hashes=tuple(sorted(chapter_hashes)),
    )


def _classify_file(
    rel: str,
    path: Path,
    workspace: StoryWorkspace,
    story: ImportReport,
    outline_by_number: Mapping[int, str],
) -> SourceItem:
    """给单个源文件一个归类；这是「每个源项恰一种结果」的唯一判定点。"""
    # 运行记录：显式 ignored + 原因
    if rel in _IGNORED_FILES:
        return SourceItem(rel, _type_of(rel), OUTCOME_IGNORED, detail=_IGNORED_FILES[rel])
    if rel.startswith("sessions/") and rel.endswith(".lock"):
        return SourceItem(rel, "session_lock", OUTCOME_IGNORED, detail=_SESSION_LOCK_REASON)
    if rel.startswith("sessions/") and rel.endswith(".jsonl"):
        return SourceItem(rel, "session_log", OUTCOME_IGNORED, detail=_SESSION_LOG_REASON)
    if rel.startswith("session-") and rel.endswith(".jsonl"):
        return SourceItem(rel, "session_archive", OUTCOME_IGNORED, detail=_SESSION_ARCHIVE_REASON)

    # 正文章节
    if rel.startswith("chapters/.revisions/"):
        chapter_stem = rel.split("/")[2]
        return SourceItem(
            rel,
            "revision",
            OUTCOME_LEGACY,
            target=deterministic_story_id(ms_kinds.KIND_CHAPTER, f"v1:chapter:{chapter_stem}"),
            detail="V2 不重放旧历史；旧稿哈希/统计作为 legacy_metadata 只读保留",
        )
    if _is_top_level_chapter(rel):
        chapter_id = deterministic_story_id(ms_kinds.KIND_CHAPTER, f"v1:chapter:{path.stem}")
        number = _chapter_number(path.stem)
        linked = outline_by_number.get(number) if number is not None else None
        segments = split_paragraphs(path.read_text(encoding="utf-8"))
        detail = f"→ {ms_kinds.KIND_CHAPTER}，{len(segments)} 段"
        if linked:
            detail += f"；关联叙事章「{linked}」"
        return SourceItem(rel, "chapter", OUTCOME_MAPPED, target=chapter_id, detail=detail)
    if rel.startswith("chapters/"):
        # 嵌套子目录或非 .md：不是受管章布局，显式报错而非静默丢弃/假装导入
        return SourceItem(
            rel,
            "chapter",
            OUTCOME_ERROR,
            detail=(
                "不支持的章节目录结构：正文只迁移 chapters/ 顶层的 *.md。"
                "嵌套子目录（如 chapters/archive/）不会被导入；"
                "须先把文件移到 chapters/ 顶层，或从源中移除后重新 dry-run"
            ),
        )

    # 角色卡：按文件名定位 story mapper 的对应实体
    if rel.startswith("bible/characters/"):
        entity = next(
            (
                e
                for e in story.entities
                if e.entity_type == "character" and e.source_name == path.stem
            ),
            None,
        )
        return _ledger_item(rel, "character", (entity,) if entity else ())

    # 单文件账本
    if rel.startswith("bible/"):
        entity_type = _LEDGER_TYPES.get(path.name)
        if entity_type is not None:
            ents = tuple(e for e in story.entities if e.entity_type == entity_type)
            return _ledger_item(rel, entity_type, ents)

    return SourceItem(
        rel,
        _type_of(rel),
        OUTCOME_ERROR,
        detail="未知源文件，映射表未覆盖；须显式归类后才能迁移（不静默丢弃）",
    )


def _ledger_item(rel: str, entity_type: str, entities: tuple[Any, ...]) -> SourceItem:
    """由 story mapper 的实体归类汇总出一个源文件的 outcome。"""
    if not entities:
        return SourceItem(rel, entity_type, OUTCOME_IGNORED, detail="该账本在源中无条目")
    if any(e.issues for e in entities):
        detail = "；".join(f"{i.code}@{i.where}" for e in entities for i in e.issues)
        return SourceItem(rel, entity_type, OUTCOME_ERROR, detail=detail)
    if all(e.planned for e in entities):
        kinds_used = ",".join(sorted({e.target_kind for e in entities}))
        return SourceItem(
            rel, entity_type, OUTCOME_MAPPED, detail=f"{len(entities)} 条 → {kinds_used}"
        )
    blocked = [e.source_name for e in entities if not e.planned]
    return SourceItem(
        rel, entity_type, OUTCOME_IGNORED, detail=f"部分条目无可迁移目标：{','.join(blocked)}"
    )


def _type_of(rel: str) -> str:
    if rel in ("story.yaml", "settings.yaml"):
        return "story"
    if rel == "sessions.json":
        return "session_index"
    if rel == "session.jsonl":
        return "session_log"
    if rel == ".session.jsonl.lock":
        return "session_lock"
    if rel == "events.jsonl":
        return "events"
    if rel.startswith("chapters/.revisions/"):
        return "revision"
    if rel.startswith("chapters/"):
        return "chapter"
    if rel.startswith("bible/characters/"):
        return "character"
    if rel.startswith("sessions/"):
        return "session_log"
    if rel.startswith("bible/"):
        return "ledger"
    return "other"


def plan_migration(workspace: StoryWorkspace) -> MigrationPlan:
    """dry-run：报告 + 可提交提案（故事 DomainChange 与正文章节变化）。"""
    story_plan = map_v1_workspace(workspace)
    report = scan_v1_workspace(workspace)
    outline_by_number = _outline_by_number(workspace)

    manuscript: list[ArtifactChange] = []
    for path in _chapter_files(workspace):
        chapter_change, _chapter_id, _hash, units = _chapter_changes(
            workspace, path, outline_by_number
        )
        manuscript.append(chapter_change)
        manuscript.extend(units)

    return MigrationPlan(
        report=report,
        story_proposal=story_plan.proposal,
        manuscript_changes=tuple(manuscript),
    )


# --------------------------------------------------------------------------
# T17.2 幂等导入
# --------------------------------------------------------------------------
class V1WorkspaceImporter:
    """把 V1 工作区导入到指定 V2 项目；写入只经 Commit Service。"""

    def __init__(
        self,
        reader: RepositoryReader,
        committer: CommitRepository,
        *,
        actor: Actor,
        branch_name: str = "main",
    ) -> None:
        self._reader = reader
        self._committer = committer
        self._actor = actor
        self._branch_name = branch_name

    def dry_run(self, workspace: StoryWorkspace) -> MigrationReport:
        return scan_v1_workspace(workspace)

    def apply(
        self,
        workspace: StoryWorkspace,
        *,
        project_id: ProjectId,
        confirm_run_id: str,
        summary: str | None = None,
    ) -> MigrationResult:
        """校验报告 → 幂等提交 → 回读校验。

        ``confirm_run_id`` 是调用方**已确认的 dry-run** run_id：与当前源 hash 不符
        说明源在工作区已变化，必须重新 dry-run（不静默用旧报告覆盖新源）。
        """
        plan = plan_migration(workspace)
        report = plan.report
        where = f"v1.import({project_id.value})"
        if report.run_id != confirm_run_id:
            raise _violation(
                ec.REPO_INVALID_CHANGE,
                where,
                f"源工作区已变化（当前 run_id={report.run_id[:12]}…，"
                f"确认的 run_id={confirm_run_id[:12]}…）",
                "重新运行 dry-run 并把新的 report.run_id 作为 confirm_run_id 传入",
                current=report.run_id,
                confirmed=confirm_run_id,
            )
        if not report.ok:
            raise _violation(
                ec.REPO_INVALID_CHANGE,
                where,
                f"迁移报告未通过（error={len(report.errors)}，"
                f"无理由遗漏={len(report.unexplained)}，故事侧错误={len(report.story.errors)}）",
                "修正报告中的阻断项后重新 dry-run；不使用未通过的报告导入",
                errors=[item.path for item in report.errors],
            )

        branch = self._reader.get_branch(project_id, self._branch_name)
        if branch is None:
            raise _violation(
                ec.REPO_BRANCH_NOT_FOUND,
                where,
                f"项目 {project_id.value} 下没有 {self._branch_name!r} 分支",
                "先用 create_project 建立项目与 main 分支，再导入",
                project_id=project_id.value,
            )
        key = import_key(report.run_id, report.schema_version)
        existing = self._reader.find_commit_result(project_id, key)
        if existing is None and branch.head_commit_id is not None:
            raise _violation(
                ec.REPO_INVALID_CHANGE,
                where,
                f"目标项目非空（head={branch.head_commit_id.value}），迁移只写入空项目",
                "新建一个空项目再导入；重复导入同一源会命中幂等键并返回原结果",
                project_id=project_id.value,
                head=branch.head_commit_id.value,
            )

        commit: CommitResult
        if existing is None:
            commit = self._committer.commit(
                CommitRequest(
                    project_id=project_id,
                    branch_id=branch.id,
                    expected_head=branch.head_commit_id,
                    actor=self._actor,
                    idempotency_key=key,
                    summary=summary or f"V1 工作区导入：{workspace.slug}",
                    changes=plan.manuscript_changes + _story_changes(plan.story_proposal),
                    relations=_story_relations(plan.story_proposal),
                    events=(
                        (
                            _EVENT_IMPORTED,
                            {
                                "workspace": workspace.slug,
                                "run_id": report.run_id,
                                "schema_version": report.schema_version,
                                "files": report.counts.files,
                                "chapters": report.counts.chapters,
                                "chars": report.counts.chars,
                            },
                        ),
                    ),
                )
            )
        else:
            commit = existing
        validation = self.validate(workspace, project_id=project_id, commit_id=commit.new_head)
        return MigrationResult(
            report=report,
            validation=validation,
            status=commit.status,
            commit_id=commit.commit_id.value if commit.commit_id else None,
            new_head=commit.new_head.value if commit.new_head else None,
            replayed=existing is not None,
        )

    # ------------------------------------------------------------------
    # T17.3 校验
    # ------------------------------------------------------------------
    def validate(
        self,
        workspace: StoryWorkspace,
        *,
        project_id: ProjectId,
        commit_id: CommitId | None,
    ) -> ValidationReport:
        """在新 Commit 上重算并比对；``commit_id`` 为空（no_change）时给出失败项。"""
        report = scan_v1_workspace(workspace)
        if commit_id is None:
            return ValidationReport(
                entries=(
                    ValidationEntry("commit", False, "存在新 Commit", "no_change（无新提交）"),
                )
            )
        snapshot = self._reader.get_snapshot(project_id, commit_id)
        story = StoryCatalog.from_snapshot(snapshot)
        catalog = ManuscriptCatalog.from_snapshot(snapshot)

        entries: list[ValidationEntry] = []
        expected_story = report.story.expected_artifacts
        actual_story = len(story.entries)
        entries.append(
            ValidationEntry(
                "story_artifacts",
                actual_story == expected_story,
                str(expected_story),
                str(actual_story),
            )
        )

        v2_chapters = catalog.by_kind(ms_kinds.KIND_CHAPTER)
        entries.append(
            ValidationEntry(
                "chapters",
                len(v2_chapters) == report.counts.chapters,
                str(report.counts.chapters),
                str(len(v2_chapters)),
            )
        )

        manuscript = catalog.manuscript()
        for chapter_id, expected_hash in report.chapter_hashes:
            actual_hash = _text_hash(manuscript.text_of(chapter_id))
            entries.append(
                ValidationEntry(
                    f"chapter_hash:{chapter_id}",
                    actual_hash == expected_hash,
                    expected_hash[:12],
                    actual_hash[:12],
                )
            )

        known = set(snapshot.artifacts)
        dangling: list[str] = []
        for rel in snapshot.relations:
            if rel.source not in known:
                dangling.append(rel.source.value)
            if rel.target not in known:
                dangling.append(rel.target.value)
        for chapter_entry in v2_chapters:
            narrative_id = chapter_entry.model().narrative_chapter_id
            if narrative_id is not None and ArtifactId(narrative_id) not in known:
                dangling.append(narrative_id)
        entries.append(ValidationEntry("no_dangling_ids", not dangling, "0", str(len(dangling))))

        missing_legacy: list[str] = []
        for path in _chapter_files(workspace):
            records = _revision_records(workspace, path.stem)
            if not records:
                continue
            chapter_id = deterministic_story_id(ms_kinds.KIND_CHAPTER, f"v1:chapter:{path.stem}")
            kept_entry = catalog.chapter_entry(chapter_id)
            kept = (kept_entry.content.get("v1", {}) if kept_entry else {}).get("revisions", [])
            if len(kept) != len(records):
                missing_legacy.append(path.stem)
        entries.append(
            ValidationEntry(
                "legacy_revisions_retained",
                not missing_legacy,
                "全部归档随章保留",
                ",".join(missing_legacy) or "全部保留",
            )
        )

        # 只核对**源中实际存在**的运行记录：旧单会话工作区可以没有会话索引，
        # 新工作区也可能没有事件日志——本就没有的记录不构成失败（评审 P2）。
        archived = {item.path for item in report.ignored}
        expected_runtime = sorted(
            rel for rel, _path in _iter_workspace_files(workspace) if _is_runtime_record(rel)
        )
        missing_runtime = [rel for rel in expected_runtime if rel not in archived]
        entries.append(
            ValidationEntry(
                "runtime_records_archived",
                not missing_runtime,
                f"源中存在的 {len(expected_runtime)} 项运行记录全部只读归档",
                ",".join(missing_runtime) or "全部归档",
            )
        )
        return ValidationReport(entries=tuple(entries))


def _story_changes(proposal: tuple[DomainChange, ...]) -> tuple[ArtifactChange, ...]:
    return tuple(change for domain_change in proposal for change in domain_change.artifact_changes)


def _story_relations(proposal: tuple[DomainChange, ...]) -> tuple[RelationChange, ...]:
    return tuple(rel for domain_change in proposal for rel in domain_change.relation_changes)


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)

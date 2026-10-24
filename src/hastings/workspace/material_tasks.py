"""资料整理任务：范围校验、域权限、发布与覆盖（0.7）。

资料页发起的整理不是另一套执行器：它复用轮次服务、材料账本、事件流与作品级互斥，
只在受理处把范围收窄成「一个资料域 + 1–10 个正文章节 + 可选人物」，并把「这一轮
能发布什么」限制成该域的一个工具。

几条边界：

- **一个任务只更新一个域**。前端五页对应六个域，人物页的身份披露与人物状态是两次独立
  任务；把两个域塞进一轮，作者看到的结果就无法归因。
- **范围由服务端重新核对**：客户端给的章号与指纹只是「作者看到的是哪一版」的声明，
  真实路径归类、章序唯一性、截止章与指纹都以服务端读到的为准。
- **空结果也是结果**：读过当前版本、确认没有相关发现，就记一条带指纹的覆盖记录；
  没提交结果的目标不算完成。
- **资料任务不改作品**：只读正文与设定，发布派生记录；没有写文件工具。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, Field, model_validator

from hastings.workspace.chapter_index import (
    ChapterIndexDraft,
    ChapterIndexEntry,
    ChapterIndexStore,
    IndexEvidence,
    publish_index,
)
from hastings.workspace.character_states import (
    CharacterStateStore,
    StateSnapshot,
    publish_states,
)
from hastings.workspace.clues import ClueEntry, ClueStore, publish_clues
from hastings.workspace.disclosure import (
    DisclosureStore,
    ObservedEvidence,
    ObservedFact,
    check_evidence,
    publish_observed,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.glossary import GlossaryStore, GlossaryTerm, publish_terms
from hastings.workspace.materials import MaterialLedger, ReadRequirement
from hastings.workspace.metadata import parse_character
from hastings.workspace.state import ConversationStore
from hastings.workspace.state_store import current_chapter
from hastings.workspace.story_index import (
    MODE_LABEL,
    OBSERVATION_MODES,
    ChapterProjection,
    EventObservation,
    ObservationMode,
    RecordRef,
    StoryIndexStore,
    StoryTime,
    validate_projection,
)
from hastings.workspace.story_materials import (
    COVERAGE_OUTCOMES,
    DOMAIN_LABEL,
    CoverageRecord,
    CoverageStore,
)
from hastings.workspace.timeline import TimelineEntry, TimelineStore, publish_timeline

__all__ = [
    "MAX_MATERIAL_CHAPTERS",
    "MAX_MATERIAL_ENTITIES",
    "MaterialTaskScope",
    "freeze_material_scope",
    "material_conversation_ids",
    "material_instructions",
    "publish_material_result",
    "scope_brief",
    "task_list",
    "task_view",
]

MaterialDomain = Literal[
    "story_index", "chapter_index", "disclosure", "roster", "timeline", "clues", "glossary"
]
CoverageOutcome = Literal["found", "empty", "insufficient"]

#: 一次整理最多覆盖的章节与人物数；与章节索引一致，分几次整理而不是一次跑整本书。
MAX_MATERIAL_CHAPTERS = 10
MAX_MATERIAL_ENTITIES = 10

_ENTITY_DOMAINS = {"disclosure", "roster"}


class MaterialTaskScope(BaseModel):
    """前端提交的整理范围；结构合法不代表范围可用，服务端会再核对一遍。"""

    domain: Literal[
        "story_index", "chapter_index", "disclosure", "roster", "timeline", "clues", "glossary"
    ]
    chapter_paths: list[str] = Field(min_length=1, max_length=MAX_MATERIAL_CHAPTERS)
    entity_paths: list[str] = Field(default_factory=list, max_length=MAX_MATERIAL_ENTITIES)
    as_of_chapter: int = Field(ge=0)
    #: 作者在页面上看到的版本；与受理时服务端读到的不同就拒绝，避免按旧页面发起。
    observed_digests: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_entities(self) -> MaterialTaskScope:
        needs_entities = self.domain in _ENTITY_DOMAINS
        if needs_entities and not self.entity_paths:
            raise ValueError("人物资料需要先选择人物小传")
        if not needs_entities and self.entity_paths:
            raise ValueError("这类资料不接受人物目标")
        return self


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _dedupe(paths: Sequence[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    for path in paths:
        if path not in ordered:
            ordered.append(path)
    return tuple(ordered)


def freeze_material_scope(
    workspace: Workspace, scope: MaterialTaskScope | Mapping[str, object]
) -> dict[str, object]:
    """核对并冻结范围：真实路径、章序、截止章与指纹都由服务端决定。"""
    domain: str
    chapter_paths: list[str]
    entity_paths: list[str]
    requested_as_of: int
    observed: dict[str, str]
    if isinstance(scope, MaterialTaskScope):
        domain = scope.domain
        chapter_paths = list(scope.chapter_paths)
        entity_paths = list(scope.entity_paths)
        requested_as_of = scope.as_of_chapter
        observed = dict(scope.observed_digests)
    else:
        raw = dict(scope)
        domain = str(raw.get("domain") or "")
        chapter_paths = [str(item) for item in cast(list[object], raw.get("chapter_paths") or [])]
        entity_paths = [str(item) for item in cast(list[object], raw.get("entity_paths") or [])]
        requested_as_of = int(str(raw.get("as_of_chapter") or 0))
        observed = {
            str(key): str(value)
            for key, value in cast(dict[object, object], raw.get("observed_digests") or {}).items()
        }
    if domain not in DOMAIN_LABEL:
        raise _scope_error("不支持的资料种类", detail={"domain": domain})

    paths = _dedupe(chapter_paths)
    if not paths:
        raise _scope_error("没有选择要整理的章节")
    if len(paths) > MAX_MATERIAL_CHAPTERS:
        raise _scope_error(
            f"一次最多整理 {MAX_MATERIAL_CHAPTERS} 章，这次有 {len(paths)} 章",
            detail={"limit": MAX_MATERIAL_CHAPTERS, "count": len(paths)},
        )

    latest = current_chapter(workspace)
    # 超出全书末章按末章处理；目标章仍然不能越过它。
    as_of = latest if requested_as_of <= 0 else min(latest, requested_as_of)
    catalog = {entry.path: entry for entry in workspace.catalog().chapters}
    chapters: dict[str, int] = {}
    digests: dict[str, str] = {}
    seen_numbers: dict[int, str] = {}
    for path in paths:
        entry = catalog.get(path)
        if entry is None or not entry.exists:
            raise WorkspaceError(
                code="MATERIAL_SCOPE_INVALID",
                where="material_tasks.freeze",
                why=f"要整理的章节不存在：{path}",
                fix="重新读取目录后再选择章节",
                details={"path": path},
            )
        if not is_managed_path(path) or not path.startswith("正文/") or not path.endswith(".md"):
            raise _scope_error(f"资料只整理正文章节：{path}", detail={"path": path})
        if entry.chapter_number is None:
            raise WorkspaceError(
                code="MATERIAL_SCOPE_CONFLICT",
                where="material_tasks.freeze",
                why=f"无法从文件名读出章序：{path}",
                fix="把文件名改成「四位序号 + 标题」，再重新选择",
                details={"path": path},
            )
        number = entry.chapter_number
        if number in seen_numbers:
            raise WorkspaceError(
                code="MATERIAL_SCOPE_CONFLICT",
                where="material_tasks.freeze",
                why=(
                    f"第 {number} 章出现在两个文件里：{seen_numbers[number]}、{path}；"
                    "章序重复时无法按时点整理"
                ),
                fix="先修正重复的章节编号，再重新发起整理",
                details={"chapter": number, "paths": [seen_numbers[number], path]},
            )
        seen_numbers[number] = path
        if number > as_of:
            raise _scope_error(
                f"{path} 属于第 {number} 章，超出本次查看时点（第 {as_of} 章）",
                detail={"path": path, "chapter": number, "as_of_chapter": as_of},
            )
        try:
            view = workspace.read(path)
        except WorkspaceError as exc:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_NOT_FOUND",
                where="material_tasks.freeze",
                why=f"要整理的章节当前不可用：{path}（{exc.why}）",
                fix="重新读取目录后再选择章节",
                details={"path": path},
            ) from exc
        claimed = observed.get(path)
        if claimed is not None and claimed != view.digest:
            raise WorkspaceError(
                code="CONTEXT_SOURCE_STALE_CONFLICT",
                where="material_tasks.freeze",
                why=f"这一章在你打开资料页之后被改动过：{path}",
                fix="重新读取资料页，确认最新内容后再发起整理",
                details={"path": path, "seen": claimed, "current": view.digest},
            )
        chapters[path] = number
        digests[path] = view.digest

    entities = _dedupe(entity_paths)
    entity_digests: dict[str, str] = {}
    if domain in _ENTITY_DOMAINS:
        if not entities:
            raise _scope_error("人物资料需要先选择人物小传")
        if len(entities) > MAX_MATERIAL_ENTITIES:
            raise _scope_error(
                f"一次最多整理 {MAX_MATERIAL_ENTITIES} 个人物，这次有 {len(entities)} 个",
                detail={"limit": MAX_MATERIAL_ENTITIES, "count": len(entities)},
            )
        available = {entry.path for entry in workspace.catalog().characters if entry.exists}
        for path in entities:
            if path not in available:
                raise WorkspaceError(
                    code="MATERIAL_SCOPE_INVALID",
                    where="material_tasks.freeze",
                    why=f"要核对的人物小传不存在：{path}",
                    fix="先在对话里建立小传，或在人物页重新选择",
                    details={"path": path},
                )
            view = workspace.read(path)
            claimed = observed.get(path)
            if claimed is not None and claimed != view.digest:
                raise WorkspaceError(
                    code="CONTEXT_SOURCE_STALE_CONFLICT",
                    where="material_tasks.freeze",
                    why=f"这份小传在你打开资料页之后被改动过：{path}",
                    fix="重新读取资料页，确认最新内容后再发起整理",
                    details={"path": path, "seen": claimed, "current": view.digest},
                )
            entity_digests[path] = view.digest
    elif entities:
        raise _scope_error("这类资料不接受人物目标")

    return {
        "domain": domain,
        "chapter_paths": list(chapters),
        "entity_paths": list(entities),
        "as_of_chapter": as_of,
        # 客户端请求的时点（0 表示全书末章）；同键重试按它比较，不按解析后的章号。
        "requested_as_of": requested_as_of,
        "digests": digests,
        "entity_digests": entity_digests,
        "chapters": chapters,
        "created_at": _now(),
    }


def _scope_error(why: str, *, detail: Mapping[str, object] | None = None) -> WorkspaceError:
    return WorkspaceError(
        code="MATERIAL_SCOPE_INVALID",
        where="material_tasks.freeze",
        why=why,
        fix="按资料页当前列出的章节与人物重新选择；一次 1–10 章",
        details=detail,
    )


def targets(scope: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    """任务的目标清单：章节域按章，人物域按「人物 × 章」。"""
    domain = str(scope.get("domain"))
    chapters = cast(dict[str, object], scope.get("chapters") or {})
    rows: list[dict[str, object]] = []
    if domain in _ENTITY_DOMAINS:
        for entity in cast(list[object], scope.get("entity_paths") or []):
            for path, number in chapters.items():
                rows.append({"path": path, "chapter": int(str(number)), "entity_path": str(entity)})
    else:
        for path, number in chapters.items():
            rows.append({"path": path, "chapter": int(str(number)), "entity_path": ""})
    return tuple(rows)


# -- 上下文与提示 -----------------------------------------------------------


_DOMAIN_RULES: dict[str, str] = {
    "story_index": (
        "每章提交一份摘要（≤500 字符）与事件观察。\n"
        "每条事件写清表达方式：narration（叙述）、testimony（证词）、recollection（回忆）、"
        "hypothesis（推测）、dream（梦）或 unknown（不确定）；回忆与证词不等于事实，"
        "不要改写成客观叙述。\n"
        "时间写原文说法；只有原文能明确对应日期时才填 normalized（YYYY-MM-DD）。"
        "只有正文明确写出先后的，才用 before／after 引用其他观察的 ID；不确定就留空。\n"
        "人物与作者事件用完整引用（库、模块、修订、条目四段）；不知道就留空，不要只写名字。"
    ),
    "chapter_index": (
        "每章提交一份摘要（≤500 字符）与带证据的事件、人物、时间地点、线索。\n"
        "摘要只写这一章实际发生了什么，不从旧摘要改写；这一章有正文就必须提交摘要。"
    ),
    "timeline": (
        "每章提交一个时间锚点：原文怎么说的时间、跨了多久、主要在哪。\n"
        "时间写原文的说法（例如「10月21日深夜」），不要换算成自己推的日期；"
        "原文确实没有时间信息时提交空结果并说明，不要编一个时间。"
    ),
    "clues": (
        "登记读者能观察到的线索：埋在哪一章、计划哪一章回收、现在什么状态。\n"
        "只有正文确实写了回收过程才标 resolved，并同时给出回收章与回收处原文；"
        "计划回收章和「已废弃」是作者的决定，不要凭「读完后没再出现」自行设定。\n"
        "计划回收章可以落在本次截止章之后，它是计划不是证据；埋设与回收的原文都必须在"
        "截止章以内。\n"
        "整理回收章时，已有线索要保留并复核原埋设证据，不能顺带改写别的章的埋设事实。"
    ),
    "glossary": (
        "登记正文里确实出现过的专名：地名、组织、物件；人物姓名与别名来自小传，不重复登记。\n"
        "同一事物的其他写法放进 aliases；名称冲突逐项列出，不要自动合并或改正文。"
    ),
    "disclosure": (
        "核对「读者从第几章起知道了什么」。只记录正文确实写到的披露；凭设定推测的不要发布。\n"
        "作者在小传里写的计划披露不是正文事实，不能当成已披露；"
        "某一章没有新的披露就提交空结果。"
    ),
    "roster": (
        "核对人物在某一章结束时的位置、是否还活着、立场，每条都要有原文依据。\n"
        "只记这一章正文明确写到的事；人物说自己在某处不等于他真在。\n"
        "没有生死依据时提交信息不足，不要为了填满字段猜「还活着」。"
    ),
}


def material_instructions(domain: str) -> str:
    """这一轮的任务要求；按域写清允许提交什么、什么不算完成。"""
    label = DOMAIN_LABEL.get(domain, "资料")
    return f"""\
你在整理这部作品的「{label}」。这一轮只读正文与设定，发布派生资料，不改任何作品文件。

## 这一轮只做{label}
- 只能用目录、搜索、读取和本域的结果提交工具；没有写文件工具，也不要试图改正文或小传。
- 只处理任务里列出的章节与人物，不要顺手扩大范围；
  额外读到的依据可以用来佐证，但不能成为新的输出目标。
- 每读完一个目标再提交：有发现用 found，读完确认没有相关发现用 empty，信息不足用 insufficient。
- 空结果要写一句说明；没有提交结果的目标不算完成，即使你在回复里说「已经整理完」。
- 提交的每条记录都要给路径、版本、字符区间与引文，区间与引文必须一致。

## 判定规则
{_DOMAIN_RULES.get(domain, "")}
"""


def scope_brief(workspace: Workspace, root: Path, scope: Mapping[str, object]) -> str:
    """给模型的任务说明：范围、完成条件与已有记录（待核对，不是当前依据）。"""
    domain = str(scope.get("domain"))
    chapters = cast(dict[str, object], scope.get("chapters") or {})
    entities = [str(item) for item in cast(list[object], scope.get("entity_paths") or [])]
    as_of = int(str(scope.get("as_of_chapter") or 0))
    catalog = {entry.path: entry for entry in workspace.catalog().chapters}
    lines = [
        "### 本次整理",
        f"- 资料种类：{DOMAIN_LABEL.get(domain, domain)}",
        f"- 截止章：第 {as_of} 章（只看这一章及以前写到的内容）",
        "- 目标章节：",
    ]
    for path, number in chapters.items():
        title = catalog[path].display_title if path in catalog else path
        lines.append(f"  - 第 {number} 章｜`{path}`｜{title}")
    if entities:
        lines.append("- 目标人物：")
        for path in entities:
            name = path.rsplit("/", 1)[-1].removesuffix(".md")
            lines.append(f"  - `{path}`｜{name}")
    lines.append("")
    lines.append("### 完成条件")
    lines.append(
        "- 每个目标提交一次结果（found / empty / insufficient）；空结果也要说明读完了什么。"
    )
    lines.append("- 证据区间用 read_file 返回的字符位置，引文要与区间完全一致。")
    lines.append("- 证据只能来自截止章及以前的正文；发布未来章的依据会被拒绝。")
    if domain in _ENTITY_DOMAINS:
        lines.append("- 提交前要完整读过所选人物小传与对应章节正文。")
    existing = _existing_notes(workspace, root, domain, scope)
    if existing:
        lines.append("")
        lines.append("### 已有记录（待核对，不是当前依据）")
        lines.extend(existing)
    return "\n".join(lines)


def _existing_notes(
    workspace: Workspace, root: Path, domain: str, scope: Mapping[str, object]
) -> list[str]:
    chapters = cast(dict[str, object], scope.get("chapters") or {})
    numbers = {int(str(number)) for number in chapters.values()}
    entities = [str(item) for item in cast(list[object], scope.get("entity_paths") or [])]
    lines: list[str] = []
    if domain == "story_index":
        story_store = StoryIndexStore(root)
        for path, projection in story_store.read_all().items():
            if path not in chapters:
                continue
            modes = "、".join(
                sorted({MODE_LABEL.get(item.mode, item.mode) for item in projection.events})
            )
            lines.append(
                f"- `{path}` 上次摘要：{projection.summary}"
                + (f"（事件 {len(projection.events)} 条：{modes}）" if modes else "")
            )
    elif domain == "chapter_index":
        index_store = ChapterIndexStore(root)
        for path in chapters:
            record = index_store.read_all().get(path)
            if record is not None:
                lines.append(f"- `{path}` 上次摘要：{record.summary}")
    elif domain == "timeline":
        timeline_store = TimelineStore(root)
        for anchor in timeline_store.entries():
            if anchor.chapter in numbers:
                lines.append(f"- 第{anchor.chapter}章 上次记录的时间：{anchor.when}")
    elif domain == "clues":
        clue_store = ClueStore(root)
        for clue in clue_store.entries():
            if clue.planted_chapter > int(str(scope.get("as_of_chapter") or 0)):
                continue
            lines.append(
                f"- 「{clue.clue}」第{clue.planted_chapter}章埋下，状态 {clue.status}；"
                f"上次依据：{clue.evidence.quote}"
            )
    elif domain == "glossary":
        glossary_store = GlossaryStore(root)
        for term in glossary_store.entries():
            alias = f"（{'、'.join(term.aliases)}）" if term.aliases else ""
            lines.append(f"- {term.term}{alias}｜{term.kind}｜第{term.first_chapter}章")
    elif domain == "disclosure":
        disclosure_store = DisclosureStore(root)
        for path in entities:
            try:
                meta = parse_character(path, workspace.read(path).text)
            except WorkspaceError:
                continue
            declared = "、".join(
                f"第{item.chapter}章:{item.fact}" for item in (*meta.revealed, *meta.planned)
            )
            if declared:
                lines.append(f"- {meta.title} 小传里的声明/计划：{declared}")
            for fact in disclosure_store.entries():
                if fact.entity_path == path:
                    lines.append(
                        f"- {meta.title} 上次核对：第{fact.chapter}章 {fact.fact}"
                        f"（依据：{fact.evidence.quote}）"
                    )
    elif domain == "roster":
        roster_store = CharacterStateStore(root)
        for snapshot in roster_store.entries():
            if snapshot.entity_path in entities:
                lines.append(
                    f"- `{snapshot.entity_path}` 第{snapshot.chapter}章："
                    f"{snapshot.location or '位置未记'}，"
                    f"{'在世' if snapshot.alive else '已死亡'}，"
                    f"立场 {snapshot.stance or '未记'}"
                )
    return lines


# -- 发布 -------------------------------------------------------------------


def publish_material_result(
    *,
    workspace: Workspace,
    root: Path,
    scope: Mapping[str, object],
    ledger: MaterialLedger,
    turn_id: str,
    domain: str,
    path: str,
    entity_path: str,
    outcome: str,
    note: str,
    records: Sequence[Mapping[str, object]],
    digest: str,
) -> dict[str, object]:
    """发布一个目标的结果，并写入覆盖记录；任一校验不通过都不写。

    ``records`` 的结构由域决定，和该域的发布工具一一对应；空结果不允许带记录，
    有结果不允许零条记录。
    """
    if domain != str(scope.get("domain")):
        raise WorkspaceError(
            code="MATERIAL_DOMAIN_INVALID",
            where=f"material_tasks.publish:{domain}",
            why=(
                f"这次任务只整理「{DOMAIN_LABEL.get(str(scope.get('domain')), '')}」，"
                "不能发布其他资料"
            ),
            fix="只提交本次任务指定资料种类的结果",
            details={"expected": scope.get("domain"), "given": domain},
        )
    if outcome not in COVERAGE_OUTCOMES:
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where="material_tasks.publish",
            why=f"不支持的结果类型：{outcome}",
            fix="用 found（有发现）、empty（读完无发现）或 insufficient（信息不足）",
        )
    chapter_paths = [str(item) for item in cast(list[object], scope.get("chapter_paths") or [])]
    if path not in chapter_paths:
        raise WorkspaceError(
            code="MATERIAL_TARGET_INVALID",
            where=f"material_tasks.publish:{path}",
            why="这一章不在本次整理范围内",
            fix="只提交本次任务列出的章节；需要更多章节时由作者重新发起",
            details={"path": path, "allowed": chapter_paths},
        )
    entity_paths = [str(item) for item in cast(list[object], scope.get("entity_paths") or [])]
    if domain in _ENTITY_DOMAINS:
        if entity_path not in entity_paths:
            raise WorkspaceError(
                code="MATERIAL_TARGET_INVALID",
                where=f"material_tasks.publish:{entity_path}",
                why="这份小传不在本次整理范围内",
                fix="只提交本次任务选择的人物",
                details={"path": entity_path, "allowed": entity_paths},
            )
    elif entity_path:
        raise WorkspaceError(
            code="MATERIAL_TARGET_INVALID",
            where="material_tasks.publish",
            why="这类资料不按人物提交",
            fix="不要带人物路径；需要核对人物时从人物页发起",
            details={"entity": entity_path},
        )
    if domain == "chapter_index" and outcome != "found":
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where=f"material_tasks.publish:{path}",
            why="章节概览对有正文的章节必须提交摘要，不能用空结果绕过",
            fix="按当前原文写一份摘要与条目后再提交",
        )
    if domain == "chapter_index" and outcome != "found":
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where=f"material_tasks.publish:{path}",
            why="章节概览对有正文的章节必须提交摘要，不能用空结果绕过",
            fix="按当前原文写一份摘要与条目后再提交",
        )
    if outcome == "found" and not records:
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where=f"material_tasks.publish:{path}",
            why="标为有发现但没有提交任何条目",
            fix="提交至少一条带原文依据的记录，或改报 empty / insufficient",
        )
    if outcome != "found" and records:
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where=f"material_tasks.publish:{path}",
            why="空结果或信息不足不能附带条目",
            fix="要么按 found 提交条目，要么不提交条目并说明原因",
        )
    if outcome != "found" and not note.strip():
        raise WorkspaceError(
            code="MATERIAL_RESULT_INVALID",
            where=f"material_tasks.publish:{path}",
            why="空结果需要一句说明，作者才知道你读到了什么",
            fix="用一句话说明这一章没有相关发现或信息不足的原因",
        )

    try:
        source = workspace.read(path)
    except WorkspaceError as exc:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_NOT_FOUND",
            where=f"material_tasks.publish:{path}",
            why=f"目标章节当前不可用：{path}（{exc.why}）",
            fix="重新读取资料页后重新发起整理",
            details={"path": path},
        ) from exc
    frozen = cast(dict[str, str], scope.get("digests") or {})
    if digest != source.digest or frozen.get(path) != source.digest:
        raise WorkspaceError(
            code="CONTEXT_SOURCE_STALE_CONFLICT",
            where=f"material_tasks.publish:{path}",
            why="这一章在受理之后被改动过，结果不能按旧内容发布",
            fix="重新读取当前内容后再整理这一章",
            details={
                "path": path,
                "expected": frozen.get(path),
                "current": source.digest,
            },
        )
    ledger.require_target_read(workspace, ReadRequirement.full(path))
    if domain in _ENTITY_DOMAINS:
        ledger.require_target_read(workspace, ReadRequirement.full(entity_path))
    chapter_number = int(str(cast(dict[str, object], scope.get("chapters") or {})[path]))
    as_of = int(str(scope.get("as_of_chapter") or 0))
    if chapter_number > as_of:
        raise WorkspaceError(
            code="MATERIAL_TARGET_INVALID",
            where=f"material_tasks.publish:{path}",
            why="目标章节超出本次截止章",
            fix="只整理截止章及以前的章节",
            details={"chapter": chapter_number, "as_of_chapter": as_of},
        )

    # 空结果通常只记覆盖，不写域存储：没有发现不等于清空已有记录。
    # 例外是「一章一份贡献」的域：索引、时间线、线索与专名重建为 empty 时，
    # 只移除这一章的贡献，其他章与作者安排都不动。
    published = 0
    if outcome == "found":
        published = _dispatch(
            workspace=workspace,
            root=root,
            domain=domain,
            chapter_path=path,
            chapter_number=chapter_number,
            entity_path=entity_path,
            as_of=as_of,
            records=records,
            ledger=ledger,
        )
    elif outcome == "empty" and domain == "story_index":
        StoryIndexStore(root).remove(path)
    elif outcome == "empty" and domain == "timeline":
        publish_timeline(workspace, TimelineStore(root), (), source_chapter=chapter_number)
    elif outcome == "empty" and domain == "clues":
        publish_clues(workspace, ClueStore(root), (), source_chapter=chapter_number)
    elif outcome == "empty" and domain == "glossary":
        publish_terms(workspace, GlossaryStore(root), (), source_chapter=chapter_number)

    CoverageStore(root).record(
        CoverageRecord(
            domain=cast(MaterialDomain, domain),
            path=path,
            entity_path=entity_path,
            digest=source.digest,
            outcome=outcome,
            task_id=turn_id,
            at=_now(),
            note=note.strip()[:200],
        )
    )
    return {
        "domain": domain,
        "path": path,
        "entity": entity_path,
        "outcome": outcome,
        "published": published,
        "note": note.strip()[:200],
    }


def _dispatch(
    *,
    workspace: Workspace,
    root: Path,
    domain: str,
    chapter_path: str,
    chapter_number: int,
    entity_path: str,
    as_of: int,
    records: Sequence[Mapping[str, object]],
    ledger: MaterialLedger,
) -> int:
    if domain == "story_index":
        if not records:
            raise WorkspaceError(
                code="MATERIAL_RESULT_INVALID",
                where=f"material_tasks.publish:{chapter_path}",
                why="章节索引必须提交摘要与事件，不能用空数组绕过",
                fix="按当前原文写一份摘要与事件后再提交",
            )
        raw = dict(records[0])
        events_raw = raw.get("events")
        if not isinstance(events_raw, list):
            raise _result_error("events", "必须是数组；这一章没有事件时提交空数组")
        events = tuple(
            _story_event(item, chapter_number) for item in events_raw if isinstance(item, dict)
        )
        if len(events) != len(events_raw):
            raise _result_error("events", "每一项必须是对象")
        for event in events:
            for item in event.evidence:
                check_evidence(workspace, item, chapter=chapter_number, ledger=ledger)
        projection = ChapterProjection(
            path=chapter_path,
            digest=workspace.read(chapter_path).digest,
            chapter=chapter_number,
            summary=str(raw.get("summary") or ""),
            events=events,
        )
        validate_projection(workspace, projection)
        StoryIndexStore(root).publish(projection, expected_digest=projection.digest)
        return 1
    if domain == "chapter_index":
        if not records:
            raise WorkspaceError(
                code="MATERIAL_RESULT_INVALID",
                where=f"material_tasks.publish:{chapter_path}",
                why="章节概览必须提交摘要，不能用空结果绕过",
                fix="按当前原文写一份摘要与条目后再提交",
            )
        raw = dict(records[0])
        draft = ChapterIndexDraft(
            source_path=chapter_path,
            source_digest=workspace.read(chapter_path).digest,
            summary=str(raw.get("summary") or ""),
            evidence=_index_evidence(raw.get("evidence")),
            events=_index_entries(raw.get("events"), "event"),
            characters=_index_entries(raw.get("characters"), "character"),
            places=_index_entries(raw.get("places"), "place"),
            clues=_index_entries(raw.get("clues"), "clue"),
        )
        publish_index(
            draft,
            workspace=workspace,
            ledger=ledger,
            store=ChapterIndexStore(root),
            allowed_paths=(chapter_path,),
        )
        return 1
    if domain == "timeline":
        anchors = tuple(_timeline_entry(item, chapter_number, as_of) for item in records)
        for anchor in anchors:
            check_evidence(workspace, anchor.evidence, chapter=anchor.chapter, ledger=ledger)
        publish_timeline(workspace, TimelineStore(root), anchors, source_chapter=chapter_number)
        return len(anchors)
    if domain == "clues":
        clues = tuple(_clue_entry(item, as_of) for item in records)
        for clue in clues:
            check_evidence(workspace, clue.evidence, chapter=clue.planted_chapter, ledger=ledger)
            if clue.resolution_evidence is not None and clue.resolution_chapter is not None:
                check_evidence(
                    workspace,
                    clue.resolution_evidence,
                    chapter=clue.resolution_chapter,
                    ledger=ledger,
                )
        publish_clues(workspace, ClueStore(root), clues, source_chapter=chapter_number)
        return len(clues)
    if domain == "glossary":
        terms = tuple(_glossary_term(item, as_of) for item in records)
        for term in terms:
            if term.evidence is not None:
                check_evidence(workspace, term.evidence, chapter=term.first_chapter, ledger=ledger)
        publish_terms(workspace, GlossaryStore(root), terms, source_chapter=chapter_number)
        return len(terms)
    if domain == "disclosure":
        facts = tuple(_observed_fact(item, entity_path, workspace, as_of) for item in records)
        for fact in facts:
            check_evidence(workspace, fact.evidence, chapter=fact.chapter, ledger=ledger)
        publish_observed(workspace, DisclosureStore(root), facts)
        return len(facts)
    snapshots = tuple(_state_snapshot(item, entity_path, workspace, as_of) for item in records)
    for snapshot in snapshots:
        check_evidence(workspace, snapshot.evidence, chapter=snapshot.chapter, ledger=ledger)
    publish_states(workspace, CharacterStateStore(root), snapshots)
    return len(snapshots)


def _evidence_from(raw: object) -> ObservedEvidence:
    if not isinstance(raw, dict):
        raise _result_error("evidence", "必须是对象")
    try:
        return ObservedEvidence(
            path=str(raw["path"]),
            digest=str(raw["digest"]),
            start=int(raw["start"]),
            end=int(raw["end"]),
            quote=str(raw["quote"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise _result_error("evidence", "需要 path、digest、start、end、quote") from exc


def _story_event(item: Mapping[str, object], chapter: int) -> EventObservation:
    """把模型提交的一条事件转成观察；表达方式与引用格式在这里就拦住。"""
    text = str(item.get("text") or "").strip()
    mode = str(item.get("mode") or "unknown")
    if mode not in OBSERVATION_MODES:
        raise _result_error(
            "mode",
            "只能是 narration、testimony、recollection、hypothesis、dream 或 unknown",
        )
    evidence_raw = item.get("evidence")
    if not isinstance(evidence_raw, list) or not evidence_raw:
        raise _result_error("evidence", "每条事件至少要有一处原文依据")
    story_time = _story_time(item.get("story_time"))
    return EventObservation(
        text=text,
        chapter=chapter,
        mode=cast(ObservationMode, mode),
        evidence=tuple(_evidence_from(value) for value in evidence_raw),
        story_time=story_time,
        speaker=_record_ref(item.get("speaker"), "speaker"),
        related=_record_refs(item.get("related"), "related"),
        author_event=_record_ref(item.get("author_event"), "author_event"),
    )


def _story_time(raw: object) -> StoryTime:
    if raw is None:
        return StoryTime()
    if not isinstance(raw, dict):
        raise _result_error("story_time", "必须是对象或省略")
    return StoryTime(
        label=str(raw.get("label") or ""),
        normalized=str(raw.get("normalized") or ""),
        before=_id_list(raw.get("before"), "story_time.before"),
        after=_id_list(raw.get("after"), "story_time.after"),
    )


def _id_list(raw: object, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _result_error(field, "必须是数组")
    return tuple(str(item) for item in raw if str(item).strip())


def _record_ref(raw: object, field: str) -> RecordRef | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or len(raw) != 4:
        raise _result_error(field, "必须是库、模块、修订、条目四段引用")
    parts = tuple(str(item) for item in raw)
    return cast(RecordRef, parts)


def _record_refs(raw: object, field: str) -> tuple[RecordRef, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _result_error(field, "必须是数组")
    refs: list[RecordRef] = []
    for item in raw:
        ref = _record_ref(item, field)
        if ref is not None:
            refs.append(ref)
    return tuple(refs)


def _index_evidence(raw: object) -> tuple[IndexEvidence, ...]:
    if not isinstance(raw, list):
        raise _result_error("evidence", "必须是数组")
    items: list[IndexEvidence] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _result_error("evidence", "每一项必须是对象")
        try:
            items.append(
                IndexEvidence(
                    index=int(item["index"]),
                    start=int(item["start"]),
                    end=int(item["end"]),
                    quote=str(item["quote"]),
                )
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise _result_error("evidence", "需要 index、start、end、quote") from exc
    return tuple(items)


def _index_entries(raw: object, kind: str) -> tuple[ChapterIndexEntry, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _result_error(kind, "必须是数组")
    items: list[ChapterIndexEntry] = []
    for item in raw:
        if not isinstance(item, dict):
            raise _result_error(kind, "每一项必须是对象")
        text = str(item.get("text") or "")
        evidence = item.get("evidence") or []
        if not isinstance(evidence, list):
            raise _result_error(kind, "evidence 必须是证据编号数组")
        items.append(
            ChapterIndexEntry(
                kind=cast(Any, kind),
                text=text,
                evidence=tuple(int(value) for value in evidence),
            )
        )
    return tuple(items)


def _timeline_entry(item: Mapping[str, object], chapter: int, as_of: int) -> TimelineEntry:
    when = str(item.get("when") or "").strip()
    if not when:
        raise _result_error("when", "时间锚点不能为空；这一章没有时间信息时请提交空结果")
    if chapter > as_of:
        raise _result_error("chapter", "超出截止章")
    return TimelineEntry(
        chapter=chapter,
        when=when,
        span=str(item.get("span") or ""),
        location=str(item.get("location") or ""),
        evidence=_evidence_from(item.get("evidence")),
    )


def _clue_entry(item: Mapping[str, object], as_of: int) -> ClueEntry:
    """把一条提交转成台账条目。

    截止章只管正文证据：埋设与回收都要有已写出的原文，因此不得晚于本次截止章。
    ``due_chapter`` 是作者的回收计划，可以落在截止章之后——否则写到第 8 章就没法
    登记「计划第 22 章回收」，这正是伏笔台账存在的理由。计划不能早于埋设由
    ``publish_clues`` 统一校验，不在这里重复。
    """
    status = str(item.get("status") or "open")
    if status not in ("open", "resolved", "dropped"):
        raise _result_error("status", "只能是 open、resolved 或 dropped")
    planted = int(str(item.get("planted_chapter") or 0))
    due = item.get("due_chapter")
    resolution = item.get("resolution_chapter")
    if planted > as_of:
        raise _result_error("planted_chapter", "埋设章超出本次截止章")
    if resolution is not None and int(str(resolution)) > as_of:
        raise _result_error("resolution_chapter", "回收章超出本次截止章")
    resolution_evidence = item.get("resolution_evidence")
    return ClueEntry(
        clue=str(item.get("clue") or "").strip(),
        planted_chapter=planted,
        due_chapter=int(str(due)) if due is not None else None,
        status=cast(Any, status),
        supports=str(item.get("supports") or ""),
        evidence=_evidence_from(item.get("evidence")),
        resolution_chapter=int(str(resolution)) if resolution is not None else None,
        resolution_evidence=(
            _evidence_from(resolution_evidence) if resolution_evidence is not None else None
        ),
    )


def _glossary_term(item: Mapping[str, object], as_of: int) -> GlossaryTerm:
    kind = str(item.get("kind") or "item")
    if kind not in ("person", "place", "org", "item"):
        raise _result_error("kind", "只能是 person / place / org / item")
    first = int(str(item.get("first_chapter") or 0))
    if first > as_of:
        raise _result_error("first_chapter", "首次出现章超出本次截止章")
    aliases = item.get("aliases") or []
    if not isinstance(aliases, list):
        raise _result_error("aliases", "必须是字符串数组")
    return GlossaryTerm(
        term=str(item.get("term") or "").strip(),
        kind=cast(Any, kind),
        aliases=tuple(str(alias) for alias in aliases if str(alias).strip()),
        first_chapter=first,
        source="text",
        evidence=_evidence_from(item.get("evidence")),
    )


def _observed_fact(
    item: Mapping[str, object], entity_path: str, workspace: Workspace, as_of: int
) -> ObservedFact:
    chapter = int(str(item.get("chapter") or 0))
    if chapter > as_of:
        raise _result_error("chapter", "披露章超出本次截止章")
    return ObservedFact(
        entity_path=entity_path,
        entity_digest=workspace.read(entity_path).digest,
        fact=str(item.get("fact") or "").strip(),
        chapter=chapter,
        evidence=_evidence_from(item.get("evidence")),
    )


def _state_snapshot(
    item: Mapping[str, object], entity_path: str, workspace: Workspace, as_of: int
) -> StateSnapshot:
    chapter = int(str(item.get("chapter") or 0))
    if chapter > as_of:
        raise _result_error("chapter", "状态章超出本次截止章")
    alive = item.get("alive")
    if not isinstance(alive, bool):
        raise _result_error("alive", "必须是布尔值；没有生死依据时请提交信息不足，不要猜")
    return StateSnapshot(
        entity_path=entity_path,
        entity_digest=workspace.read(entity_path).digest,
        chapter=chapter,
        location=str(item.get("location") or ""),
        alive=alive,
        stance=str(item.get("stance") or ""),
        evidence=_evidence_from(item.get("evidence")),
    )


def _result_error(name: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="MATERIAL_RESULT_INVALID",
        where=f"material_tasks.result:{name}",
        why=why,
        fix="按当前原文重新提交；空结果用 empty 或 insufficient 并写明原因",
        details={"field": name},
    )


# -- 任务查询 ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MaterialTurn:
    conversation_id: str
    turn_id: str
    scope: dict[str, object]
    started_at: str
    status: str
    published: int
    model: str = ""


def _material_turns(store: ConversationStore) -> tuple[MaterialTurn, ...]:
    rows: list[MaterialTurn] = []
    for conversation in store.conversations():
        events = store.events(conversation.id)
        for event in events:
            if event.get("type") != "turn_started":
                continue
            if event.get("purpose") != "organize_materials":
                continue
            scope = event.get("material_scope")
            if not isinstance(scope, dict):
                continue
            turn_id = str(event.get("turn_id") or "")
            rows.append(
                MaterialTurn(
                    conversation_id=conversation.id,
                    turn_id=turn_id,
                    scope=dict(scope),
                    started_at=str(event.get("at") or conversation.created_at),
                    status=_turn_status(events, turn_id),
                    published=_published_count(events, turn_id),
                    model=str(event.get("model") or ""),
                )
            )
    return tuple(sorted(rows, key=lambda item: item.started_at, reverse=True))


def _turn_status(events: Sequence[dict[str, object]], turn_id: str) -> str:
    status = ""
    running = False
    for event in events:
        if event.get("turn_id") != turn_id:
            continue
        kind = event.get("type")
        if kind == "run_finished":
            status = str(event.get("status") or "")
        elif kind in ("model_request_started", "tool_started"):
            running = True
        elif kind == "error":
            status = status or "failed"
    if status:
        return status
    return "running" if running else "accepted"


def _published_count(events: Sequence[dict[str, object]], turn_id: str) -> int:
    seen: set[tuple[str, str]] = set()
    for event in events:
        if event.get("type") != "material_target_published":
            continue
        if event.get("turn_id") != turn_id:
            continue
        seen.add((str(event.get("path") or ""), str(event.get("entity") or "")))
    return len(seen)


def material_conversation_ids(store: ConversationStore) -> set[str]:
    """资料任务专用会话：写作会话列表默认不列它们，但活动记录仍可回看。"""
    ids: set[str] = set()
    for conversation in store.conversations():
        if conversation.id in ids:
            continue
        for event in store.events(conversation.id):
            if event.get("type") == "turn_started" and event.get("origin") == "materials":
                ids.add(conversation.id)
                break
    return ids


def task_list(
    store: ConversationStore,
    root: Path,
    *,
    domain: str = "",
    limit: int = 20,
    active_turn_id: str = "",
) -> list[dict[str, object]]:
    """本域最近的资料任务；仍在运行的也要列出来，页面刷新后能接上。"""
    rows: list[dict[str, object]] = []
    for turn in _material_turns(store):
        if domain and str(turn.scope.get("domain")) != domain:
            continue
        rows_targets = targets(turn.scope)
        status = turn.status
        if (
            active_turn_id
            and turn.turn_id == active_turn_id
            and status
            in (
                "accepted",
                "running",
            )
        ):
            status = "running"
        rows.append(
            {
                "turn_id": turn.turn_id,
                "conversation_id": turn.conversation_id,
                "domain": turn.scope.get("domain"),
                "label": DOMAIN_LABEL.get(str(turn.scope.get("domain")), ""),
                "scope": _public_scope(turn.scope),
                "status": status,
                "model": turn.model,
                "started_at": turn.started_at,
                "published": turn.published,
                "done": turn.published,
                "total": len(rows_targets),
                "targets": rows_targets,
            }
        )
        if len(rows) >= max(1, limit):
            break
    return rows


def task_view(
    store: ConversationStore,
    root: Path,
    workspace: Workspace,
    turn_id: str,
    *,
    active_turn_id: str = "",
) -> dict[str, object]:
    """单个任务的规范化结果：目标、已发布、未完成项与覆盖结果。"""
    for turn in _material_turns(store):
        if turn.turn_id != turn_id:
            continue
        scope = turn.scope
        rows_targets = targets(scope)
        coverage = {
            (item.path, item.entity_path): item
            for item in CoverageStore(root).for_domain(
                cast(MaterialDomain, str(scope.get("domain")))
            )
            if item.task_id == turn_id
        }
        rows: list[dict[str, object]] = []
        for target in rows_targets:
            record = coverage.get((str(target["path"]), str(target["entity_path"])))
            rows.append(
                {
                    **target,
                    "outcome": record.outcome if record is not None else "unrecorded",
                    "freshness": (
                        _record_freshness(workspace, record) if record is not None else "none"
                    ),
                }
            )
        status = turn.status
        if (
            active_turn_id
            and turn.turn_id == active_turn_id
            and status
            in (
                "accepted",
                "running",
            )
        ):
            status = "running"
        done = max(
            sum(1 for row in rows if row["outcome"] != "unrecorded"),
            min(turn.published, len(rows)),
        )
        return {
            "turn_id": turn.turn_id,
            "conversation_id": turn.conversation_id,
            "domain": scope.get("domain"),
            "label": DOMAIN_LABEL.get(str(scope.get("domain")), ""),
            "scope": _public_scope(scope),
            "status": status,
            "model": turn.model,
            "started_at": turn.started_at,
            "published": turn.published,
            "done": done,
            "total": len(rows),
            "targets": rows,
            "unfinished": [row for row in rows if row["outcome"] == "unrecorded"],
        }
    raise WorkspaceError(
        code="MATERIAL_TASK_NOT_FOUND",
        where=f"material_tasks.view:{turn_id}",
        why="找不到这次资料任务",
        fix="刷新资料页的任务列表后再打开",
        details={"turn_id": turn_id},
    )


def _public_scope(scope: Mapping[str, object]) -> dict[str, object]:
    return {
        "domain": scope.get("domain"),
        "chapter_paths": list(cast(list[object], scope.get("chapter_paths") or [])),
        "entity_paths": list(cast(list[object], scope.get("entity_paths") or [])),
        "as_of_chapter": scope.get("as_of_chapter"),
    }


def _record_freshness(workspace: Workspace, record: CoverageRecord) -> str:
    try:
        current = workspace.read(record.path).digest
    except WorkspaceError:
        return "source_missing"
    return "valid" if current == record.digest else "stale"

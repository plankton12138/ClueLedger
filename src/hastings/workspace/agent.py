"""直接修改文件的写作 Agent（0.4 M03）。

一个 Agent，一组通用文件工具，没有规划者/写作者/审阅者的固定多角色流程。工具只在
文件层之上做两件事：把参数整理成可理解的错误，以及在第一次真正写文件之前通知记录层
准备 Git 基线——读操作永远不会触发提交。

工具成功只表示「已经写到磁盘」：``write_file`` / ``edit_file`` 返回的 ``digest`` 是
落盘后的实际指纹，模型可以据此继续改。指纹不匹配、路径不受管、文件不存在都按失败
返回给模型，由它重新读取，而不是由应用替它猜。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from hastings.library.bindings import AssemblyService
from hastings.library.changes import ChangeEdit, ChangeOperation, ModuleChangeService
from hastings.library.modules import ModuleLibrary
from hastings.library.records import EntryDraft
from hastings.workspace.assembly import AssembledContext, Exclusion, Profile
from hastings.workspace.chapter_index import (
    ChapterIndexDraft,
    ChapterIndexEntry,
    ChapterIndexStore,
    EntryKind,
    IndexEvidence,
    publish_index,
)
from hastings.workspace.character_states import (
    CharacterStateStore,
    StateSnapshot,
    build_roster,
    publish_states,
    roster_view,
)
from hastings.workspace.clues import (
    ClueEntry,
    ClueStore,
    build_clues,
    clues_view,
    publish_clues,
)
from hastings.workspace.context import (
    IDEA_INSTRUCTIONS,
    INDEX_INSTRUCTIONS,
    STATE_INSTRUCTIONS,
    build_assembled_context,
)
from hastings.workspace.disclosure import (
    DisclosureStore,
    ObservedEvidence,
    ObservedFact,
    build_disclosure_state,
    check_evidence,
    publish_observed,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, is_managed_path
from hastings.workspace.git import GitRepo
from hastings.workspace.glossary import (
    GlossaryStore,
    GlossaryTerm,
    build_glossary,
    glossary_view,
    publish_terms,
)
from hastings.workspace.ideas import IdeaStore
from hastings.workspace.material_tasks import publish_material_result
from hastings.workspace.materials import (
    LIST_RESULT_CHARS,
    READ_MAX_CHARS,
    READ_RESULT_CHARS,
    SEARCH_MAX_HITS,
    IdeaReference,
    MaterialLedger,
    ReadRequirement,
    Reference,
    SourceKind,
    SourceRef,
    bounded_search_hits,
    read_slice,
    require_author_quote,
    require_read_quote,
)
from hastings.workspace.module_assembly import Assembly
from hastings.workspace.questions import QuestionStore
from hastings.workspace.retrieval import MaterialQuery, catalog_brief, query_materials
from hastings.workspace.review import (
    REVIEW_INSTRUCTIONS,
    ReviewDraft,
    ReviewEvidence,
    ReviewFinding,
    ReviewRequest,
    ReviewStore,
    publish_report,
    target_digests,
)
from hastings.workspace.service import WriteSession
from hastings.workspace.story_materials import DOMAIN_LABEL, CoverageStore
from hastings.workspace.task_scope import (
    ModuleAction,
    ModuleChangeTarget,
    ModuleContentGrant,
    ModuleGrant,
    Mutation,
    MutationAction,
    ScopeController,
    ScopeDraft,
    ScopeMode,
)
from hastings.workspace.timeline import (
    TimelineEntry,
    TimelineStore,
    build_timeline,
    publish_timeline,
    timeline_view,
)
from tau_agent.harness import AgentHarness, AgentHarnessConfig
from tau_agent.messages import AgentMessage, TextContent
from tau_agent.provider import ModelProvider
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

__all__ = ["MAX_TURNS", "RunContext", "ToolMode", "build_harness", "build_tools"]

#: 工具集合：普通写作、索引任务、只读检查，或只读展开一条灵感。
ToolMode = Literal["write", "index", "review", "state", "materials", "idea", "overview"]


#: 单轮最多的模型回合数；到顶就结束并如实记录，不无限循环。
MAX_TURNS = 24

#: 单次工具输出上限，避免把整本书塞回模型。
_MAX_DIFF_CHARS = 20_000
_READ_DEFAULT_LINES = 200
_LIST_DEFAULT_ENTRIES = 200


@dataclass
class RunContext:
    """一轮写入的共享状态：文件工作区、材料记录、任务范围与记录会话。

    ``materials`` 记录本轮真正提供了哪些原文、依赖哪些版本；``scope`` 记录作者授权的
    确切路径与操作。所有作品变更都必须先经过 ``before_mutation``：范围、读取前提与依赖
    版本都通过后才准备 Git 基线，检查失败时不留任何文件副作用。
    """

    workspace: Workspace
    session: WriteSession
    materials: MaterialLedger = field(default_factory=MaterialLedger)
    scope: ScopeController = field(default_factory=ScopeController)
    #: 本轮可见的作者消息（事件序号 + 原文），用于核验授权引文。
    author_messages: tuple[tuple[int, str], ...] = ()
    #: 事件出口：材料与范围的记录写进会话事件；测试与只读场景可以不给。
    emit: Callable[[str, dict[str, Any]], None] | None = None
    #: 本轮轮次标识：报告要记到具体轮次上。
    turn_id: str = ""
    #: 作者在修改任务里明确要求的写后检查；为 None 表示没有要求。
    review_request: ReviewRequest | None = None
    #: 本轮状态时点；没有定位时不自动采用全书末章，查询需明确指定章号。
    state_chapter: int | None = None
    #: 受理时冻结的装配与库入口；检索工具只读这些精确版本。
    assembly: Assembly = field(default_factory=Assembly)
    library: ModuleLibrary | None = None
    #: 作者在对话里明确选中的灵感修订；只有这些条目允许读取正文。
    idea_references: tuple[IdeaReference, ...] = ()
    #: 本轮显式搜索灵感后看到的修订；只在当前轮有效，下一轮不继承。
    idea_visible: set[tuple[str, str]] = field(default_factory=set)
    #: 作品状态目录；检索观察与本作品投影都在这里。
    state_root: Path | None = None
    #: 当前受理轮次的作者原话；灵感许可不能从历史授权中继承。
    current_author_text: str | None = None
    #: 本轮的模块内容已经应用：只允许读取结果并给完成回复，不再继续写入。
    finalizing: bool = False
    #: 本轮所属会话：提问记录要能按会话找回。
    conversation_id: str = ""
    #: 受理时冻结的装配指纹：回答问题前核对目标版本是否仍是当时那一份。
    assembly_digest: str = ""
    #: 受理时冻结的材料身份；提问恢复时据此承接原请求。
    request_context: Mapping[str, Any] = field(default_factory=dict)
    #: 本轮已经向作者提问：写入被阻止，收尾状态为 needs_input。
    pending_question: dict[str, Any] | None = None

    def record(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if self.emit is not None:
            self.emit(event_type, dict(payload))

    def before_mutation(
        self,
        operation: Mutation,
        *,
        required_read: ReadRequirement | None = None,
        preflight: Callable[[], None] | None = None,
    ) -> None:
        """统一写前检查：范围 → 目标现状 → 读取前提 → 参考完整性 → 版本 → 准备 Git。

        ``preflight`` 是文件层的无副作用意图核对（指纹与目标存在性）；它在范围检查之后
        执行，因此越界操作先报「不被允许」，不会先被指纹错误掩盖。
        """
        self.require_writable(f"task_scope:{operation.path}")
        self.scope.require(operation)
        if preflight is not None:
            preflight()
        self.materials.require_target_read(self.workspace, required_read)
        self.materials.require_references_complete(self.workspace)
        self.materials.verify_current(self.workspace)
        if not self.session.prepared:
            self.session.prepare()
            self.record(
                "version_baseline",
                {
                    "baseline_commit": self.session.baseline_head,
                    "external_commit": self.session.external_commit,
                },
            )
        # Git 准备期间可能有外部程序改动文件：落盘前再复核一次依赖版本。
        self.materials.verify_current(self.workspace)

    def require_writable(self, where: str) -> None:
        """应用完成后本任务进入收尾；提问后只等作者回答：两种情况都不再继续写入。"""
        if self.pending_question is not None:
            raise WorkspaceError(
                code="TASK_AWAITING_INPUT_CONFLICT",
                where=where,
                why="本轮已经向作者提问，正在等待回答",
                fix="只读取现有内容并简短说明；作者回答后会在下一轮继续原请求",
            )
        if not self.finalizing:
            return
        raise WorkspaceError(
            code="TASK_FINALIZING_CONFLICT",
            where=where,
            why="本轮的模块内容已经应用，任务正在收尾",
            fix="只读取本次结果并给出完成回复；新的修改请由作者在下一轮提出",
        )


def build_tools(
    workspace: Workspace,
    run: RunContext,
    *,
    mode: ToolMode = "write",
    index_allowed: Sequence[str] = (),
    index_store: ChapterIndexStore | None = None,
    review_targets: Sequence[str] = (),
    review_store: ReviewStore | None = None,
    disclosure_store: DisclosureStore | None = None,
    glossary_store: GlossaryStore | None = None,
    roster_store: CharacterStateStore | None = None,
    timeline_store: TimelineStore | None = None,
    clue_store: ClueStore | None = None,
    material_scope: Mapping[str, object] | None = None,
    coverage_store: CoverageStore | None = None,
) -> list[AgentTool]:
    """本轮可用的文件工具；Git 提交与撤销不暴露给模型。

    索引任务只开放目录、搜索、读取与 ``save_chapter_index``：它能保存索引缓存，
    但不能改作品正文，也不准备 Git 基线。
    """
    if mode == "overview":
        # 该用途由统一轮次中的逐目标执行器构造受限工具，禁止意外得到写作工具。
        raise WorkspaceError(
            code="OVERVIEW_SCOPE_INVALID",
            where="总览整理",
            why="缺少冻结的总览目标",
            fix="从故事总览发起更新",
        )
    repo = GitRepo(workspace.root)

    async def list_files(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        offset = _int_argument(arguments, "offset", default=1, minimum=1)
        limit = _int_argument(arguments, "limit", default=_LIST_DEFAULT_ENTRIES, minimum=1)
        catalog = workspace.catalog()
        # 三块内容按「正文 → 固定设定 → 角色」排成一列再分页：返回结构不变，
        # 超长作品也能用 offset 继续看，不会一次把整本书的条目塞回模型。
        rows: list[tuple[str, dict[str, Any]]] = []
        for entry in catalog.chapters:
            rows.append(
                (
                    "chapters",
                    {
                        "path": entry.path,
                        "display_title": entry.display_title,
                        "title": entry.title,
                        "chapter_number": entry.chapter_number,
                        "chapter_type": entry.chapter_type,
                        "size": entry.size,
                    },
                )
            )
        for entry in catalog.settings:
            rows.append(
                (
                    "settings",
                    {"path": entry.path, "title": entry.title, "exists": entry.exists},
                )
            )
        for entry in catalog.characters:
            rows.append(
                (
                    "characters",
                    {
                        "path": entry.path,
                        "display_title": entry.display_title,
                        "title": entry.title,
                        "roles": list(entry.roles),
                        "size": entry.size,
                    },
                )
            )
        groups: dict[str, list[dict[str, Any]]] = {
            "chapters": [],
            "settings": [],
            "characters": [],
        }
        used = 0
        taken = 0
        for group, row in rows[offset - 1 : offset - 1 + limit]:
            size = len(json.dumps(row, ensure_ascii=False))
            if used + size > LIST_RESULT_CHARS:
                break
            groups[group].append(row)
            used += size
            taken += 1
        next_offset = offset + taken
        return _json(
            {
                "chapters": groups["chapters"],
                "settings": groups["settings"],
                "characters": groups["characters"],
                "offset": offset,
                "total_entries": len(rows),
                "next_offset": next_offset if next_offset <= len(rows) else None,
                "truncated": next_offset <= len(rows),
            }
        )

    async def read_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        offset = _int_argument(arguments, "offset", default=1, minimum=1)
        limit = _int_argument(arguments, "limit", default=_READ_DEFAULT_LINES, minimum=1)
        cursor = _optional_int_argument(arguments, "cursor", minimum=0)
        view = workspace.read(path)
        chunk = read_slice(
            view.text,
            cursor=cursor,
            offset=offset,
            limit=limit,
            budget=READ_MAX_CHARS,
        )
        # 只有真正返回给模型的区间才计入读取记录；UI 打开文件不走这里。
        run.materials.record_read(
            SourceRef(view.path, view.digest),
            length=len(view.text),
            start=chunk.start_char,
            end=chunk.end_char,
        )
        run.record(
            "material_read",
            {
                "path": view.path,
                "digest": view.digest,
                "start_char": chunk.start_char,
                "end_char": chunk.end_char,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "total_chars": len(view.text),
            },
        )
        payload: dict[str, Any] = {
            "path": view.path,
            "digest": view.digest,
            "total_lines": view.text.count("\n") + 1,
            "total_chars": len(view.text),
            "offset": chunk.start_line,
            "start_char": chunk.start_char,
            "end_char": chunk.end_char,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "next_cursor": chunk.next_cursor,
            "truncated": chunk.truncated,
            "text": chunk.text,
        }
        if len(json.dumps(payload, ensure_ascii=False)) > READ_RESULT_CHARS:
            # 原文本身受 READ_MAX_CHARS 约束；这里防的是极端的多字节内容把结果撑爆。
            raise WorkspaceError(
                code="TOOL_RESULT_TOO_LARGE",
                where=f"tools.read_file:{path}",
                why="这次读取的结果超过单次输出上限",
                fix="用更小的 limit 或更短的字符区间再读一次",
                details={"path": path},
            )
        return _json(payload)

    async def search_text(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        query = _text_argument(arguments, "query")
        limit = _int_argument(arguments, "limit", default=20, minimum=1)
        result = workspace.search(query, limit=min(limit, SEARCH_MAX_HITS))
        hits, truncated = bounded_search_hits(
            [(hit.path, hit.line, hit.text) for hit in result.hits]
        )
        return _json(
            {
                "hits": hits,
                "unreadable": list(result.unreadable),
                "truncated": truncated or result.truncated,
            }
        )

    async def request_review(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """记录作者在修改任务里明确要求的写后检查；没有要求就不调用。"""
        del tool_call_id, signal, on_update
        requirement = _text_argument(arguments, "requirement")
        authorization = _text_argument(arguments, "authorization")
        targets = _string_list_argument(arguments, "paths")
        require_author_quote(
            run.author_messages,
            authorization,
            _optional_int_argument(arguments, "seq", minimum=0),
            where="tools.request_review",
        )
        request = ReviewRequest(
            requirement=requirement, authorization=authorization, targets=targets
        )
        run.review_request = request
        run.record(
            "review_requested",
            {
                "requirement": request.requirement,
                "authorization": request.authorization,
                "targets": list(request.targets),
            },
        )
        return _json(
            {
                "requirement": request.requirement,
                "targets": list(request.targets),
                "note": "修改完成后会进入只读检查阶段",
            }
        )

    async def save_review_report(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """发布检查报告：目标版本、覆盖与证据由检查模块统一核对。"""
        del tool_call_id, signal, on_update
        if review_store is None:
            raise _input_error("review_store", "本轮没有开启检查保存")
        targets = _string_list_argument(arguments, "target_paths")
        status = _text_argument(arguments, "status")
        if status not in ("findings", "no_findings", "incomplete"):
            raise _input_error("status", "只能是 findings、no_findings 或 incomplete")
        draft = ReviewDraft(
            target_paths=targets,
            target_digests=target_digests(workspace, targets),
            requirement=_text_argument(arguments, "requirement"),
            status=cast(Any, status),
            findings=_review_findings(arguments.get("findings")),
            uncovered=_string_list_argument(arguments, "uncovered"),
        )
        report = publish_report(
            draft,
            workspace=workspace,
            ledger=run.materials,
            store=review_store,
            turn_id=run.turn_id,
            allowed_targets=review_targets,
        )
        run.record(
            "review_finished",
            {
                "report_id": report.report_id,
                "status": report.status,
                "targets": list(report.target_paths),
                "findings": [item.to_dict() for item in report.findings],
                "uncovered": list(report.uncovered),
            },
        )
        return _json(
            {
                "report_id": report.report_id,
                "status": report.status,
                "findings": len(report.findings),
                "uncovered": list(report.uncovered),
            }
        )

    async def save_chapter_index(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """发布一章索引；范围、全文覆盖与引文匹配由索引模块统一核对。"""
        del tool_call_id, signal, on_update
        if index_store is None:
            raise _input_error("index_store", "本轮没有开启索引保存")
        path = _text_argument(arguments, "path")
        digest = _required_digest(arguments)
        draft = ChapterIndexDraft(
            source_path=path,
            source_digest=digest,
            summary=_text_argument(arguments, "summary"),
            evidence=_index_evidence(arguments.get("evidence")),
            events=_index_entries(arguments.get("events"), "event"),
            characters=_index_entries(arguments.get("characters"), "character"),
            places=_index_entries(arguments.get("places"), "place"),
            clues=_index_entries(arguments.get("clues"), "clue"),
        )
        record = publish_index(
            draft,
            workspace=workspace,
            ledger=run.materials,
            store=index_store,
            allowed_paths=index_allowed,
        )
        run.record(
            "chapter_index_updated",
            {
                "path": record.source_path,
                "source_digest": record.source_digest,
                "recipe_version": record.recipe_version,
                "entries": len(record.entries()),
                "status": "valid",
            },
        )
        return _json(
            {
                "path": record.source_path,
                "digest": record.source_digest,
                "summary": record.summary,
                "entries": len(record.entries()),
                "status": record.status,
            }
        )

    async def register_references(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """登记作者明确要求参考的文件，以及任务中发现的补充材料。

        作者文本来源必须带会话事件定位与原文短引文；发现的材料必须已经真正读过，
        并用文件里的原文说明为什么相关。客户端选择的参考由应用登记，不走这里。
        """
        del tool_call_id, signal, on_update
        raw = arguments.get("references")
        if not isinstance(raw, list) or not raw:
            raise _input_error("references", "必须是非空数组")
        registered: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                raise _input_error("references", "每一项必须是对象")
            path = item.get("path")
            kind = item.get("kind")
            quote = item.get("quote", "")
            seq = item.get("seq")
            required = bool(item.get("required", False))
            if not isinstance(path, str) or not path:
                raise _input_error("references", "每一项都要有非空 path")
            if kind not in ("author_text", "discovered"):
                raise WorkspaceError(
                    code="REFERENCE_KIND_INVALID",
                    where=f"tools.register_references:{path}",
                    why=f"工具只能登记作者文本或发现的材料，不能登记来源类别 {kind}",
                    fix="作者选择的文件由应用登记；这里用 author_text 或 discovered",
                    details={"kind": str(kind)},
                )
            if not isinstance(quote, str):
                raise _input_error("references", "quote 必须是字符串")
            if seq is not None and (isinstance(seq, bool) or not isinstance(seq, int)):
                raise _input_error("references", "seq 必须是整数")
            try:
                view = workspace.read(path)
            except WorkspaceError as exc:
                raise WorkspaceError(
                    code="REFERENCE_UNRESOLVED_CONFLICT",
                    where=f"tools.register_references:{path}",
                    why=f"要登记的材料当前不可用：{path}（{exc.why}）",
                    fix="先用 list_files 确认路径，再读取或登记",
                    details={"path": path},
                ) from exc
            if kind == "author_text":
                require_author_quote(
                    run.author_messages,
                    quote,
                    seq,
                    where=f"tools.register_references:{path}",
                )
            else:
                if not quote.strip():
                    raise WorkspaceError(
                        code="REFERENCE_EVIDENCE_INVALID",
                        where=f"tools.register_references:{path}",
                        why="发现的材料需要一段实际读到的原文作为关联证据",
                        fix="先 read_file 读取该文件，再引用其中一句说明为什么相关",
                        details={"path": path},
                    )
                require_read_quote(
                    workspace,
                    run.materials,
                    path=path,
                    quote=quote,
                    where=f"tools.register_references:{path}",
                )
            reference = run.materials.register(
                Reference(
                    ref=SourceRef(view.path, view.digest),
                    kind=cast(SourceKind, kind),
                    required=required,
                    quote=quote.strip(),
                    seq=seq,
                )
            )
            registered.append(
                {
                    "path": reference.path,
                    "digest": reference.ref.digest,
                    "kind": reference.kind,
                    "required": reference.required,
                }
            )
        run.record("reference_registered", {"references": registered})
        return _json(
            {
                "references": registered,
                "required": [row["path"] for row in registered if row["required"]],
            }
        )

    async def set_task_scope(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        mode = _text_argument(arguments, "mode")
        if mode not in ("read_only", "edit"):
            raise _input_error("mode", "只能是 read_only 或 edit")
        draft = ScopeDraft(
            mode=cast(ScopeMode, mode),
            grants=_mutation_arguments(arguments.get("grants")),
            denied_paths=_string_list_argument(arguments, "denied_paths"),
            summary=_text_argument(arguments, "summary"),
            authorization=_text_argument(arguments, "authorization"),
            authorization_seq=_optional_int_argument(arguments, "authorization_seq", minimum=0),
            carried_from_seq=_optional_int_argument(arguments, "carried_from_seq", minimum=0),
            required_references=_string_list_argument(arguments, "required_references"),
            library_search=bool(arguments.get("library_search", False)),
            module_grants=_module_grant_arguments(arguments.get("module_grants")),
            module_content_grants=_module_content_grant_arguments(
                arguments.get("module_content_grants")
            ),
            library_publish=bool(arguments.get("library_publish", False)),
            extendable=bool(arguments.get("extendable", False)),
            extend_actions=_action_list_argument(arguments, "extend_actions"),
            note=_optional_text_argument(arguments, "note"),
        )
        scope = run.scope.set_scope(draft, author_texts=run.author_messages)
        # 范围是这一轮的实际权限记录：写进事件，界面与回读都按同一份内容展示。
        run.record("task_scope_set", scope.to_dict())
        return _json(scope.to_dict())

    async def extend_task_scope(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        evidence_path = _text_argument(arguments, "evidence_path")
        evidence = _text_argument(arguments, "evidence")
        # 依据必须来自本轮真正提供给模型的原文：先读、后引，且引文在已提供的区间里。
        require_read_quote(
            workspace,
            run.materials,
            path=evidence_path,
            quote=evidence,
            where=f"tools.extend_task_scope:{evidence_path}",
        )
        scope = run.scope.extend(
            version=_int_argument(arguments, "version", default=0, minimum=0),
            grants=_mutation_arguments(arguments.get("grants")),
            evidence_path=evidence_path,
            evidence=evidence,
            author_texts=run.author_messages,
        )
        run.record("task_scope_extended", scope.to_dict())
        return _json(scope.to_dict())

    async def write_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del signal, on_update
        path = _text_argument(arguments, "path")
        text = _text_argument(arguments, "text", allow_empty=True)
        digest = _digest_argument(arguments)
        action: MutationAction = "update" if digest is not None else "create"
        # 存在性与提交意图冲突时由文件层拒绝；这里只按实际存在性给出读取前提。
        requirement = ReadRequirement.full(path) if action == "update" else None
        run.before_mutation(
            Mutation(action=action, path=path),
            required_read=requirement,
            # 无副作用预检：意图与现状冲突时不能先准备 Git 基线再报错。
            preflight=lambda: workspace.check_write_intent(path, expected_digest=digest),
        )
        view = workspace.write(path, text, expected_digest=digest)
        run.record(
            "file_changed",
            {
                "tool_call_id": tool_call_id,
                "action": action,
                "path": view.path,
                "before_digest": digest,
                "after_digest": view.digest,
                "changed": digest != view.digest,
                "size": view.size,
            },
        )
        run.materials.after_full_write(view)
        return _json({"path": view.path, "digest": view.digest, "size": view.size})

    async def edit_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del signal, on_update
        path = _text_argument(arguments, "path")
        old_text = _text_argument(arguments, "old_text")
        new_text = _text_argument(arguments, "new_text", allow_empty=True)
        digest = _digest_argument(arguments)
        view = workspace.read(path)
        occurrences = view.text.count(old_text)
        if occurrences == 0:
            raise WorkspaceError(
                code="EDIT_MATCH_NOT_FOUND",
                where=f"tools.edit_file:{path}",
                why="要替换的原文没有出现在当前文件里",
                fix="重新 read_file 确认当前内容，再复制一段完全一致的原文",
                details={"path": path},
            )
        if occurrences > 1:
            raise WorkspaceError(
                code="EDIT_MATCH_AMBIGUOUS",
                where=f"tools.edit_file:{path}",
                why=f"要替换的原文出现了 {occurrences} 次，无法确定改哪一处",
                fix="多带一些上下文让原文唯一，或改用 write_file 重写整章",
                details={"path": path, "occurrences": occurrences},
            )
        start = view.text.find(old_text)
        end = start + len(old_text)
        # 局部替换的前提是被替换区间已提供，不是「整章读过」。
        run.before_mutation(
            Mutation(action="update", path=path),
            required_read=ReadRequirement.span(path, start, end),
        )
        updated = view.text.replace(old_text, new_text, 1)
        written = workspace.write(path, updated, expected_digest=digest)
        run.record(
            "file_changed",
            {
                "tool_call_id": tool_call_id,
                "action": "update",
                "path": written.path,
                "before_digest": view.digest,
                "after_digest": written.digest,
                "changed": view.digest != written.digest,
                "start": start,
                "end": end,
                "size": written.size,
            },
        )
        run.materials.after_edit(
            before=SourceRef(view.path, view.digest), view=written, start=start, end=end
        )
        return _json({"path": written.path, "digest": written.digest, "size": written.size})

    async def move_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del signal, on_update
        source = _text_argument(arguments, "source")
        target = _text_argument(arguments, "target")
        digest = _required_digest(arguments)
        run.before_mutation(
            Mutation(action="move", path=source, target=target),
            required_read=ReadRequirement.identity(source),
            preflight=lambda: workspace.check_move_intent(source, target, expected_digest=digest),
        )
        view = workspace.move(source, target, expected_digest=digest)
        run.record(
            "file_changed",
            {
                "tool_call_id": tool_call_id,
                "action": "move",
                "path": source,
                "target": target,
                "before_digest": digest,
                "after_digest": view.digest,
                "changed": True,
            },
        )
        run.materials.after_move(SourceRef(source, digest), view)
        return _json({"path": view.path, "digest": view.digest})

    async def delete_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del signal, on_update
        path = _text_argument(arguments, "path")
        digest = _required_digest(arguments)
        run.before_mutation(
            Mutation(action="delete", path=path),
            required_read=ReadRequirement.identity(path),
            preflight=lambda: workspace.check_delete_intent(path, expected_digest=digest),
        )
        workspace.delete(path, expected_digest=digest)
        run.record(
            "file_changed",
            {
                "tool_call_id": tool_call_id,
                "action": "delete",
                "path": path,
                "before_digest": digest,
                "after_digest": None,
                "changed": True,
            },
        )
        run.materials.after_delete(SourceRef(path, digest))
        return _json({"path": path, "deleted": True})

    async def git_diff(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, arguments, signal, on_update
        diff = repo.diff()
        if len(diff) > _MAX_DIFF_CHARS:
            diff = diff[:_MAX_DIFF_CHARS] + "\n…（差异过长，已截断）"
        return _json({"diff": diff})

    async def git_log(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        limit = min(_int_argument(arguments, "limit", default=10, minimum=1), 50)
        entries = repo.log(limit=limit)
        commits: list[dict[str, Any]] = []
        used = 0
        for commit in entries:
            row = {
                "commit": commit.sha,
                "at": commit.committed_at,
                "subject": commit.subject,
            }
            size = len(json.dumps(row, ensure_ascii=False))
            if used + size > LIST_RESULT_CHARS:
                break
            commits.append(row)
            used += size
        return _json({"commits": commits, "truncated": len(commits) < len(entries)})

    tools = [
        AgentTool(
            name="list_files",
            label="文件目录",
            description=(
                "列出正文章节、角色小传以及线索与真相文件的路径和标题（只读）。"
                "条目很多时用 offset 继续看其余部分。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "offset": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "从第几条开始，默认 1",
                    },
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "最多返回多少条，默认 200",
                    },
                },
                "additionalProperties": False,
            },
            execute_fn=list_files,
            prompt_guidelines=("不确定有哪些文件时先调用；无参数。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="read_file",
            label="读取文件",
            description=(
                "读取受管文件的文本，返回内容、实际字符区间、行号、总行数与 digest。"
                "digest 是后续写入的凭据。长文用 offset/limit 按行分页，或带上一次返回的 "
                "next_cursor 按字符继续；单次最多 16000 字符。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "如 正文/0001 雨夜.md"},
                    "offset": {"type": "integer", "minimum": 1, "description": "起始行号，默认 1"},
                    "limit": {"type": "integer", "minimum": 1, "description": "读取行数，默认 200"},
                    "cursor": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "字符游标：上次返回的 next_cursor，用于继续读同一位置之后",
                    },
                },
                "required": ["path"],
                "additionalProperties": False,
            },
            execute_fn=read_file,
            prompt_guidelines=(
                "改写前必须读；读完保留返回的 digest 供写入使用。",
                "truncated 为真时按 next_cursor 继续读，不要假设已看完全文。",
            ),
            execution_mode="sequential",
        ),
        AgentTool(
            name="search_text",
            label="搜索文本",
            description="在正文与设定中逐行搜索关键词，返回路径、行号与摘录（只读）。",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "要查找的文字"},
                    "limit": {
                        "type": "integer",
                        "minimum": 1,
                        "description": "最多条数，默认 20，上限 50",
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
            execute_fn=search_text,
            prompt_guidelines=("找伏笔、人物首次出现等具体内容时使用。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="register_references",
            label="登记参考",
            description=(
                "登记作者在消息里明确要求参考的文件，或任务中发现的相关材料。"
                "作者文本来源要带作者原话短引文；发现的材料要先读过并引用其中一句原文。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "references": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "kind": {
                                    "type": "string",
                                    "enum": ["author_text", "discovered"],
                                },
                                "required": {
                                    "type": "boolean",
                                    "description": "写作品前是否必须读完；作者明确说「参考」时为真",
                                },
                                "quote": {
                                    "type": "string",
                                    "description": "作者原话短引文，或文件里的一句原文",
                                },
                                "seq": {
                                    "type": "integer",
                                    "minimum": 0,
                                    "description": "可选：作者消息所在事件的 seq",
                                },
                            },
                            "required": ["path", "kind"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["references"],
                "additionalProperties": False,
            },
            execute_fn=register_references,
            prompt_guidelines=(
                "作者说「参考第一章」时登记它，并设 required；只是自己搜到的线索不必设 required。",
                "客户端已选的参考由应用登记，不要重复登记。",
            ),
            execution_mode="sequential",
        ),
        AgentTool(
            name="set_task_scope",
            label="登记修改范围",
            description=(
                "登记这一轮要做什么、作者在哪句话里授权、允许改哪些确切文件。"
                "没有登记范围的写工具会被拒绝；纯讨论不必登记。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "mode": {
                        "type": "string",
                        "enum": ["read_only", "edit"],
                        "description": "read_only 只读；edit 允许按 grants 修改",
                    },
                    "summary": {"type": "string", "description": "一句话说明本轮任务理解"},
                    "authorization": {
                        "type": "string",
                        "description": "作者原话里的短引文，必须与作者消息一致",
                    },
                    "authorization_seq": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "可选：授权那句话所在事件的 seq",
                    },
                    "carried_from_seq": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "可选：说「继续」时，被承接的作者请求所在事件的 seq",
                    },
                    "grants": {
                        "type": "array",
                        "description": "允许的操作与确切路径；移动另带 target",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["create", "update", "delete", "move"],
                                },
                                "path": {"type": "string"},
                                "target": {"type": "string"},
                            },
                            "required": ["action", "path"],
                            "additionalProperties": False,
                        },
                    },
                    "denied_paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "明确不许改的路径，例如「不动人物设定」时的小传",
                    },
                    "required_references": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "写作品前必须读完的参考文件",
                    },
                    "library_search": {
                        "type": "boolean",
                        "description": "作者明确要求从全局模块库查找时才设 true；只开搜索",
                    },
                    "module_grants": {
                        "type": "array",
                        "description": (
                            "作者明确要求装卸时才登记；装入给模块与精确版本，卸下给绑定 ID"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["attach", "detach"]},
                                "module_id": {"type": "string"},
                                "revision_id": {"type": "string"},
                                "binding_id": {"type": "string"},
                            },
                            "required": ["action"],
                            "additionalProperties": False,
                        },
                    },
                    "module_content_grants": {
                        "type": "array",
                        "description": (
                            "本轮要保存到当前作品的内容操作：新建用稳定槽位，"
                            "修改用绑定、模块与基础版本"
                        ),
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {"type": "string", "enum": ["create", "update"]},
                                "slot": {
                                    "type": "string",
                                    "description": "新建：本任务稳定的槽位，与 operation_id 一致",
                                },
                                "purpose": {"type": "string", "description": "这次内容用来做什么"},
                                "binding_id": {"type": "string"},
                                "module_id": {"type": "string"},
                                "base_revision": {"type": "string"},
                            },
                            "required": ["action"],
                            "additionalProperties": False,
                        },
                    },
                    "library_publish": {
                        "type": "boolean",
                        "description": "作者明确要求把内容发布到全局模块库时才设 true",
                    },
                    "extendable": {
                        "type": "boolean",
                        "description": "任务允许发现相关文件后追加范围（例如「同步相关章节」）",
                    },
                    "extend_actions": {
                        "type": "array",
                        "items": {
                            "type": "string",
                            "enum": ["create", "update", "delete", "move"],
                        },
                        "description": "可追加范围时允许的操作类别",
                    },
                    "note": {"type": "string", "description": "可选说明"},
                },
                "required": ["mode", "summary", "authorization"],
                "additionalProperties": False,
            },
            execute_fn=set_task_scope,
            prompt_guidelines=(
                "要改文件时先登记；作者没明确要求修改时用 read_only 或干脆不登记。",
                "grants 只写确切路径，不要写目录或通配符。",
            ),
            execution_mode="sequential",
        ),
        AgentTool(
            name="extend_task_scope",
            label="追加修改范围",
            description=(
                "在可发现范围内追加与原目标有关的具体文件；必须给出这句原文来自哪份文件、"
                "原文本身，并带上当前范围版本。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "version": {"type": "integer", "minimum": 0, "description": "当前范围版本"},
                    "grants": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "action": {
                                    "type": "string",
                                    "enum": ["create", "update", "delete", "move"],
                                },
                                "path": {"type": "string"},
                                "target": {"type": "string"},
                            },
                            "required": ["action", "path"],
                            "additionalProperties": False,
                        },
                    },
                    "evidence_path": {
                        "type": "string",
                        "description": "这句原文来自哪份文件：必须本轮已读过",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "为什么这个文件属于本次任务：引用实际读到的原文",
                    },
                },
                "required": ["version", "grants", "evidence_path", "evidence"],
                "additionalProperties": False,
            },
            execute_fn=extend_task_scope,
            prompt_guidelines=("只在登记时标记了 extendable 的任务里使用；固定范围会被拒绝。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="request_review",
            label="要求写后检查",
            description=(
                "作者明确说「改完检查」时登记检查要求；修改完成后会进入只读检查阶段。"
                "没有这句话就不要调用。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "requirement": {"type": "string", "description": "要检查什么"},
                    "authorization": {
                        "type": "string",
                        "description": "作者原话里的短引文，必须与作者消息一致",
                    },
                    "seq": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "可选：那句话所在事件的 seq",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可选：要检查的文件；不填按本轮修改的目标推断",
                    },
                },
                "required": ["requirement", "authorization"],
                "additionalProperties": False,
            },
            execute_fn=request_review,
            prompt_guidelines=("只在作者明确要求检查时使用；普通修改不隐含开启检查。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="write_file",
            label="写入文件",
            description=(
                "整体写入一个受管文件。新建文件不带 digest；改写已有文件必须带 read_file "
                "得到的 digest。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "text": {"type": "string", "description": "完整的新内容（UTF-8 文本）"},
                    "digest": {"type": "string", "description": "改写已有文件时必填"},
                },
                "required": ["path", "text"],
                "additionalProperties": False,
            },
            execute_fn=write_file,
            prompt_guidelines=("适合新建章节或整章重写；局部修改优先 edit_file。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="edit_file",
            label="局部修改",
            description=(
                "用唯一匹配的原文替换完成局部修改，返回新的 digest。原文出现 0 次或多次都会失败。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_text": {"type": "string", "description": "要被替换的原文，必须唯一"},
                    "new_text": {"type": "string", "description": "替换后的文字"},
                    "digest": {"type": "string", "description": "read_file 得到的 digest"},
                },
                "required": ["path", "old_text", "new_text", "digest"],
                "additionalProperties": False,
            },
            execute_fn=edit_file,
            prompt_guidelines=("改一句话、补一段时使用；不要为小改动重写整章。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="move_file",
            label="重命名文件",
            description="在同类目录内重命名章节或角色小传（线索、真相不可移动），需要 digest。",
            parameters={
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "target": {"type": "string"},
                    "digest": {"type": "string"},
                },
                "required": ["source", "target", "digest"],
                "additionalProperties": False,
            },
            execute_fn=move_file,
            execution_mode="sequential",
        ),
        AgentTool(
            name="delete_file",
            label="删除文件",
            description="删除章节或单个角色小传（线索、真相不可删除），需要 digest。",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "digest": {"type": "string"}},
                "required": ["path", "digest"],
                "additionalProperties": False,
            },
            execute_fn=delete_file,
            execution_mode="sequential",
        ),
        AgentTool(
            name="git_diff",
            label="查看差异",
            description="查看工作区相对上次提交的差异（只读）。",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=git_diff,
            prompt_guidelines=("确认自己改了什么时使用；新文件不出现在工作区差异里。",),
            execution_mode="sequential",
        ),
        AgentTool(
            name="git_log",
            label="查看历史",
            description="查看最近的提交标题与时间（只读）。",
            parameters={
                "type": "object",
                "properties": {"limit": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
            execute_fn=git_log,
            execution_mode="sequential",
        ),
    ]

    async def read_state(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        """状态层查询：``domain`` 选域；``entity`` 只在身份与人物状态域里用。"""
        del tool_call_id, signal, on_update
        chapter = run.state_chapter if mode == "write" else None
        if arguments.get("as_of_chapter") is not None:
            requested = _int_argument(arguments, "as_of_chapter", default=0, minimum=0)
            chapter = min(chapter, requested) if chapter is not None else requested
        if mode == "write" and chapter is None:
            chapter = 0
        at = {"as_of_chapter": chapter}
        domain = arguments.get("domain") or "disclosure"
        if domain == "roster":
            return _json(roster_view(build_roster(workspace, roster_store, **at)))
        if domain == "timeline":
            return _json(timeline_view(build_timeline(workspace, timeline_store, **at)))
        if domain == "clues":
            return _json(clues_view(build_clues(workspace, clue_store, **at)))
        if domain == "glossary":
            return _json(glossary_view(build_glossary(workspace, glossary_store, **at)))
        if domain != "disclosure":
            raise _input_error("domain", "只能是 disclosure / roster / timeline / clues / glossary")
        snapshot = build_disclosure_state(workspace, disclosure_store, **at)
        entity_path = arguments.get("entity")
        if entity_path is None:
            return _json(
                {
                    "current_chapter": snapshot.current_chapter,
                    "entities": [
                        {
                            "path": entity.path,
                            "name": entity.name,
                            "surface": entity.surface,
                            "revealed": [
                                {"fact": item.fact, "chapter": item.chapter, "source": item.source}
                                for item in entity.revealed
                            ],
                            "planned": [
                                {"fact": item.fact, "chapter": item.chapter}
                                for item in entity.planned
                                if mode != "write"
                            ],
                        }
                        for entity in snapshot.entities
                    ],
                    "conflicts": [
                        {"name": conflict.name, "kind": conflict.kind, "detail": conflict.detail}
                        for conflict in snapshot.conflicts
                        if mode != "write"
                    ],
                }
            )
        if not isinstance(entity_path, str) or not entity_path:
            raise _input_error("entity", "必须是小传路径")
        entity = next((item for item in snapshot.entities if item.path == entity_path), None)
        if entity is None:
            raise WorkspaceError(
                code="STATE_NOT_FOUND",
                where=f"state.read:{entity_path}",
                why="没有这个角色的身份状态",
                fix="先用不带 entity 的形式看有哪些角色",
                details={"path": entity_path},
            )
        entries = disclosure_store.entries() if disclosure_store is not None else ()
        return _json(
            {
                "path": entity.path,
                "name": entity.name,
                "surface": entity.surface,
                "aliases": list(entity.aliases),
                "revealed": [
                    {"fact": item.fact, "chapter": item.chapter, "source": item.source}
                    for item in entity.revealed
                ],
                "upcoming": [
                    {"fact": item.fact, "chapter": item.chapter}
                    for item in entity.upcoming
                    if mode != "write"
                ],
                "planned": [
                    {"fact": item.fact, "chapter": item.chapter}
                    for item in entity.planned
                    if mode != "write"
                ],
                "record_status": entity.record_status,
                "evidence": [
                    {
                        "fact": item.fact,
                        "path": item.evidence.path,
                        "start": item.evidence.start,
                        "end": item.evidence.end,
                        "quote": item.evidence.quote,
                    }
                    for item in entries
                    if item.entity_path == entity.path
                    and item.chapter <= snapshot.current_chapter
                    and disclosure_store is not None
                    and disclosure_store.status(workspace, item) == "valid"
                ],
            }
        )

    tools.append(
        AgentTool(
            name="read_state",
            label="查看状态",
            description=(
                "查看状态层：domain=disclosure（默认）给人物身份，domain=roster 给位置、生死与"
                "立场，domain=timeline 给故事时间，domain=clues 给线索台账，domain=glossary 给"
                "专名。带 entity 可查某个角色的条目与原文依据。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "enum": ["disclosure", "roster", "timeline", "clues", "glossary"],
                    },
                    "entity": {"type": "string", "description": "如 设定集/角色小传/林静.md"},
                    "as_of_chapter": {
                        "type": "integer",
                        "minimum": 0,
                        "description": "本轮所写章号；不会超过已固定的任务时点",
                    },
                },
                "additionalProperties": False,
            },
            execute_fn=read_state,
            execution_mode="sequential",
        )
    )

    if run.library is not None and mode in ("write", "review", "materials"):

        async def list_modules(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            limit = _int_argument(arguments, "limit", default=50, minimum=1)
            offset = _int_argument(arguments, "offset", default=0, minimum=0)
            brief = catalog_brief(workspace, run.assembly, run.library, limit=limit)
            bindings = list(cast(list[dict[str, object]], brief["bindings"]))
            page = bindings[offset : offset + limit]
            next_offset = offset + len(page)
            return _json(
                {
                    "current_chapter": brief["current_chapter"],
                    "modules": page,
                    "total": len(bindings),
                    "next_cursor": str(next_offset) if next_offset < len(bindings) else "",
                    "warnings": brief["warnings"],
                }
            )

        async def query_records(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            if run.state_root is None:
                raise WorkspaceError(
                    code="RETRIEVAL_UNAVAILABLE",
                    where="tools.query_records",
                    why="这一轮没有作品状态目录，无法读取观察",
                    fix="在正常写作轮次里使用检索；不要用它查临时文件",
                )
            include_plans = arguments.get("include_plans", True)
            if not isinstance(include_plans, bool):
                raise _input_error("include_plans", "必须是布尔值")
            if mode == "materials":
                # 整理任务只能核对正文观察，不能把作者计划当成已发生的事实。
                include_plans = False
            query = MaterialQuery(
                kinds=_string_list_argument(arguments, "kinds"),
                module_ids=_string_list_argument(arguments, "module_ids"),
                text=_optional_text_argument(arguments, "query"),
                entity_ids=_string_list_argument(arguments, "entity_ids"),
                chapter_paths=_string_list_argument(arguments, "chapter_paths"),
                as_of_chapter=run.state_chapter,
                include_plans=include_plans,
                cursor=_optional_text_argument(arguments, "cursor"),
                limit=_int_argument(arguments, "limit", default=20, minimum=1),
            )
            view = query_materials(workspace, run.state_root, run.assembly, run.library, query)
            run.record(
                "records_queried",
                {
                    "query": {
                        "text": query.text,
                        "module_ids": list(query.module_ids),
                        "kinds": list(query.kinds),
                    },
                    "metrics": view["metrics"],
                },
            )
            return _json(view)

        async def read_record(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.read_record",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再读取；不要用文件工具猜库路径",
                )
            module_id = _text_argument(arguments, "module_id")
            revision_id = _text_argument(arguments, "revision_id")
            entry_id = _text_argument(arguments, "entry_id")
            binding = next(
                (
                    item
                    for item in run.assembly.bindings
                    if item.module.module_id == module_id and item.module.revision_id == revision_id
                ),
                None,
            )
            if binding is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where=f"tools.read_record:{module_id}@{revision_id}",
                    why="这个模块版本不在本轮装配里",
                    fix="先用 list_modules 或 query_records 拿本轮可用的完整引用",
                    details={"module_id": module_id, "revision_id": revision_id},
                )
            detail = run.library.read_entry(
                module_id=module_id,
                revision_id=revision_id,
                entry_id=entry_id,
                start=_optional_int_argument(arguments, "start", minimum=0) or 0,
                end=_optional_int_argument(arguments, "end", minimum=0),
                ledger=run.materials,
            )
            detail["module_ref"] = [
                binding.module.library_id,
                module_id,
                revision_id,
                entry_id,
            ]
            detail["binding_id"] = binding.id
            run.record(
                "record_read",
                {
                    "module_ref": detail["module_ref"],
                    "entry_id": entry_id,
                    "start": detail["start"],
                    "end": detail["end"],
                    "length": detail["length"],
                    "digest": detail["digest"],
                },
            )
            if len(json.dumps(detail, ensure_ascii=False)) > READ_RESULT_CHARS:
                raise WorkspaceError(
                    code="TOOL_RESULT_TOO_LARGE",
                    where=f"tools.read_record:{entry_id}",
                    why="这次读取的结果超过单次输出上限",
                    fix="用更小的 start/end 区间再读一次",
                    details={"entry_id": entry_id},
                )
            return _json(detail)

        async def search_module_library(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            scope = run.scope.current
            if scope is None or not scope.draft.library_search:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.search_module_library",
                    why="作者还没有明确要求从全局模块库查找",
                    fix="先按作者原话登记 library_search；搜索不等于装入或改库",
                )
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.search_module_library",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再搜索",
                )
            kind = _optional_text_argument(arguments, "kind")
            text = _optional_text_argument(arguments, "query")
            page = run.library.modules(
                kind=kind or None,
                q=text,
                cursor=_optional_text_argument(arguments, "cursor") or None,
                limit=_int_argument(arguments, "limit", default=20, minimum=1),
                include_deleted=False,
            )
            run.record(
                "module_library_searched",
                {
                    "query": text,
                    "kind": kind,
                    "total": int(str(page.get("total") or 0)),
                    "returned": len(cast(list[object], page.get("items") or [])),
                },
            )
            return _json(page)

        async def read_library_record(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            """作者明确要求时，单轮只读一个未装版本；不装入、不开放其他模块。"""
            del tool_call_id, signal, on_update
            scope = run.scope.current
            if scope is None or not scope.draft.library_search:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.read_library_record",
                    why="作者还没有明确要求引用模块库里的内容",
                    fix="先按作者原话登记 library_search；单轮引用不等于装入",
                )
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.read_library_record",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再读取",
                )
            module_id = _text_argument(arguments, "module_id")
            revision_text = _optional_text_argument(arguments, "revision_id")
            revision_id = revision_text or ""
            if not revision_id:
                try:
                    head = run.library.module(module_id).head
                except WorkspaceError as exc:
                    raise WorkspaceError(
                        code="MODULE_ACCESS_DENIED",
                        where="tools.read_library_record",
                        why=f"模块不可用：{exc.why}",
                        fix="先用 search_module_library 取得存在的模块与版本",
                        details={"module_id": module_id},
                    ) from exc
                if not head:
                    raise WorkspaceError(
                        code="MODULE_ACCESS_DENIED",
                        where="tools.read_library_record",
                        why="这个模块还没有可读的版本",
                        fix="先发布一个版本，或选择其他模块",
                        details={"module_id": module_id},
                    )
                revision_id = head
            revision = run.library.revision(module_id=module_id, revision_id=revision_id)
            entry_id = _optional_text_argument(arguments, "entry_id")
            if not entry_id:
                return _json(
                    {
                        "library_id": run.library.library_id,
                        "module_id": module_id,
                        "revision_id": revision_id,
                        "kind": revision.module_kind,
                        "name": revision.module_name,
                        "entries": [
                            {"entry_id": item.entry_id, "kind": item.kind, "title": item.title}
                            for item in revision.entries
                        ],
                        "note": "这里只是目录；正文用 entry_id 再读一次。本轮不自动装入。",
                    }
                )
            detail = run.library.read_entry(
                module_id=module_id,
                revision_id=revision_id,
                entry_id=entry_id,
                start=_optional_int_argument(arguments, "start", minimum=0) or 0,
                end=_optional_int_argument(arguments, "end", minimum=0),
                ledger=run.materials,
            )
            ref = [run.library.library_id or "", module_id, revision_id, entry_id]
            detail["module_ref"] = ref
            detail["installed"] = any(
                binding.module.module_id == module_id and binding.module.revision_id == revision_id
                for binding in run.assembly.bindings
            )
            run.record(
                "library_record_read",
                {
                    "module_ref": ref,
                    "entry_id": entry_id,
                    "start": detail["start"],
                    "end": detail["end"],
                    "length": detail["length"],
                    "digest": detail["digest"],
                },
            )
            return _json(detail)

        def _require_module_grant(
            action: ModuleAction,
            *,
            module_id: str = "",
            revision_id: str = "",
            binding_id: str = "",
        ) -> None:
            """装卸只看本轮登记过的确切版本或绑定；搜索与一般写作授权都不够。"""
            scope = run.scope.current
            if scope is None:
                raise WorkspaceError(
                    code="TASK_SCOPE_REQUIRED_CONFLICT",
                    where="tools.module_assembly",
                    why="这一轮还没有登记修改范围",
                    fix="先用 set_task_scope 登记装配操作与作者原话",
                )
            if scope.scope.mode == "read_only":
                raise WorkspaceError(
                    code="TASK_SCOPE_DENIED_CONFLICT",
                    where="tools.module_assembly",
                    why="这一轮登记为只读任务",
                    fix="装卸模块要由作者提出新的修改要求",
                )
            grant = next(
                (
                    item
                    for item in scope.draft.module_grants
                    if item.action == action
                    and item.module_id == module_id
                    and item.revision_id == revision_id
                    and item.binding_id == binding_id
                ),
                None,
            )
            if grant is None:
                raise WorkspaceError(
                    code="TASK_SCOPE_DENIED_CONFLICT",
                    where="tools.module_assembly",
                    why="本轮范围不包含这次装配操作",
                    fix="只操作已登记的确切模块版本或绑定；需要变更时请作者明确要求",
                    details={
                        "action": action,
                        "module_id": module_id,
                        "revision_id": revision_id,
                        "binding_id": binding_id,
                        "granted": [item.to_dict() for item in scope.draft.module_grants],
                    },
                )

        def _require_library_publish() -> None:
            scope = run.scope.current
            if scope is None:
                raise WorkspaceError(
                    code="TASK_SCOPE_REQUIRED_CONFLICT",
                    where="tools.publish_module",
                    why="这一轮还没有登记修改范围",
                    fix="先用 set_task_scope 登记库发布与作者原话",
                )
            if not scope.draft.library_publish:
                raise WorkspaceError(
                    code="TASK_SCOPE_DENIED_CONFLICT",
                    where="tools.publish_module",
                    why="作者没有明确要求把内容发布到模块库",
                    fix="只在作者明确要求保存或发布模块时使用；搜索与装入不算发布授权",
                    details={"version": scope.version},
                )

        async def attach_module(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            run.require_writable("tools.attach_module")
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.attach_module",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再装入",
                )
            module_id = _text_argument(arguments, "module_id")
            revision_id = _text_argument(arguments, "revision_id")
            label = _optional_text_argument(arguments, "label")
            _require_module_grant("attach", module_id=module_id, revision_id=revision_id)
            service = AssemblyService(run.library, workspace)
            view = service.bind(
                module_id=module_id,
                revision_id=revision_id,
                label=label,
                expected_digest=service.store.digest(),
                request_key=f"{run.turn_id}:attach:{module_id}:{revision_id}",
            )
            run.record(
                "assembly_attached",
                {
                    "module_id": module_id,
                    "revision_id": revision_id,
                    "digest": view.get("digest"),
                },
            )
            # 专用装配轮：提交后结束，避免同轮后续工具按旧装配继续。
            return _json(
                {
                    "attached": True,
                    "assembly": view,
                    "note": "装配已提交；这一轮到此结束，下一轮写作会使用新装配。",
                },
                terminate=True,
            )

        async def detach_module(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            run.require_writable("tools.detach_module")
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.detach_module",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再卸下",
                )
            binding_id = _text_argument(arguments, "binding_id")
            _require_module_grant("detach", binding_id=binding_id)
            service = AssemblyService(run.library, workspace)
            view = service.unbind(
                binding_id=binding_id,
                expected_digest=service.store.digest(),
                request_key=f"{run.turn_id}:detach:{binding_id}",
            )
            run.record(
                "assembly_detached",
                {"binding_id": binding_id, "digest": view.get("digest")},
            )
            return _json(
                {
                    "detached": True,
                    "assembly": view,
                    "note": "装配已提交；这一轮到此结束，下一轮写作不再使用该模块。",
                },
                terminate=True,
            )

        async def publish_module(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            run.require_writable("tools.publish_module")
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.publish_module",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再发布",
                )
            _require_library_publish()
            entries = _entry_drafts_argument(arguments.get("entries"))
            module_id = _optional_text_argument(arguments, "module_id")
            library = run.library
            if module_id:
                # 默认基于调用时的 head 编辑并做 CAS：期间别人发布过就冲突，不覆盖新版本。
                current = library.module(module_id)
                base_revision = _optional_text_argument(arguments, "base_revision") or current.head
                expected_head = _optional_text_argument(arguments, "expected_head") or current.head
                revision = library.publish(
                    module_id=module_id,
                    base_revision=base_revision,
                    expected_head=expected_head,
                    entries=entries,
                    request_key=f"{run.turn_id}:publish:{module_id}",
                )
                view: dict[str, object] = {
                    "created": False,
                    "module_id": module_id,
                    "revision_id": revision.revision_id,
                }
            else:
                kind = _text_argument(arguments, "kind")
                name = _text_argument(arguments, "name")
                created = library.create_module(
                    kind=kind,
                    name=name,
                    entries=entries,
                    request_key=_hashed_key("publish-new", run.turn_id, kind, name),
                )
                revision_row = cast(Mapping[str, object], created.get("revision") or {})
                view = {
                    "created": True,
                    "module_id": str(revision_row.get("module_id") or ""),
                    "revision_id": str(revision_row.get("revision_id") or ""),
                }
            run.record(
                "module_published",
                {
                    "module_id": view["module_id"],
                    "revision_id": view["revision_id"],
                    "entries": len(entries),
                    "created": view["created"],
                },
            )
            return _json(
                {
                    **view,
                    "entries": len(entries),
                    "note": "已发布新版本；不自动装入本作品，也不改其他作品的绑定。",
                }
            )

        async def prepare_module_changes(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            """把一次创作里的多份内容写成任务进度；不改库装配，也不提前结束本轮。"""
            del tool_call_id, signal, on_update
            run.require_writable("tools.prepare_module_changes")
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.prepare_module_changes",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再保存内容",
                )
            operations = _change_operations_argument(arguments.get("operations"))
            service = ModuleChangeService(run.library, workspace)
            run.scope.check_module_changes(_change_targets(operations))
            base_digest = _optional_text_argument(arguments, "base_digest")
            view = service.prepare(
                turn_id=run.turn_id,
                operations=operations,
                base_digest=base_digest or service.assembly.store.digest(),
            )
            run.record("module_changes_prepared", view)
            return _json(view)

        async def apply_module_changes(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            """保存已准备的内容并一次性提交本作品装配；成功后本轮进入收尾。"""
            del tool_call_id, signal, on_update
            run.require_writable("tools.apply_module_changes")
            if run.library is None:
                raise WorkspaceError(
                    code="MODULE_ACCESS_DENIED",
                    where="tools.apply_module_changes",
                    why="这一轮没有可用的模块库",
                    fix="确认应用已配置模块库后再应用内容",
                )
            change_set_id = _text_argument(arguments, "change_set_id")
            service = ModuleChangeService(run.library, workspace)
            prepared = service.view(change_set_id=change_set_id)
            run.scope.check_module_changes(_targets_from_view(prepared.get("operations")))
            result = service.apply(
                change_set_id=change_set_id,
                turn_id=run.turn_id,
                ledger=run.materials,
                expected_digest=_optional_text_argument(arguments, "expected_digest")
                or service.assembly.store.digest(),
            )
            saved_events = cast(list[object], result.get("saved_events") or [])
            for saved in saved_events:
                if isinstance(saved, dict):
                    run.record("module_content_saved", saved)
            if result.get("applied"):
                run.record(
                    "module_changes_applied",
                    {
                        "change_set_id": change_set_id,
                        "assembly": result.get("assembly"),
                        "operations": result.get("operations"),
                        "saved": result.get("saved"),
                        "undo": result.get("undo"),
                    },
                )
                # 同一轮不再继续创作写入，避免后续工具按旧装配继续。
                run.finalizing = True
            else:
                run.record(
                    "module_changes_failed",
                    {
                        "change_set_id": change_set_id,
                        "stage": result.get("stage"),
                        "error": result.get("error"),
                        "operations": result.get("operations"),
                        "saved": result.get("saved"),
                    },
                )
            return _json(result)

        tools.extend(
            [
                AgentTool(
                    name="list_modules",
                    label="查看已装模块",
                    description=("列出本轮装配里的模块实例与精确版本，含条目数；不接受未装模块。"),
                    parameters={
                        "type": "object",
                        "properties": {
                            "limit": {"type": "integer", "minimum": 1},
                            "offset": {
                                "type": "integer",
                                "minimum": 0,
                                "description": "从第几个模块开始；用上一页的 next_cursor",
                            },
                        },
                        "additionalProperties": False,
                    },
                    execute_fn=list_modules,
                    prompt_guidelines=("只在这里出现的模块版本可以读取；不要按名字去库里找。",),
                    execution_mode="sequential",
                ),
                AgentTool(
                    name="query_records",
                    label="检索资料",
                    description=(
                        "查询本轮已装模块的条目与本作品观察：可按模块、人物、章区间与关键词。"
                        "返回来源、版本、命中原因与扫描量；命中不等于读过全文。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "关键词或别名"},
                            "kinds": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "条目种类，如 character、author_event",
                            },
                            "module_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "只查这些已装实例",
                            },
                            "entity_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "人物或作者事件的条目 ID",
                            },
                            "chapter_paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "只看这些正文观察",
                            },
                            "include_plans": {
                                "type": "boolean",
                                "description": "是否返回计划类模块；默认 true",
                            },
                            "cursor": {"type": "string", "description": "上一页的 next_cursor"},
                            "limit": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "默认 20，最多 50",
                            },
                        },
                        "additionalProperties": False,
                    },
                    execute_fn=query_records,
                    prompt_guidelines=(
                        "先给具体模块、人物、章区间或关键词；没有命中时看 scope 与 warnings。",
                        "命中只用于定位；原文用 read_record 或 read_file，不要凭摘要写作。",
                    ),
                    execution_mode="sequential",
                ),
                AgentTool(
                    name="read_record",
                    label="读取模块条目",
                    description=("按本轮装配的完整引用读取模块条目原文与区间；未装版本会被拒绝。"),
                    parameters={
                        "type": "object",
                        "properties": {
                            "module_id": {"type": "string"},
                            "revision_id": {"type": "string"},
                            "entry_id": {"type": "string"},
                            "start": {"type": "integer", "minimum": 0},
                            "end": {"type": "integer", "minimum": 0},
                        },
                        "required": ["module_id", "revision_id", "entry_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=read_record,
                    prompt_guidelines=(
                        "module_ref 四段必须来自 list_modules/query_records；"
                        "不要用库 head 代替精确版本。",
                        "truncated 为真时按 next_cursor 用 start 继续读。",
                    ),
                    execution_mode="sequential",
                ),
            ]
        )
        if mode == "write":
            tools.append(
                AgentTool(
                    name="search_module_library",
                    label="搜索全局模块库",
                    description=(
                        "仅在作者明确要求从模块库挑选模块时使用；返回有界候选与精确版本，"
                        "不自动装入、升级或修改任何内容。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "名称或关键词"},
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "characters",
                                    "world",
                                    "clues",
                                    "truth",
                                    "timeline",
                                    "plot",
                                    "reference",
                                ],
                            },
                            "cursor": {"type": "string"},
                            "limit": {"type": "integer", "minimum": 1},
                        },
                        "additionalProperties": False,
                    },
                    execute_fn=search_module_library,
                    prompt_guidelines=(
                        "先在范围里登记 library_search；作者没有明确要求就不要搜索。",
                        "搜索结果只是候选；装入要由作者确认，不能自己改装配。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="read_library_record",
                    label="单轮读取库记录",
                    description=(
                        "作者明确要求参考库内容时，只读一个精确版本的目录或条目原文；"
                        "不自动装入，也不开放其他模块。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "module_id": {"type": "string"},
                            "revision_id": {
                                "type": "string",
                                "description": "省略时读取当前 head；写入前仍应确认版本",
                            },
                            "entry_id": {
                                "type": "string",
                                "description": "省略时只返回该版本的条目标题目录",
                            },
                            "start": {"type": "integer", "minimum": 0},
                            "end": {"type": "integer", "minimum": 0},
                        },
                        "required": ["module_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=read_library_record,
                    prompt_guidelines=(
                        "只在作者明确要求引用模块库内容且已登记 library_search 时使用。",
                        "单轮引用不改变装配；要长期使用仍需作者确认装入。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="attach_module",
                    label="装入模块",
                    description=(
                        "把本轮范围登记过的精确模块版本装入本作品；只改装配清单，不改库与正文。"
                        "提交后本轮结束。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "module_id": {"type": "string"},
                            "revision_id": {"type": "string"},
                            "label": {"type": "string", "description": "可选：界面显示名"},
                        },
                        "required": ["module_id", "revision_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=attach_module,
                    prompt_guidelines=(
                        "只在作者明确要求装入、且范围已登记这个精确版本时使用。",
                        "不要按模块名猜版本；版本应从 list_modules/query_records 或库搜索里取。",
                        "提交装配后本轮结束；写作下一轮才会看到新模块。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="detach_module",
                    label="卸下模块",
                    description=(
                        "把本轮范围登记过的绑定从本作品卸下；后续默认查询与依赖展开都会排除它。"
                        "正文事实不受影响，提交后本轮结束。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "binding_id": {"type": "string", "description": "本作品的绑定 ID"},
                        },
                        "required": ["binding_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=detach_module,
                    prompt_guidelines=(
                        "先用 list_modules 拿到绑定 ID；不要按模块名猜。",
                        "卸下只影响后续查询；已经写进正文的事实仍然保留。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="publish_module",
                    label="发布模块",
                    description=(
                        "把作者要求的条目发布到全局模块库：已有模块发布新版本，或新建一个实例。"
                        "不自动装入本作品，也不改其他作品的绑定。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "module_id": {
                                "type": "string",
                                "description": "已有模块：发布新版本；省略时按 kind 与 name 新建",
                            },
                            "kind": {
                                "type": "string",
                                "enum": [
                                    "characters",
                                    "world",
                                    "clues",
                                    "truth",
                                    "timeline",
                                    "plot",
                                    "reference",
                                ],
                            },
                            "name": {"type": "string"},
                            "base_revision": {"type": "string"},
                            "expected_head": {"type": "string"},
                            "entries": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "entry_id": {"type": "string"},
                                        "kind": {"type": "string"},
                                        "title": {"type": "string"},
                                        "body": {"type": "string"},
                                        "aliases": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "fields": {"type": "object"},
                                    },
                                    "required": ["entry_id", "kind", "title"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["entries"],
                        "additionalProperties": False,
                    },
                    execute_fn=publish_module,
                    prompt_guidelines=(
                        "只在作者明确要求保存或发布到模块库、且范围已登记 library_publish 时使用。",
                        "发布不隐含装入；要用于本作品时另行登记装配操作。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="prepare_module_changes",
                    label="准备模块内容",
                    description=(
                        "把本次创作的多份内容写成任务进度：可同时新建多份或修改已有模块。"
                        "只校验结构与目标，不改库装配；随后用 apply_module_changes 一次应用。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "operations": {
                                "type": "array",
                                "description": "本次要新建或修改的内容；每份一个独立 operation_id",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "operation_id": {
                                            "type": "string",
                                            "description": "本任务稳定的操作与槽位标识",
                                        },
                                        "action": {
                                            "type": "string",
                                            "enum": ["create", "update"],
                                        },
                                        "name": {
                                            "type": "string",
                                            "description": "模块显示名；新建时必填",
                                        },
                                        "body": {
                                            "type": "string",
                                            "description": "新建模块的完整正文",
                                        },
                                        "binding_id": {
                                            "type": "string",
                                            "description": "修改：本作品的绑定 ID",
                                        },
                                        "module_id": {"type": "string"},
                                        "base_revision": {
                                            "type": "string",
                                            "description": "修改：读取到的基础版本",
                                        },
                                        "edits": {
                                            "type": "array",
                                            "description": "修改：按条目给出唯一替换或整份正文",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "entry_id": {"type": "string"},
                                                    "base_digest": {
                                                        "type": "string",
                                                        "description": "读取该条目时返回的 digest",
                                                    },
                                                    "find": {
                                                        "type": "string",
                                                        "description": "条目正文里唯一出现的原文",
                                                    },
                                                    "replace": {"type": "string"},
                                                    "body": {
                                                        "type": "string",
                                                        "description": "整份重写；需已读全文",
                                                    },
                                                },
                                                "required": ["entry_id", "base_digest"],
                                                "additionalProperties": False,
                                            },
                                        },
                                    },
                                    "required": ["operation_id", "action"],
                                    "additionalProperties": False,
                                },
                            },
                            "base_digest": {
                                "type": "string",
                                "description": "可选：读取到的装配指纹；省略时用当前值",
                            },
                        },
                        "required": ["operations"],
                        "additionalProperties": False,
                    },
                    execute_fn=prepare_module_changes,
                    prompt_guidelines=(
                        "先用 set_task_scope 登记 module_content_grants，再提交内容。",
                        "新建正文由服务适配为自由资料；不要为每个字段建模块，也不建空模块。",
                        "修改前必须读取实际条目与版本，并带上 base_digest。",
                    ),
                    execution_mode="sequential",
                )
            )
            tools.append(
                AgentTool(
                    name="apply_module_changes",
                    label="应用模块内容",
                    description=(
                        "保存已准备的内容并一次性更新当前作品装配。成功后本轮进入收尾："
                        "只读取结果并回复，不再继续写入。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "change_set_id": {
                                "type": "string",
                                "description": "prepare_module_changes 返回的变更集 ID",
                            },
                            "expected_digest": {
                                "type": "string",
                                "description": "可选：提交前的装配指纹",
                            },
                        },
                        "required": ["change_set_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=apply_module_changes,
                    prompt_guidelines=(
                        "内容保存失败时保留生成稿，按返回的阶段说明可继续的范围。",
                        "应用成功后不要继续改文件；用实际结果说明已用于本作品的内容。",
                    ),
                    execution_mode="sequential",
                )
            )

    if mode in ("write", "idea"):
        search_phrases = (
            "找灵感",
            "搜灵感",
            "搜索灵感",
            "查灵感",
            "翻灵感",
            "看看灵感",
            "灵感里找",
            "灵感里查",
        )
        save_phrases = (
            "记下来",
            "记一下",
            "记住",
            "保存为灵感",
            "存成灵感",
            "存到灵感",
            "记到灵感",
            "放进灵感",
            "写到灵感",
        )

        def _author_said(phrases: tuple[str, ...]) -> bool:
            text = run.current_author_text
            if text is None:
                # 直接构造工具的调用方没有受理快照时，只认最新一条作者消息。
                text = max(run.author_messages, default=(0, ""), key=lambda item: item[0])[1]
            # 引号中的笔记内容既不授予权限，也不把正文里的否定误当成操作禁止。
            text = re.sub(r"「[^」]*」|『[^』]*』|“[^”]*”|\"[^\"]*\"|'[^']*'", "", text)
            clauses = re.split(r"[，,。.!！？?；;\n]|但是?|不过", text)
            matching = [clause for clause in clauses if any(phrase in clause for phrase in phrases)]
            # 同一操作的明确禁止优先；搜索和保存分别判断，互不授予权限。
            denied = (
                r"不要|不用|无需|不必|不准|不能|禁止|不得|别(?!忘)|"
                r"不想|不需要|不允许|不可以|不再|暂不"
            )
            return bool(matching) and not any(re.search(denied, clause) for clause in matching)

        async def read_idea(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            idea_id = _text_argument(arguments, "idea_id")
            revision_id = str(arguments.get("revision_id") or "").strip()
            allowed = {(item.idea_id, item.revision_id) for item in run.idea_references}
            if not revision_id:
                selected = [item for item in run.idea_references if item.idea_id == idea_id]
                if selected:
                    revision_id = selected[0].revision_id
            if (idea_id, revision_id) not in allowed and (
                idea_id,
                revision_id,
            ) not in run.idea_visible:
                raise WorkspaceError(
                    code="IDEA_ACCESS_DENIED",
                    where="tools.read_idea",
                    why="这条灵感不在本轮授权范围内",
                    fix="请作者先在本轮选中这条灵感，或明确要求检索灵感本",
                    details={"idea_id": idea_id, "revision_id": revision_id},
                )
            view = IdeaStore(workspace).revision(idea_id, revision_id)
            return _json(
                {
                    "idea_id": idea_id,
                    "revision_id": revision_id,
                    "title": view["title"],
                    "body": view["body"],
                    "tags": view["tags"],
                    "note": "灵感是作者笔记，不是正文事实；写进正文前要由作者确认。",
                }
            )

        async def search_ideas(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            if not _author_said(search_phrases):
                raise WorkspaceError(
                    code="IDEA_SEARCH_DENIED",
                    where="tools.search_ideas",
                    why="作者没有要求检索灵感本",
                    fix="只在作者明确说找灵感、搜索灵感时使用；普通写作不能顺手翻灵感",
                )
            query = str(arguments.get("query") or "").strip()
            page = IdeaStore(workspace).list(q=query, limit=10)
            items = cast(list[dict[str, object]], page["items"])
            for item in items:
                run.idea_visible.add((str(item["idea_id"]), str(item["head_revision_id"])))
            return _json(
                {
                    "items": items,
                    "total": page["total"],
                    "note": "这些摘要只在本轮可继续用 read_idea 读取；下一轮不再自动可见。",
                }
            )

        save_operations = 0

        def _save_operation(call_id: str) -> str:
            """一次保存调用一个稳定身份：同一调用重试复用，不同调用各自独立。"""
            nonlocal save_operations
            text = str(call_id or "").strip()
            if text:
                return text
            save_operations += 1
            return f"call-{save_operations}"

        async def save_idea(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del signal, on_update
            if not _author_said(save_phrases):
                raise WorkspaceError(
                    code="IDEA_SAVE_DENIED",
                    where="tools.save_idea",
                    why="作者没有明确要求把内容记成灵感",
                    fix="只在作者说记下来、保存为灵感时使用；普通讨论不要自动收集",
                )
            # 新 schema 只公开正文；旧参数仍被接受，供历史重放与旧调用记录使用。
            body = str(arguments.get("body") or "")
            title = str(arguments.get("title") or "").strip()
            tags = list(_string_list_argument(arguments, "tags"))
            created = IdeaStore(workspace).create(
                title=title,
                body=body,
                tags=tags,
                request_key=f"{run.turn_id}:idea:{_save_operation(tool_call_id)}",
            )
            run.record(
                "idea_saved",
                {
                    "idea_id": created["idea_id"],
                    "revision_id": created["revision_id"],
                    "title": created["title"],
                    "tool_call_id": tool_call_id,
                },
            )
            return _json(
                {
                    "idea_id": created["idea_id"],
                    "revision_id": created["revision_id"],
                    "note": "已保存到灵感本；它不是正式设定，也不会自动写进正文。",
                }
            )

        tools.extend(
            [
                AgentTool(
                    name="read_idea",
                    label="读取灵感",
                    description=(
                        "读取作者本轮选中、或本轮明确搜索到的灵感修订；未授权的灵感拒绝。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "idea_id": {"type": "string"},
                            "revision_id": {
                                "type": "string",
                                "description": "省略时使用本轮选中的那个修订",
                            },
                        },
                        "required": ["idea_id"],
                        "additionalProperties": False,
                    },
                    execute_fn=read_idea,
                    prompt_guidelines=(
                        "只读本轮选中的修订；作者改过之后不要拿旧修订当当前。",
                        "灵感是作者笔记，不是已发生的事实。",
                    ),
                    execution_mode="sequential",
                ),
                AgentTool(
                    name="search_ideas",
                    label="搜索灵感本",
                    description=(
                        "仅在作者明确要求找灵感时使用；返回摘要与当前修订，不自动写入作品。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "关键词或标签"},
                        },
                        "additionalProperties": False,
                    },
                    execute_fn=search_ideas,
                    prompt_guidelines=(
                        "作者没有明确要求就不要搜索灵感；普通目录与文件工具读不到灵感。",
                        "搜索结果只在本轮可见，下一轮要重新获得许可。",
                    ),
                    execution_mode="sequential",
                ),
                AgentTool(
                    name="save_idea",
                    label="记成灵感",
                    description=(
                        "把作者明确要求记下的内容保存为灵感；不改成正式设定，也不自动采用。"
                    ),
                    parameters={
                        "type": "object",
                        "properties": {
                            "body": {
                                "type": "string",
                                "description": "作者要求记下的原文；照抄，不要改写或补充",
                            },
                        },
                        "required": ["body"],
                        "additionalProperties": False,
                    },
                    execute_fn=save_idea,
                    prompt_guidelines=(
                        "只在作者说记下来、保存为灵感时使用；不要自动收集讨论内容。",
                        "保存后告诉作者已记录，但不要把它当成正文设定。",
                    ),
                    execution_mode="sequential",
                ),
            ]
        )

    if mode == "idea":
        # 展开讨论：只读原文、只读本轮固定的灵感；白名单之外的写入、装配、发布、
        # 索引与状态工具一律不返回。新增工具默认不进入这个用途。
        readonly = {
            "list_files",
            "read_file",
            "search_text",
            "list_modules",
            "query_records",
            "read_record",
            "read_state",
            "read_idea",
            "search_ideas",
            "save_idea",
        }
        return [tool for tool in tools if tool.name in readonly]

    if mode == "review":
        # 检查任务：只读原文与搜索，加保存报告；没有作品写工具。
        readonly = {
            "list_files",
            "read_file",
            "search_text",
            "list_modules",
            "query_records",
            "read_record",
        }
        return [
            *[tool for tool in tools if tool.name in readonly],
            AgentTool(
                name="save_review_report",
                label="保存检查报告",
                description=(
                    "发布这一轮的检查报告：目标、状态、问题与未覆盖部分。"
                    "每条问题都要引用实际读到的原文；没读完就报告为未完成。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "target_paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "这次检查的文件",
                        },
                        "requirement": {
                            "type": "string",
                            "description": "作者的检查要求（原话或摘要）",
                        },
                        "status": {
                            "type": "string",
                            "enum": ["findings", "no_findings", "incomplete"],
                        },
                        "findings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": [
                                            "continuity",
                                            "disclosure",
                                            "task_mismatch",
                                            "other",
                                        ],
                                    },
                                    "certainty": {
                                        "type": "string",
                                        "enum": ["supported", "suspected"],
                                    },
                                    "description": {"type": "string"},
                                    "suggestion": {"type": "string"},
                                    "evidence": {
                                        "type": "array",
                                        "items": {
                                            "type": "object",
                                            "properties": {
                                                "path": {"type": "string"},
                                                "digest": {"type": "string"},
                                                "start": {"type": "integer", "minimum": 0},
                                                "end": {"type": "integer", "minimum": 0},
                                                "quote": {"type": "string"},
                                            },
                                            "required": [
                                                "path",
                                                "digest",
                                                "start",
                                                "end",
                                                "quote",
                                            ],
                                            "additionalProperties": False,
                                        },
                                    },
                                },
                                "required": [
                                    "kind",
                                    "certainty",
                                    "description",
                                    "suggestion",
                                    "evidence",
                                ],
                                "additionalProperties": False,
                            },
                        },
                        "uncovered": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "没有覆盖到的部分；状态为 incomplete 时必填",
                        },
                    },
                    "required": ["target_paths", "requirement", "status"],
                    "additionalProperties": False,
                },
                execute_fn=save_review_report,
                prompt_guidelines=(
                    "先读完目标再判断；每条问题至少一处原文证据，跨文件冲突列双方出处。",
                    "没读完或来源不可用时用 incomplete，并写明未覆盖的部分。",
                ),
                execution_mode="sequential",
            ),
        ]
    if mode == "index":
        # 索引任务：目录、搜索、读取加保存索引，没有作品写工具，也不准备 Git 基线。
        readonly = {"list_files", "read_file", "search_text"}
        return [
            *[tool for tool in tools if tool.name in readonly],
            AgentTool(
                name="save_chapter_index",
                label="保存章节索引",
                description=(
                    "发布一章的索引：摘要、事件、人物、时间地点与线索，每条都要引用原文证据。"
                    "只对本轮作者选中的章节有效，且必须先读完当前全文。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "如 正文/0003 书房.md"},
                        "digest": {"type": "string", "description": "read_file 得到的 digest"},
                        "summary": {"type": "string", "description": "这一章发生了什么，≤500 字符"},
                        "evidence": {
                            "type": "array",
                            "description": "原文依据：编号从 1 连续递增，区间与引文一致",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "index": {"type": "integer", "minimum": 1},
                                    "start": {"type": "integer", "minimum": 0},
                                    "end": {"type": "integer", "minimum": 0},
                                    "quote": {"type": "string", "description": "≤160 字符"},
                                },
                                "required": ["index", "start", "end", "quote"],
                                "additionalProperties": False,
                            },
                        },
                        "events": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
                        "characters": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
                        "places": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
                        "clues": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
                    },
                    "required": ["path", "digest", "summary", "evidence"],
                    "additionalProperties": False,
                },
                execute_fn=save_chapter_index,
                prompt_guidelines=(
                    "先读完这一章再发布；摘要只写这一章，不要从旧摘要改写。",
                    "证据区间用 read_file 返回的字符位置，引文要与区间一致。",
                ),
                execution_mode="sequential",
            ),
        ]
    if mode == "state":
        # 核对身份：只读原文与小传，发布带证据的披露条目；没有作品写工具。
        readonly = {"list_files", "read_file", "search_text", "read_state"}
        allowed = tuple(index_allowed)

        def require_entity_read(path: str) -> None:
            run.materials.require_target_read(workspace, ReadRequirement.full(path))

        def require_evidence(evidence: ObservedEvidence, chapter: int) -> None:
            check_evidence(workspace, evidence, chapter=chapter, ledger=run.materials)

        async def save_glossary_term(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            kind = _text_argument(arguments, "kind")
            if kind not in ("person", "place", "org", "item"):
                raise _input_error("kind", "只能是 person / place / org / item")
            evidence_raw = arguments.get("evidence")
            if not isinstance(evidence_raw, dict):
                raise _input_error("evidence", "必须是对象")
            raw_aliases = arguments.get("aliases")
            aliases: tuple[str, ...] = ()
            if raw_aliases is not None:
                if not isinstance(raw_aliases, list):
                    raise _input_error("aliases", "必须是字符串数组")
                aliases = tuple(str(item) for item in raw_aliases if str(item).strip())
            term = GlossaryTerm(
                term=_text_argument(arguments, "term"),
                kind=kind,  # type: ignore[arg-type]
                aliases=aliases,
                first_chapter=_int_argument(arguments, "first_chapter", default=0, minimum=1),
                source="text",
                evidence=ObservedEvidence(
                    path=_text_argument(evidence_raw, "path"),
                    digest=_text_argument(evidence_raw, "digest"),
                    start=_int_argument(evidence_raw, "start", default=0, minimum=0),
                    end=_int_argument(evidence_raw, "end", default=0, minimum=0),
                    quote=_text_argument(evidence_raw, "quote"),
                ),
            )
            if glossary_store is None:
                raise WorkspaceError(
                    code="STATE_STORE_MISSING",
                    where="glossary.save",
                    why="没有可写的状态目录",
                    fix="从作品状态目录重新发起核对",
                )
            assert term.evidence is not None
            require_evidence(term.evidence, term.first_chapter)
            published = publish_terms(workspace, glossary_store, (term,))
            run.record(
                "state_published",
                {
                    "domain": "glossary",
                    "term": term.term,
                    "published": 1,
                    "total": len(published),
                    "evidence": arguments.get("evidence"),
                },
            )
            return _json({"term": term.term, "total": len(published)})

        async def save_character_state(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            entity = _text_argument(arguments, "entity")
            entity_digest = _text_argument(arguments, "entity_digest")
            if allowed and entity not in allowed:
                raise WorkspaceError(
                    code="STATE_ENTITY_INVALID",
                    where=f"roster.save:{entity}",
                    why="这一轮没有选择这份小传",
                    fix="只核对作者选中的小传；需要更多时让作者重新发起核对",
                    details={"path": entity},
                )
            raw = arguments.get("states")
            if not isinstance(raw, list) or not raw:
                raise _input_error("states", "必须是非空数组")
            snapshots = tuple(
                _snapshot_from(item, entity=entity, entity_digest=entity_digest) for item in raw
            )
            if roster_store is None:
                raise WorkspaceError(
                    code="STATE_STORE_MISSING",
                    where="roster.save",
                    why="没有可写的状态目录",
                    fix="从作品状态目录重新发起核对",
                )
            require_entity_read(entity)
            for item in snapshots:
                require_evidence(item.evidence, item.chapter)
            published = publish_states(workspace, roster_store, snapshots)
            run.record(
                "state_published",
                {
                    "domain": "roster",
                    "entity": entity,
                    "published": len(snapshots),
                    "total": len(published),
                    "records": arguments.get("states"),
                },
            )
            return _json({"entity": entity, "published": len(snapshots), "total": len(published)})

        async def save_timeline(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            raw = arguments.get("entries")
            if not isinstance(raw, list) or not raw:
                raise _input_error("entries", "必须是非空数组")
            entries = tuple(_timeline_from(item) for item in raw)
            if timeline_store is None:
                raise WorkspaceError(
                    code="STATE_STORE_MISSING",
                    where="timeline.save",
                    why="没有可写的状态目录",
                    fix="从作品状态目录重新发起核对",
                )
            for entry in entries:
                require_evidence(entry.evidence, entry.chapter)
            published = publish_timeline(workspace, timeline_store, entries)
            run.record(
                "state_published",
                {
                    "domain": "timeline",
                    "published": len(entries),
                    "total": len(published),
                    "records": arguments.get("entries"),
                },
            )
            return _json({"published": len(entries), "total": len(published)})

        async def save_clues(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            raw = arguments.get("entries")
            if not isinstance(raw, list) or not raw:
                raise _input_error("entries", "必须是非空数组")
            entries = tuple(_clue_from(item) for item in raw)
            if clue_store is None:
                raise WorkspaceError(
                    code="STATE_STORE_MISSING",
                    where="clues.save",
                    why="没有可写的状态目录",
                    fix="从作品状态目录重新发起核对",
                )
            for clue in entries:
                require_evidence(clue.evidence, clue.planted_chapter)
                if clue.resolution_evidence is not None and clue.resolution_chapter is not None:
                    require_evidence(clue.resolution_evidence, clue.resolution_chapter)
            published = publish_clues(workspace, clue_store, entries)
            run.record(
                "state_published",
                {
                    "domain": "clues",
                    "published": len(entries),
                    "total": len(published),
                    "records": arguments.get("entries"),
                },
            )
            return _json({"published": len(entries), "total": len(published)})

        async def save_disclosure(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            entity = _text_argument(arguments, "entity")
            entity_digest = _text_argument(arguments, "entity_digest")
            if allowed and entity not in allowed:
                raise WorkspaceError(
                    code="STATE_ENTITY_INVALID",
                    where=f"disclosure.save:{entity}",
                    why="这一轮没有选择这份小传",
                    fix="只核对作者选中的小传；需要更多时让作者重新发起核对",
                    details={"path": entity},
                )
            raw = arguments.get("facts")
            if not isinstance(raw, list) or not raw:
                raise _input_error("facts", "必须是非空数组")
            facts = tuple(
                _observed_from(item, entity=entity, entity_digest=entity_digest) for item in raw
            )
            if disclosure_store is None:
                raise WorkspaceError(
                    code="STATE_STORE_MISSING",
                    where="disclosure.save",
                    why="没有可写的状态目录",
                    fix="从作品状态目录重新发起核对",
                )
            require_entity_read(entity)
            for fact in facts:
                require_evidence(fact.evidence, fact.chapter)
            published = publish_observed(workspace, disclosure_store, facts)
            run.record(
                "state_published",
                {
                    "domain": "disclosure",
                    "entity": entity,
                    "published": len(facts),
                    "total": len(published),
                    "records": arguments.get("facts"),
                },
            )
            return _json(
                {
                    "entity": entity,
                    "published": len(facts),
                    "total": len(published),
                }
            )

        return [
            *[tool for tool in tools if tool.name in readonly],
            AgentTool(
                name="save_disclosure",
                label="保存身份核对",
                description=(
                    "记录一条或多条「读者从第几章起知道什么」，每条都要给出正文原文依据。"
                    "只对作者选中核对的小传有效，必须先读完该小传与相关章节。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "如 设定集/角色小传/林静.md"},
                        "entity_digest": {
                            "type": "string",
                            "description": "read_file 读到的小传 digest",
                        },
                        "facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "fact": {"type": "string", "description": "读者知道了什么"},
                                    "chapter": {"type": "integer", "minimum": 1},
                                    "evidence": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "digest": {"type": "string"},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 0},
                                            "quote": {"type": "string", "description": "≤160 字符"},
                                        },
                                        "required": ["path", "digest", "start", "end", "quote"],
                                        "additionalProperties": False,
                                    },
                                },
                                "required": ["fact", "chapter", "evidence"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["entity", "entity_digest", "facts"],
                    "additionalProperties": False,
                },
                execute_fn=save_disclosure,
                prompt_guidelines=(
                    "只记录正文里确实写到的披露；凭设定推测的不要发布。",
                    "证据区间用 read_file 返回的字符位置，引文要与区间完全一致。",
                ),
                execution_mode="sequential",
            ),
            AgentTool(
                name="save_glossary_term",
                label="登记专有名词",
                description=(
                    "登记正文里确实出现过的专名（地名、组织、物件），带一处原文依据。"
                    "人物姓名与别名来自小传，不需要重复登记。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "term": {"type": "string"},
                        "kind": {"type": "string", "enum": ["person", "place", "org", "item"]},
                        "aliases": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "同一事物的其他写法",
                        },
                        "first_chapter": {"type": "integer", "minimum": 1},
                        "evidence": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string"},
                                "digest": {"type": "string"},
                                "start": {"type": "integer", "minimum": 0},
                                "end": {"type": "integer", "minimum": 0},
                                "quote": {"type": "string", "description": "≤160 字符"},
                            },
                            "required": ["path", "digest", "start", "end", "quote"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["term", "kind", "first_chapter", "evidence"],
                    "additionalProperties": False,
                },
                execute_fn=save_glossary_term,
                prompt_guidelines=(
                    "只登记正文出现过的名字；同义写法放进 aliases。",
                    "证据区间用 read_file 返回的字符位置，引文要与区间完全一致。",
                ),
                execution_mode="sequential",
            ),
            AgentTool(
                name="save_character_state",
                label="保存人物状态",
                description=(
                    "记录某人在某一章结束时的状态：位置、是否还活着、立场。"
                    "每条都要给出正文原文依据；只对作者选中核对的小传有效。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "entity": {"type": "string", "description": "如 设定集/角色小传/林静.md"},
                        "entity_digest": {
                            "type": "string",
                            "description": "read_file 读到的小传 digest",
                        },
                        "states": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "chapter": {"type": "integer", "minimum": 1},
                                    "location": {
                                        "type": "string",
                                        "description": "这一章结束时在哪",
                                    },
                                    "alive": {"type": "boolean"},
                                    "stance": {
                                        "type": "string",
                                        "description": "这一章结束时的立场，可为空",
                                    },
                                    "evidence": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "digest": {"type": "string"},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 0},
                                            "quote": {"type": "string", "description": "≤160 字符"},
                                        },
                                        "required": ["path", "digest", "start", "end", "quote"],
                                        "additionalProperties": False,
                                    },
                                },
                                "required": ["chapter", "alive", "evidence"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["entity", "entity_digest", "states"],
                    "additionalProperties": False,
                },
                execute_fn=save_character_state,
                prompt_guidelines=(
                    "只记这一章正文明确写到的事；人物说自己在某处不等于他真在。",
                    "位置与立场至少要有一个；都没有就不要发布这一条。",
                    "死亡只在正文确实写到死亡时记 alive=false。",
                ),
                execution_mode="sequential",
            ),
            AgentTool(
                name="save_timeline",
                label="保存时间锚点",
                description=(
                    "记录一章的时间锚点：原文怎么说的时间、跨了多久、主要在哪。"
                    "一章一条，后来的覆盖旧的。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "entries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "chapter": {"type": "integer", "minimum": 1},
                                    "when": {
                                        "type": "string",
                                        "description": "原文里的时间说法，如「10月21日深夜」",
                                    },
                                    "span": {"type": "string", "description": "这一章跨了多久"},
                                    "location": {"type": "string", "description": "主要地点"},
                                    "evidence": {
                                        "type": "object",
                                        "properties": {
                                            "path": {"type": "string"},
                                            "digest": {"type": "string"},
                                            "start": {"type": "integer", "minimum": 0},
                                            "end": {"type": "integer", "minimum": 0},
                                            "quote": {"type": "string", "description": "≤160 字符"},
                                        },
                                        "required": ["path", "digest", "start", "end", "quote"],
                                        "additionalProperties": False,
                                    },
                                },
                                "required": ["chapter", "when", "evidence"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["entries"],
                    "additionalProperties": False,
                },
                execute_fn=save_timeline,
                prompt_guidelines=(
                    "时间写原文的说法，不要换算成自己推的日期。",
                    "跨度只在原文给了线索时写，例如「次日清晨」就是一夜。",
                ),
                execution_mode="sequential",
            ),
            AgentTool(
                name="save_clues",
                label="登记线索",
                description=(
                    "登记读者看到过的线索：埋在哪一章、计划哪一章回收、现在什么状态。"
                    "每条都要给出埋设处的原文依据；标为已回收时还要给出回收处。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "entries": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "clue": {
                                        "type": "string",
                                        "description": "读者能观察到的事实，不是解释",
                                    },
                                    "planted_chapter": {"type": "integer", "minimum": 1},
                                    "due_chapter": {
                                        "type": "integer",
                                        "minimum": 1,
                                        "description": "计划回收章；不确定就不填",
                                    },
                                    "status": {
                                        "type": "string",
                                        "enum": ["open", "resolved", "dropped"],
                                    },
                                    "supports": {
                                        "type": "string",
                                        "description": "这条线索指向哪种可能，可为空",
                                    },
                                    "resolution_chapter": {"type": "integer", "minimum": 1},
                                    "evidence": _EVIDENCE_SCHEMA,
                                    "resolution_evidence": _EVIDENCE_SCHEMA,
                                },
                                "required": ["clue", "planted_chapter", "status", "evidence"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["entries"],
                    "additionalProperties": False,
                },
                execute_fn=save_clues,
                prompt_guidelines=(
                    "线索是读者看到的事实，不是它的解释；解释属于设定，不写进台账。",
                    "只有正文确实写了回收过程才标 resolved，并给出回收处原文。",
                    "作者已经放弃的线索标 dropped，不要删掉：删掉等于下次又会被当成新线索。",
                ),
                execution_mode="sequential",
            ),
        ]
    if mode == "materials":
        # 资料整理：只读目录、搜索、读取，外加本域唯一的结果提交工具；没有作品写工具。
        readonly = {
            "list_files",
            "read_file",
            "search_text",
            "list_modules",
            "query_records",
            "read_record",
        }
        scope = material_scope or {}
        domain = str(scope.get("domain") or "")

        async def publish_material(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            if coverage_store is None:
                raise _input_error("coverage_store", "本轮没有开启资料保存")
            raw_records = arguments.get("records")
            if not isinstance(raw_records, list):
                raise _input_error("records", "必须是数组；空结果用 []")
            result = publish_material_result(
                workspace=workspace,
                root=coverage_store.root,
                scope=scope,
                ledger=run.materials,
                turn_id=run.turn_id,
                domain=_text_argument(arguments, "domain"),
                path=_text_argument(arguments, "path"),
                entity_path=_optional_text_argument(arguments, "entity") or "",
                outcome=_text_argument(arguments, "outcome"),
                note=_optional_text_argument(arguments, "note") or "",
                records=[item for item in raw_records if isinstance(item, dict)],
                digest=_text_argument(arguments, "digest"),
            )
            run.record("material_target_published", dict(result))
            return _json(result)

        return [
            *[tool for tool in tools if tool.name in readonly],
            AgentTool(
                name="publish_material_result",
                label="提交资料结果",
                description=(
                    f"提交一个目标的「{DOMAIN_LABEL.get(domain, domain)}」整理结果。"
                    "有发现用 found 并给出条目；读完确认没有相关发现用 empty；"
                    "信息不足用 insufficient。空结果也要写一句说明。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "domain": {"type": "string", "enum": [domain]},
                        "path": {
                            "type": "string",
                            "description": "本次任务列出的正文章节路径",
                        },
                        "digest": {"type": "string", "description": "read_file 得到的 digest"},
                        "entity": {
                            "type": "string",
                            "description": "人物域要填本次选择的小传路径；其他域不要填",
                        },
                        "outcome": {
                            "type": "string",
                            "enum": ["found", "empty", "insufficient"],
                        },
                        "note": {"type": "string", "description": "空结果或信息不足的说明"},
                        "records": {
                            "type": "array",
                            "items": _MATERIAL_RECORD_SCHEMA[domain],
                        },
                    },
                    "required": ["domain", "path", "digest", "outcome", "records"],
                    "additionalProperties": False,
                },
                execute_fn=publish_material,
                prompt_guidelines=(
                    "先读完目标当前全文再提交；每条记录都要给原文证据。",
                    "没有提交结果的目标不算完成；不要用回复代替结果提交。",
                    "空结果也要写说明，并确认目标已完整读取。",
                ),
                execution_mode="sequential",
            ),
        ]
    if mode == "write":

        async def ask_author(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            """把必须由作者决定的问题记下来，并结束本轮等待回答。"""
            del tool_call_id, signal, on_update
            run.require_writable("tools.ask_author")
            if run.state_root is None or not run.conversation_id:
                raise WorkspaceError(
                    code="AUTHOR_QUESTION_UNAVAILABLE",
                    where="tools.ask_author",
                    why="这一轮没有可记录问题的作品状态目录",
                    fix="改为在回复里直接提问；不要假定问题已经保存",
                )
            if run.pending_question is not None:
                # 同一轮重复调用不再新建问题：前一条仍在等回答。
                return _json(
                    {
                        **run.pending_question,
                        "note": "本轮已经记录过问题并等待回答；不要再问第二个。",
                    },
                    terminate=True,
                )
            question = _text_argument(arguments, "question")
            options = _string_list_argument(arguments, "options")
            if len(options) > 5:
                raise _input_error("options", "最多给 5 个选项；更多分支请让作者自由回答")
            related = _string_list_argument(arguments, "paths")
            for path in related:
                if not is_managed_path(path):
                    raise _input_error("paths", f"必须是作品内的受管路径：{path}")
            record = QuestionStore(run.state_root).create(
                conversation_id=run.conversation_id,
                turn_id=run.turn_id,
                question=question,
                options=options,
                related=related,
                assembly_digest=run.assembly_digest,
                context=dict(run.request_context),
            )
            view = record.view()
            run.pending_question = view
            run.record("author_input_requested", view)
            return _json(
                {
                    **view,
                    "note": "问题已记录，本轮到此结束；作者回答后会开启新一轮，带着原请求继续。",
                },
                terminate=True,
            )

        tools.append(
            AgentTool(
                name="ask_author",
                label="询问作者",
                description=(
                    "只在任务目标不明或创作取舍无法确定时使用：把问题、可选答案和关联内容"
                    "记录下来并结束本轮。作者回答后会在下一轮继续原请求；"
                    "模块拆分、命名与已授权的人物创作不需要问。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "需要作者决定的具体问题，一句话说清",
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "可选答案；作者仍可自由回答",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "与问题有关的作品内路径，便于作者对照",
                        },
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
                execute_fn=ask_author,
                prompt_guidelines=(
                    "能自行判断的不要问；一次只问一个必须由作者决定的问题。",
                    "调用后本轮结束，不要再写入或假装已经继续。",
                ),
                execution_mode="sequential",
            )
        )
    return tools


def build_turn_assembly(
    workspace: Workspace,
    *,
    mode: ToolMode = "write",
    selected_path: str | None = None,
    history_facts: str = "",
    materials: str = "",
    ideas: str = "",
    modules: str = "",
    index_notes: str = "",
    index_excluded: Sequence[Exclusion] = (),
    instructions: str = "",
    disclosure_store: DisclosureStore | None = None,
    glossary_store: GlossaryStore | None = None,
    roster_store: CharacterStateStore | None = None,
    timeline_store: TimelineStore | None = None,
    clue_store: ClueStore | None = None,
    as_of_chapter: int | None = None,
) -> AssembledContext:
    """这一轮的上下文装配：工具模式决定用途，用途决定装哪些分区。

    服务层与 harness 共用这一个入口，保证「记下来的注入内容」和「实际发给模型的内容」
    是同一份结果，而不是两处各拼一遍。
    """
    return build_assembled_context(
        workspace,
        as_of_chapter=as_of_chapter,
        profile=_PROFILE_BY_MODE[mode],
        selected_path=selected_path,
        history_facts=history_facts,
        materials=materials,
        ideas=ideas,
        modules=modules,
        index_notes=index_notes,
        index_excluded=index_excluded,
        instructions=instructions or _instructions_for(mode),
        disclosure_store=disclosure_store,
        glossary_store=glossary_store,
        roster_store=roster_store,
        timeline_store=timeline_store,
        clue_store=clue_store,
    )


def build_harness(
    *,
    provider: ModelProvider,
    model: str,
    workspace: Workspace,
    run: RunContext,
    selected_path: str | None = None,
    max_turns: int = MAX_TURNS,
    history: Sequence[AgentMessage] = (),
    history_facts: str = "",
    materials: str = "",
    ideas: str = "",
    modules: str = "",
    index_notes: str = "",
    mode: ToolMode = "write",
    index_allowed: Sequence[str] = (),
    index_store: ChapterIndexStore | None = None,
    review_targets: Sequence[str] = (),
    review_store: ReviewStore | None = None,
    disclosure_store: DisclosureStore | None = None,
    glossary_store: GlossaryStore | None = None,
    roster_store: CharacterStateStore | None = None,
    timeline_store: TimelineStore | None = None,
    clue_store: ClueStore | None = None,
    material_scope: Mapping[str, object] | None = None,
    coverage_store: CoverageStore | None = None,
    assembly: AssembledContext | None = None,
) -> AgentHarness:
    """单个写作 Agent：系统提示 + 文件工具 + 回合上限。

    ``history`` 只有本会话公开过的提示与回复（见 ``conversation_context``）；当前提示由
    ``prompt()`` 追加一次，不能已经包含在 ``history`` 里，否则模型会收到两遍。
    ``assembly`` 由调用方传入时直接使用，避免同一轮装配两次、结果不一致。
    """
    resolved = assembly or build_turn_assembly(
        workspace,
        as_of_chapter=run.state_chapter,
        mode=mode,
        selected_path=selected_path,
        history_facts=history_facts,
        materials=materials,
        ideas=ideas,
        modules=modules,
        index_notes=index_notes,
        disclosure_store=disclosure_store,
        glossary_store=glossary_store,
        roster_store=roster_store,
        timeline_store=timeline_store,
        clue_store=clue_store,
    )
    return AgentHarness(
        AgentHarnessConfig(
            provider=provider,
            model=model,
            system=resolved.text,
            tools=build_tools(
                workspace,
                run,
                mode=mode,
                index_allowed=index_allowed,
                index_store=index_store,
                review_targets=review_targets,
                review_store=review_store,
                disclosure_store=disclosure_store,
                glossary_store=glossary_store,
                roster_store=roster_store,
                timeline_store=timeline_store,
                clue_store=clue_store,
                material_scope=material_scope,
                coverage_store=coverage_store,
            ),
            max_turns=max_turns,
        ),
        messages=history,
    )


_PROFILE_BY_MODE: dict[ToolMode, Profile] = {
    "overview": "materials",
    "write": "writer",
    "index": "extract",
    # 核对任务与建索引同类：只读原文、发布派生记录，不注入文风与旧摘要。
    "state": "extract",
    "review": "review",
    # 资料整理只装任务要求、目录与本次范围；不装设定集、谜底与作者计划。
    "materials": "materials",
    # 展开灵感：只装任务要求与那条灵感，不注入文风、设定与旧摘要。
    "idea": "idea",
}


def _instructions_for(mode: ToolMode) -> str:
    if mode == "index":
        return INDEX_INSTRUCTIONS
    if mode == "state":
        return STATE_INSTRUCTIONS
    if mode == "review":
        return REVIEW_INSTRUCTIONS
    if mode == "idea":
        return IDEA_INSTRUCTIONS
    return ""


def _json(payload: Mapping[str, Any], *, terminate: bool = False) -> AgentToolResult:
    result = AgentToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        details=dict(payload),
    )
    if terminate:
        result.terminate = True
    return result


def _text_argument(
    arguments: Mapping[str, JSONValue], name: str, *, allow_empty: bool = False
) -> str:
    value = arguments.get(name)
    if not isinstance(value, str):
        raise _input_error(name, "必须是字符串")
    if not value and not allow_empty:
        raise _input_error(name, "不能为空")
    return value


def _int_argument(
    arguments: Mapping[str, JSONValue], name: str, *, default: int, minimum: int
) -> int:
    value = arguments.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise _input_error(name, "必须是整数")
    if value < minimum:
        raise _input_error(name, f"必须 ≥ {minimum}")
    return value


def _observed_from(value: JSONValue, *, entity: str, entity_digest: str) -> ObservedFact:
    """把一条披露参数转成记录；缺字段或类型不对时按可修正的输入错误返回。"""
    if not isinstance(value, dict):
        raise _input_error("facts", "每一项必须是对象")
    return ObservedFact(
        entity_path=entity,
        entity_digest=entity_digest,
        fact=_text_argument(value, "fact"),
        chapter=_int_argument(value, "chapter", default=0, minimum=1),
        evidence=_evidence_argument(value.get("evidence")),
    )


def _evidence_argument(value: JSONValue, name: str = "evidence") -> ObservedEvidence:
    if not isinstance(value, dict):
        raise _input_error(name, "必须是对象")
    return ObservedEvidence(
        path=_text_argument(value, "path"),
        digest=_text_argument(value, "digest"),
        start=_int_argument(value, "start", default=0, minimum=0),
        end=_int_argument(value, "end", default=0, minimum=0),
        quote=_text_argument(value, "quote"),
    )


def _snapshot_from(value: JSONValue, *, entity: str, entity_digest: str) -> StateSnapshot:
    """把一条人物状态参数转成记录；缺字段按可修正的输入错误返回。"""
    if not isinstance(value, dict):
        raise _input_error("states", "每一项必须是对象")
    alive = value.get("alive")
    if not isinstance(alive, bool):
        raise _input_error("alive", "必须是布尔值")
    return StateSnapshot(
        entity_path=entity,
        entity_digest=entity_digest,
        chapter=_int_argument(value, "chapter", default=0, minimum=1),
        location=str(value.get("location") or ""),
        alive=alive,
        stance=str(value.get("stance") or ""),
        evidence=_evidence_argument(value.get("evidence")),
    )


def _timeline_from(value: JSONValue) -> TimelineEntry:
    """把一条时间锚点参数转成记录。"""
    if not isinstance(value, dict):
        raise _input_error("entries", "每一项必须是对象")
    return TimelineEntry(
        chapter=_int_argument(value, "chapter", default=0, minimum=1),
        when=_text_argument(value, "when"),
        span=str(value.get("span") or ""),
        location=str(value.get("location") or ""),
        evidence=_evidence_argument(value.get("evidence")),
    )


def _clue_from(value: JSONValue) -> ClueEntry:
    """把一条线索参数转成记录；状态取值不合法时按可修正的输入错误返回。"""
    if not isinstance(value, dict):
        raise _input_error("entries", "每一项必须是对象")
    status = value.get("status")
    if status not in ("open", "resolved", "dropped"):
        raise _input_error("status", "只能是 open / resolved / dropped")
    due = value.get("due_chapter")
    resolution_chapter = value.get("resolution_chapter")
    raw_resolution = value.get("resolution_evidence")
    return ClueEntry(
        clue=_text_argument(value, "clue"),
        planted_chapter=_int_argument(value, "planted_chapter", default=0, minimum=1),
        due_chapter=(
            _int_argument(value, "due_chapter", default=0, minimum=1) if due is not None else None
        ),
        status=status,  # type: ignore[arg-type]
        supports=str(value.get("supports") or ""),
        evidence=_evidence_argument(value.get("evidence")),
        resolution_chapter=(
            _int_argument(value, "resolution_chapter", default=0, minimum=1)
            if resolution_chapter is not None
            else None
        ),
        resolution_evidence=(
            _evidence_argument(raw_resolution, "resolution_evidence")
            if raw_resolution is not None
            else None
        ),
    )


def _review_findings(value: JSONValue) -> tuple[ReviewFinding, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error("findings", "必须是数组")
    findings: list[ReviewFinding] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise _input_error("findings", "每一项必须是对象")
        kind = raw.get("kind")
        certainty = raw.get("certainty")
        description = raw.get("description")
        suggestion = raw.get("suggestion")
        evidence_raw = raw.get("evidence")
        if not isinstance(kind, str) or not isinstance(certainty, str):
            raise _input_error("findings", "kind 与 certainty 必须是字符串")
        if not isinstance(description, str) or not isinstance(suggestion, str):
            raise _input_error("findings", "description 与 suggestion 必须是字符串")
        if evidence_raw is not None and not isinstance(evidence_raw, list):
            raise _input_error("findings", "evidence 必须是数组")
        evidence: list[ReviewEvidence] = []
        for item in evidence_raw or []:
            if not isinstance(item, dict):
                raise _input_error("findings", "evidence 每一项必须是对象")
            path = item.get("path")
            digest = item.get("digest")
            start = item.get("start")
            end = item.get("end")
            quote = item.get("quote")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise _input_error("findings", "证据要带 path 与 digest")
            if isinstance(start, bool) or not isinstance(start, int):
                raise _input_error("findings", "证据的 start 必须是整数")
            if isinstance(end, bool) or not isinstance(end, int):
                raise _input_error("findings", "证据的 end 必须是整数")
            if not isinstance(quote, str):
                raise _input_error("findings", "证据的 quote 必须是字符串")
            evidence.append(
                ReviewEvidence(path=path, digest=digest, start=start, end=end, quote=quote)
            )
        findings.append(
            ReviewFinding(
                kind=cast(Any, kind),
                certainty=cast(Any, certainty),
                description=description,
                evidence=tuple(evidence),
                suggestion=suggestion,
            )
        )
    return tuple(findings)


_INDEX_ENTRY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "description": "条目内容"},
        "evidence": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
            "description": "引用哪些证据编号",
        },
    },
    "required": ["text", "evidence"],
    "additionalProperties": False,
}

#: 一处原文依据的 JSON Schema。发布类工具共用同一份：字段少一个就等于少一道校验。
_EVIDENCE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "digest": {"type": "string"},
        "start": {"type": "integer", "minimum": 0},
        "end": {"type": "integer", "minimum": 0},
        "quote": {"type": "string", "description": "≤160 字符"},
    },
    "required": ["path", "digest", "start", "end", "quote"],
    "additionalProperties": False,
}

#: 完整模块身份：库、模块、修订、条目。四段缺一不算引用。
_REF_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {"type": "string"},
    "minItems": 4,
    "maxItems": 4,
    "description": "库、模块、修订、条目四段 ID；不知道就省略，不要只写名字",
}

#: 资料整理每个域的结果条目结构；一次任务只装配对应域的那一份。
_MATERIAL_RECORD_SCHEMA: dict[str, dict[str, Any]] = {
    "story_index": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "这一章发生了什么，≤500 字符"},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "description": "这一条观察，≤300 字符"},
                        "mode": {
                            "type": "string",
                            "enum": [
                                "narration",
                                "testimony",
                                "recollection",
                                "hypothesis",
                                "dream",
                                "unknown",
                            ],
                            "description": "表达方式；回忆与证词不等于事实",
                        },
                        "story_time": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "原文的时间说法"},
                                "normalized": {
                                    "type": "string",
                                    "description": "只有原文明确对应日期时才写 YYYY-MM-DD",
                                },
                                "before": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "本事件在哪些观察之前",
                                },
                                "after": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "本事件在哪些观察之后",
                                },
                            },
                            "additionalProperties": False,
                        },
                        "speaker": _REF_SCHEMA,
                        "related": {"type": "array", "items": _REF_SCHEMA},
                        "author_event": _REF_SCHEMA,
                        "evidence": {"type": "array", "items": _EVIDENCE_SCHEMA},
                    },
                    "required": ["text", "mode", "evidence"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["summary", "events"],
        "additionalProperties": False,
    },
    "chapter_index": {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "这一章发生了什么，≤500 字符"},
            "evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer", "minimum": 1},
                        "start": {"type": "integer", "minimum": 0},
                        "end": {"type": "integer", "minimum": 0},
                        "quote": {"type": "string"},
                    },
                    "required": ["index", "start", "end", "quote"],
                    "additionalProperties": False,
                },
            },
            "events": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
            "characters": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
            "places": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
            "clues": {"type": "array", "items": _INDEX_ENTRY_SCHEMA},
        },
        "required": ["summary", "evidence"],
        "additionalProperties": False,
    },
    "timeline": {
        "type": "object",
        "properties": {
            "when": {"type": "string", "description": "原文里的时间说法"},
            "span": {"type": "string", "description": "这一章跨了多久"},
            "location": {"type": "string", "description": "主要地点"},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["when", "evidence"],
        "additionalProperties": False,
    },
    "clues": {
        "type": "object",
        "properties": {
            "clue": {"type": "string", "description": "读者能观察到的事实，不是解释"},
            "planted_chapter": {"type": "integer", "minimum": 1},
            "due_chapter": {"type": "integer", "minimum": 1},
            "status": {"type": "string", "enum": ["open", "resolved", "dropped"]},
            "supports": {"type": "string"},
            "resolution_chapter": {"type": "integer", "minimum": 1},
            "evidence": _EVIDENCE_SCHEMA,
            "resolution_evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["clue", "planted_chapter", "status", "evidence"],
        "additionalProperties": False,
    },
    "glossary": {
        "type": "object",
        "properties": {
            "term": {"type": "string"},
            "kind": {"type": "string", "enum": ["person", "place", "org", "item"]},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "first_chapter": {"type": "integer", "minimum": 1},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["term", "kind", "first_chapter", "evidence"],
        "additionalProperties": False,
    },
    "disclosure": {
        "type": "object",
        "properties": {
            "fact": {"type": "string", "description": "读者知道了什么"},
            "chapter": {"type": "integer", "minimum": 1},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["fact", "chapter", "evidence"],
        "additionalProperties": False,
    },
    "roster": {
        "type": "object",
        "properties": {
            "chapter": {"type": "integer", "minimum": 1},
            "location": {"type": "string"},
            "alive": {"type": "boolean"},
            "stance": {"type": "string"},
            "evidence": _EVIDENCE_SCHEMA,
        },
        "required": ["chapter", "alive", "evidence"],
        "additionalProperties": False,
    },
}


def _index_evidence(value: JSONValue) -> tuple[IndexEvidence, ...]:
    if not isinstance(value, list) or not value:
        raise _input_error("evidence", "必须是非空数组")
    items: list[IndexEvidence] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise _input_error("evidence", "每一项必须是对象")
        index = raw.get("index")
        start = raw.get("start")
        end = raw.get("end")
        quote = raw.get("quote")
        for name, item in (("index", index), ("start", start), ("end", end)):
            if isinstance(item, bool) or not isinstance(item, int):
                raise _input_error("evidence", f"{name} 必须是整数")
        if not isinstance(quote, str):
            raise _input_error("evidence", "quote 必须是字符串")
        items.append(
            IndexEvidence(
                index=cast(int, index),
                start=cast(int, start),
                end=cast(int, end),
                quote=quote,
            )
        )
    return tuple(items)


def _index_entries(value: JSONValue, kind: EntryKind) -> tuple[ChapterIndexEntry, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error(kind, "必须是数组")
    items: list[ChapterIndexEntry] = []
    for raw in value:
        if not isinstance(raw, dict):
            raise _input_error(kind, "每一项必须是对象")
        text = raw.get("text")
        evidence = raw.get("evidence")
        if not isinstance(text, str) or not text:
            raise _input_error(kind, "每一项都要有非空 text")
        if not isinstance(evidence, list) or not evidence:
            raise _input_error(kind, "每一项都要引用证据编号")
        if not all(isinstance(index, int) and not isinstance(index, bool) for index in evidence):
            raise _input_error(kind, "evidence 只能包含整数编号")
        items.append(
            ChapterIndexEntry(
                kind=kind,
                text=text,
                evidence=tuple(cast(list[int], evidence)),
            )
        )
    return tuple(items)


def _optional_int_argument(
    arguments: Mapping[str, JSONValue], name: str, *, minimum: int
) -> int | None:
    """可选整数参数：缺省返回 ``None``，给出时按同一套规则校验。"""
    value = arguments.get(name)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise _input_error(name, "必须是整数")
    if value < minimum:
        raise _input_error(name, f"必须 ≥ {minimum}")
    return value


def _digest_argument(arguments: Mapping[str, JSONValue], *, required: bool = False) -> str | None:
    value = arguments.get("digest")
    if value is None:
        if required:
            raise _input_error("digest", "必须提供 read_file 得到的 digest")
        return None
    if not isinstance(value, str) or not value:
        raise _input_error("digest", "必须是非空字符串")
    return value


def _required_digest(arguments: Mapping[str, JSONValue]) -> str:
    value = _digest_argument(arguments, required=True)
    assert value is not None
    return value


def _optional_text_argument(arguments: Mapping[str, JSONValue], name: str) -> str:
    value = arguments.get(name)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise _input_error(name, "必须是字符串")
    return value


def _string_list_argument(arguments: Mapping[str, JSONValue], name: str) -> tuple[str, ...]:
    value = arguments.get(name)
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error(name, "必须是字符串数组")
    items: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise _input_error(name, "只能包含非空字符串")
        items.append(item)
    return tuple(items)


def _action_list_argument(
    arguments: Mapping[str, JSONValue], name: str
) -> tuple[MutationAction, ...]:
    actions: list[MutationAction] = []
    for item in _string_list_argument(arguments, name):
        if item not in ("create", "update", "delete", "move"):
            raise _input_error(name, f"包含不支持的操作：{item}")
        actions.append(cast(MutationAction, item))
    return tuple(actions)


def _mutation_arguments(value: JSONValue) -> tuple[Mutation, ...]:
    """把工具参数整理成变更集合；结构错误在调用应用层之前就拒绝。"""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error("grants", "必须是数组")
    grants: list[Mutation] = []
    for item in value:
        if not isinstance(item, dict):
            raise _input_error("grants", "每一项必须是对象")
        action = item.get("action")
        path = item.get("path")
        target = item.get("target")
        if action not in ("create", "update", "delete", "move"):
            raise _input_error("grants", f"包含不支持的操作：{action}")
        if not isinstance(path, str) or not path:
            raise _input_error("grants", "每一项都要有非空 path")
        if target is not None and (not isinstance(target, str) or not target):
            raise _input_error("grants", "target 必须是非空字符串")
        grants.append(Mutation(action=cast(MutationAction, action), path=path, target=target))
    return tuple(grants)


def _module_grant_arguments(value: JSONValue) -> tuple[ModuleGrant, ...]:
    """装配操作参数；装入要模块与版本，卸下要绑定 ID。"""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error("module_grants", "必须是数组")
    grants: list[ModuleGrant] = []
    for item in value:
        if not isinstance(item, dict):
            raise _input_error("module_grants", "每一项必须是对象")
        action = item.get("action")
        if action not in ("attach", "detach"):
            raise _input_error("module_grants", f"包含不支持的操作：{action}")
        module_id = item.get("module_id", "")
        revision_id = item.get("revision_id", "")
        binding_id = item.get("binding_id", "")
        if not (
            isinstance(module_id, str)
            and isinstance(revision_id, str)
            and isinstance(binding_id, str)
        ):
            raise _input_error("module_grants", "module_id、revision_id 与 binding_id 必须是字符串")
        grants.append(
            ModuleGrant(
                action=cast(ModuleAction, action),
                module_id=module_id,
                revision_id=revision_id,
                binding_id=binding_id,
            )
        )
    return tuple(grants)


def _module_content_grant_arguments(value: JSONValue) -> tuple[ModuleContentGrant, ...]:
    """内容操作参数：新建要稳定槽位，修改要精确到绑定、模块与基础版本。"""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise _input_error("module_content_grants", "必须是数组")
    grants: list[ModuleContentGrant] = []
    for item in value:
        if not isinstance(item, dict):
            raise _input_error("module_content_grants", "每一项必须是对象")
        action = item.get("action")
        if action not in ("create", "update"):
            raise _input_error("module_content_grants", f"包含不支持的操作：{action}")
        slot = item.get("slot", "")
        purpose = item.get("purpose", "")
        binding_id = item.get("binding_id", "")
        module_id = item.get("module_id", "")
        base_revision = item.get("base_revision", "")
        if not (
            isinstance(slot, str)
            and isinstance(purpose, str)
            and isinstance(binding_id, str)
            and isinstance(module_id, str)
            and isinstance(base_revision, str)
        ):
            raise _input_error("module_content_grants", "各字段必须是字符串")
        grants.append(
            ModuleContentGrant(
                action=cast(Any, action),
                slot=slot,
                purpose=purpose,
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision,
            )
        )
    return tuple(grants)


def _change_operations_argument(value: JSONValue) -> tuple[ChangeOperation, ...]:
    """把变更集工具参数整理成操作；缺字段或类型不对时在服务调用前拒绝。"""
    if not isinstance(value, list) or not value:
        raise _input_error("operations", "必须是非空数组")
    operations: list[ChangeOperation] = []
    for item in value:
        if not isinstance(item, dict):
            raise _input_error("operations", "每一项必须是对象")
        operation_id = item.get("operation_id")
        action = item.get("action")
        if not isinstance(operation_id, str) or not operation_id:
            raise _input_error("operations", "每一项都要有非空 operation_id")
        if action not in ("create", "update"):
            raise _input_error("operations", f"包含不支持的操作：{action}")
        name = item.get("name", "")
        body = item.get("body", "")
        if not isinstance(name, str) or not isinstance(body, str):
            raise _input_error("operations", "name 与 body 必须是字符串")
        if action == "create":
            operations.append(
                ChangeOperation(
                    operation_id=operation_id,
                    action="create",
                    name=name,
                    body=body,
                )
            )
            continue
        binding_id = item.get("binding_id", "")
        module_id = item.get("module_id", "")
        base_revision = item.get("base_revision", "")
        if not (
            isinstance(binding_id, str)
            and binding_id
            and isinstance(module_id, str)
            and module_id
            and isinstance(base_revision, str)
            and base_revision
        ):
            raise _input_error(
                "operations", "修改操作要给出 binding_id、module_id 与 base_revision"
            )
        raw_edits = item.get("edits")
        if not isinstance(raw_edits, list) or not raw_edits:
            raise _input_error("operations", "修改操作要给出非空 edits")
        edits: list[ChangeEdit] = []
        for raw in raw_edits:
            if not isinstance(raw, dict):
                raise _input_error("edits", "每一项必须是对象")
            entry_id = raw.get("entry_id")
            base_digest = raw.get("base_digest")
            find = raw.get("find", "")
            replace = raw.get("replace", "")
            rewrite = raw.get("body")
            if not isinstance(entry_id, str) or not entry_id:
                raise _input_error("edits", "每一项都要有非空 entry_id")
            if not isinstance(base_digest, str) or not base_digest:
                raise _input_error("edits", "每一项都要有读取时拿到的 base_digest")
            if not isinstance(find, str) or not isinstance(replace, str):
                raise _input_error("edits", "find 与 replace 必须是字符串")
            if rewrite is not None and not isinstance(rewrite, str):
                raise _input_error("edits", "body 必须是字符串")
            edits.append(
                ChangeEdit(
                    entry_id=entry_id,
                    base_digest=base_digest,
                    find=find,
                    replace=replace,
                    body=rewrite,
                )
            )
        operations.append(
            ChangeOperation(
                operation_id=operation_id,
                action="update",
                name=name,
                binding_id=binding_id,
                module_id=module_id,
                base_revision=base_revision,
                edits=tuple(edits),
            )
        )
    return tuple(operations)


def _change_targets(operations: Sequence[ChangeOperation]) -> tuple[ModuleChangeTarget, ...]:
    return tuple(
        ModuleChangeTarget(
            operation_id=operation.operation_id,
            action=operation.action,
            binding_id=operation.binding_id,
            module_id=operation.module_id,
            base_revision=operation.base_revision,
        )
        for operation in operations
    )


def _targets_from_view(value: object) -> tuple[ModuleChangeTarget, ...]:
    """从已持久化的变更集视图恢复操作目标，供应用前的范围复核。"""
    if not isinstance(value, list):
        raise _input_error("change_set_id", "变更集缺少操作清单")
    targets: list[ModuleChangeTarget] = []
    for row in value:
        if not isinstance(row, dict):
            continue
        action = row.get("action")
        operation_id = row.get("operation_id")
        if action not in ("create", "update") or not isinstance(operation_id, str):
            continue
        targets.append(
            ModuleChangeTarget(
                operation_id=operation_id,
                action=cast(Any, action),
                binding_id=str(row.get("binding_id") or ""),
                module_id=str(row.get("module_id") or ""),
                base_revision=str(row.get("base_revision") or ""),
            )
        )
    return tuple(targets)


def _hashed_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"{prefix}-{digest[:40]}"


def _entry_drafts_argument(value: JSONValue) -> list[EntryDraft]:
    """库发布的内容由模型给出，但结构在这里先校验；类型与名称留给库服务。"""
    if not isinstance(value, list) or not value:
        raise _input_error("entries", "必须是非空数组")
    drafts: list[EntryDraft] = []
    for item in value:
        if not isinstance(item, dict):
            raise _input_error("entries", "每一条必须是对象")
        entry_id = item.get("entry_id")
        kind = item.get("kind")
        title = item.get("title")
        if not isinstance(entry_id, str) or not entry_id:
            raise _input_error("entries", "每一条都要有非空 entry_id")
        if not isinstance(kind, str) or not kind:
            raise _input_error("entries", "每一条都要有非空 kind")
        if not isinstance(title, str) or not title:
            raise _input_error("entries", "每一条都要有非空 title")
        body = item.get("body", "")
        if not isinstance(body, str):
            raise _input_error("entries", "body 必须是字符串")
        aliases_value = item.get("aliases", [])
        if not isinstance(aliases_value, list) or not all(
            isinstance(alias, str) for alias in aliases_value
        ):
            raise _input_error("entries", "aliases 必须是字符串数组")
        fields_value = item.get("fields", {})
        if not isinstance(fields_value, dict):
            raise _input_error("entries", "fields 必须是对象")
        drafts.append(
            EntryDraft(
                entry_id=entry_id,
                kind=kind,
                title=title,
                body=body,
                aliases=tuple(cast(list[str], aliases_value)),
                fields=cast(dict[str, Any], fields_value),
            )
        )
    return drafts


def _input_error(name: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="TOOL_INPUT_INVALID",
        where=f"tools.{name}",
        why=f"参数 {name} {why}",
        fix="按工具说明修正参数后重试",
    )

"""直接修改文件的写作 Agent（0.4 M03）。

一个 Agent，一组通用文件工具，没有规划者/写作者/审阅者的固定多角色流程。工具只在
文件层之上做两件事：把参数整理成可理解的错误，以及在第一次真正写文件之前通知记录层
准备 Git 基线——读操作永远不会触发提交。

工具成功只表示「已经写到磁盘」：``write_file`` / ``edit_file`` 返回的 ``digest`` 是
落盘后的实际指纹，模型可以据此继续改。指纹不匹配、路径不受管、文件不存在都按失败
返回给模型，由它重新读取，而不是由应用替它猜。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, cast

from hastings.workspace.assembly import AssembledContext, Profile
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
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.glossary import (
    GlossaryStore,
    GlossaryTerm,
    build_glossary,
    glossary_view,
    publish_terms,
)
from hastings.workspace.materials import (
    LIST_RESULT_CHARS,
    READ_MAX_CHARS,
    READ_RESULT_CHARS,
    SEARCH_MAX_HITS,
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
from hastings.workspace.task_scope import (
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

#: 工具集合：普通写作、索引任务，或只读检查。
ToolMode = Literal["write", "index", "review", "state"]

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
        self.scope.require(operation)
        if preflight is not None:
            preflight()
        self.materials.require_target_read(self.workspace, required_read)
        self.materials.require_references_complete(self.workspace)
        self.materials.verify_current(self.workspace)
        if not self.session.prepared:
            self.session.prepare()
        # Git 准备期间可能有外部程序改动文件：落盘前再复核一次依赖版本。
        self.materials.verify_current(self.workspace)


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
) -> list[AgentTool]:
    """本轮可用的文件工具；Git 提交与撤销不暴露给模型。

    索引任务只开放目录、搜索、读取与 ``save_chapter_index``：它能保存索引缓存，
    但不能改作品正文，也不准备 Git 基线。
    """
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
        del tool_call_id, signal, on_update
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
        run.materials.after_full_write(view)
        return _json({"path": view.path, "digest": view.digest, "size": view.size})

    async def edit_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
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
        del tool_call_id, signal, on_update
        source = _text_argument(arguments, "source")
        target = _text_argument(arguments, "target")
        digest = _required_digest(arguments)
        run.before_mutation(
            Mutation(action="move", path=source, target=target),
            required_read=ReadRequirement.identity(source),
            preflight=lambda: workspace.check_move_intent(source, target, expected_digest=digest),
        )
        view = workspace.move(source, target, expected_digest=digest)
        run.materials.after_move(SourceRef(source, digest), view)
        return _json({"path": view.path, "digest": view.digest})

    async def delete_file(
        tool_call_id: str,
        arguments: Mapping[str, JSONValue],
        signal: ToolCancellationToken | None = None,
        on_update: ToolUpdateCallback | None = None,
    ) -> AgentToolResult:
        del tool_call_id, signal, on_update
        path = _text_argument(arguments, "path")
        digest = _required_digest(arguments)
        run.before_mutation(
            Mutation(action="delete", path=path),
            required_read=ReadRequirement.identity(path),
            preflight=lambda: workspace.check_delete_intent(path, expected_digest=digest),
        )
        workspace.delete(path, expected_digest=digest)
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

    if mode == "review":
        # 检查任务：只读原文与搜索，加保存报告；没有作品写工具。
        readonly = {"list_files", "read_file", "search_text"}
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
    return tools


def build_turn_assembly(
    workspace: Workspace,
    *,
    mode: ToolMode = "write",
    selected_path: str | None = None,
    history_facts: str = "",
    materials: str = "",
    index_notes: str = "",
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
        index_notes=index_notes,
        instructions=_instructions_for(mode),
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
            ),
            max_turns=max_turns,
        ),
        messages=history,
    )


_PROFILE_BY_MODE: dict[ToolMode, Profile] = {
    "write": "writer",
    "index": "extract",
    # 核对任务与建索引同类：只读原文、发布派生记录，不注入文风与旧摘要。
    "state": "extract",
    "review": "review",
}


def _instructions_for(mode: ToolMode) -> str:
    if mode == "index":
        return INDEX_INSTRUCTIONS
    if mode == "state":
        return STATE_INSTRUCTIONS
    if mode == "review":
        return REVIEW_INSTRUCTIONS
    return ""


def _json(payload: Mapping[str, Any]) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=json.dumps(payload, ensure_ascii=False, indent=2))],
        details=dict(payload),
    )


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


def _input_error(name: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code="TOOL_INPUT_INVALID",
        where=f"tools.{name}",
        why=f"参数 {name} {why}",
        fix="按工具说明修正参数后重试",
    )

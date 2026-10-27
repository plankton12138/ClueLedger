"""本轮修改范围（0.6 V06-B1）。

契约：没有登记范围的写工具一律拒绝；范围只接受确切路径，材料选择不产生授权；
固定范围不能自行扩大，可发现范围只能追加具体文件与已允许的操作类别；旧版本不能
覆盖新版本。拒绝时不能留下文件或 Git 副作用。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.task_scope import (
    ModuleGrant,
    Mutation,
    ScopeController,
    ScopeDraft,
    WriteScope,
)
from tau_agent.tools import AgentTool

_AUTHOR = ((1, "只改第三章，不动人物设定。"),)
_CHAPTER = "正文/0003 书房里的脚印.md"
_PROFILE = "设定集/角色小传/林远.md"
_TRUTH = "设定集/真相.md"


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


def _edit(*grants: Mutation, **kwargs: Any) -> ScopeDraft:
    return ScopeDraft(
        mode="edit",
        grants=grants,
        summary="改写第三章的问询",
        authorization="只改第三章",
        **kwargs,
    )


# -- 校验视图 ---------------------------------------------------------------


def test_permits_requires_matching_action_and_path() -> None:
    scope = WriteScope(
        mode="edit",
        grants=frozenset({Mutation("update", _CHAPTER)}),
        denied_paths=frozenset(),
    )
    assert scope.permits(Mutation("update", _CHAPTER)) is True
    assert scope.permits(Mutation("delete", _CHAPTER)) is False
    assert scope.permits(Mutation("update", "正文/0004 别章.md")) is False


def test_permits_checks_both_ends_of_a_move() -> None:
    scope = WriteScope(
        mode="edit",
        grants=frozenset({Mutation("move", _CHAPTER, "正文/0004 新名.md")}),
        denied_paths=frozenset({"设定集/真相.md"}),
    )
    assert scope.permits(Mutation("move", _CHAPTER, "正文/0004 新名.md")) is True
    assert scope.permits(Mutation("move", _CHAPTER, "设定集/真相.md")) is False


def test_read_only_scope_permits_nothing() -> None:
    scope = WriteScope(mode="read_only", grants=frozenset(), denied_paths=frozenset())
    assert scope.permits(Mutation("create", _CHAPTER)) is False


# -- 登记校验 ---------------------------------------------------------------


def test_edit_scope_needs_a_grant() -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as missing:
        controller.set_scope(
            ScopeDraft(mode="edit", summary="改写", authorization="只改第三章"),
            author_texts=_AUTHOR,
        )
    assert _code(missing) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_read_only_scope_rejects_grants() -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as invalid:
        controller.set_scope(
            ScopeDraft(
                mode="read_only",
                grants=(Mutation("update", _CHAPTER),),
                summary="检查第三章",
                authorization="只改第三章",
            ),
            author_texts=_AUTHOR,
        )
    assert _code(invalid) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_module_grant_requires_an_explicit_instruction() -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as denied:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                summary="装入模块",
                authorization="只改第三章",
                module_grants=(ModuleGrant("attach", "mod_1", "rev_1"),),
            ),
            author_texts=_AUTHOR,
        )
    assert _code(denied) == "TASK_SCOPE_EVIDENCE_INVALID"

    with pytest.raises(WorkspaceError) as incomplete:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                summary="装入模块",
                authorization="把设定模块装进这个作品",
                module_grants=(ModuleGrant("attach", "mod_1"),),
            ),
            author_texts=((1, "把设定模块装进这个作品。"),),
        )
    assert _code(incomplete) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_module_grants_are_reported_and_cannot_expand_on_reregistration() -> None:
    controller = ScopeController()
    author = ((1, "把设定模块装进这个作品。"),)
    grant = ModuleGrant("attach", "mod_1", "rev_1")

    first = controller.set_scope(
        ScopeDraft(
            mode="edit",
            summary="装入模块",
            authorization="把设定模块装进这个作品",
            module_grants=(grant,),
        ),
        author_texts=author,
    )
    assert first.to_dict()["module_grants"] == [
        {"action": "attach", "module_id": "mod_1", "revision_id": "rev_1"}
    ]

    with pytest.raises(WorkspaceError) as expanded:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                summary="再装一个",
                authorization="把设定模块装进这个作品",
                module_grants=(grant, ModuleGrant("attach", "mod_2", "rev_2")),
            ),
            author_texts=author,
        )
    assert _code(expanded) == "TASK_SCOPE_DENIED_CONFLICT"


def test_read_only_rejects_module_grants_and_publish() -> None:
    controller = ScopeController()
    for draft in (
        ScopeDraft(
            mode="read_only",
            summary="只看装配",
            authorization="把设定模块装进这个作品",
            module_grants=(ModuleGrant("attach", "mod_1", "rev_1"),),
        ),
        ScopeDraft(
            mode="read_only",
            summary="只看库",
            authorization="把设定发布到模块库",
            library_publish=True,
        ),
    ):
        with pytest.raises(WorkspaceError) as invalid:
            controller.set_scope(draft, author_texts=((1, draft.authorization),))
        assert _code(invalid) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_scope_rejects_non_managed_and_wildcard_paths() -> None:
    controller = ScopeController()
    for path in ("正文", "正文/*.md", "../x.md", "设定集/时间线.md"):
        with pytest.raises(WorkspaceError) as invalid:
            controller.set_scope(
                _edit(Mutation("update", path)),
                author_texts=_AUTHOR,
            )
        assert _code(invalid) == "TASK_SCOPE_DENIED_CONFLICT"


def test_scope_rejects_target_on_non_move_and_move_without_target() -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as wrong_target:
        controller.set_scope(
            _edit(Mutation("update", _CHAPTER, "正文/0004 别章.md")), author_texts=_AUTHOR
        )
    assert _code(wrong_target) == "TASK_SCOPE_EVIDENCE_INVALID"

    with pytest.raises(WorkspaceError) as no_target:
        controller.set_scope(_edit(Mutation("move", _CHAPTER)), author_texts=_AUTHOR)
    assert _code(no_target) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_authorization_quote_must_come_from_author_message() -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as invented:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _CHAPTER),),
                summary="改写第三章",
                authorization="模型自己认为可以改",
            ),
            author_texts=_AUTHOR,
        )
    assert _code(invented) == "TASK_SCOPE_EVIDENCE_INVALID"

    with pytest.raises(WorkspaceError) as no_messages:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _CHAPTER),),
                summary="改写第三章",
                authorization="只改第三章",
            ),
            author_texts=(),
        )
    assert _code(no_messages) == "TASK_SCOPE_EVIDENCE_INVALID"


def test_authorization_seq_must_match_a_visible_message_and_hints_the_seqs() -> None:
    controller = ScopeController()

    with pytest.raises(WorkspaceError) as unknown_seq:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _CHAPTER),),
                summary="改写第三章",
                authorization="只改第三章",
                authorization_seq=7,
            ),
            author_texts=_AUTHOR,
        )

    assert _code(unknown_seq) == "TASK_SCOPE_EVIDENCE_INVALID"
    # 引文已能定位消息；提示要给出可用的 seq 并允许省略，否则模型只能继续猜。
    assert "1" in unknown_seq.value.fix
    assert "省略" in unknown_seq.value.fix


# -- 重复登记 ---------------------------------------------------------------


def test_reregistration_cannot_widen_the_scope() -> None:
    """先登记「只改第三章、禁止改真相」，再次登记不能直接换成改真相。"""
    authors = ((1, "只改第三章，真相不要动。"),)
    controller = ScopeController()
    controller.set_scope(
        ScopeDraft(
            mode="edit",
            grants=(Mutation("update", _CHAPTER),),
            denied_paths=(_TRUTH,),
            summary="改写第三章的问询",
            authorization="只改第三章",
        ),
        author_texts=authors,
    )

    with pytest.raises(WorkspaceError) as widened:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _TRUTH),),
                summary="改写真相",
                authorization="只改第三章",
            ),
            author_texts=authors,
        )
    assert _code(widened) == "TASK_SCOPE_DENIED_CONFLICT"
    assert widened.value.details["added"] == [{"action": "update", "path": _TRUTH}]
    assert controller.current is not None
    assert controller.current.version == 1
    with pytest.raises(WorkspaceError) as denied:
        controller.require(Mutation("update", _TRUTH))
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"


def test_reregistration_cannot_drop_denied_paths() -> None:
    """去掉已登记的禁止项同样是扩大范围。"""
    controller = ScopeController()
    controller.set_scope(
        _edit(Mutation("update", _CHAPTER), denied_paths=(_TRUTH,)), author_texts=_AUTHOR
    )

    with pytest.raises(WorkspaceError) as dropped:
        controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)
    assert _code(dropped) == "TASK_SCOPE_DENIED_CONFLICT"
    assert dropped.value.details["removed_denied_paths"] == [_TRUTH]
    with pytest.raises(WorkspaceError) as denied:
        controller.require(Mutation("update", _TRUTH))
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"


def test_reregistration_cannot_switch_a_fixed_scope_to_extendable() -> None:
    """固定范围改登记成可追加，等于换一条路绕过固定范围。"""
    controller = ScopeController()
    controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)

    with pytest.raises(WorkspaceError) as opened:
        controller.set_scope(
            _edit(Mutation("update", _CHAPTER), extendable=True, extend_actions=("update",)),
            author_texts=_AUTHOR,
        )
    assert _code(opened) == "TASK_SCOPE_DENIED_CONFLICT"
    with pytest.raises(WorkspaceError) as fixed:
        controller.extend(
            version=1,
            grants=(Mutation("update", "正文/0004 别章.md"),),
            evidence_path="正文/0004 别章.md",
            evidence="相关章节",
            author_texts=_AUTHOR,
        )
    assert _code(fixed) == "TASK_SCOPE_DENIED_CONFLICT"


def test_reregistration_cannot_widen_extend_actions() -> None:
    controller = ScopeController()
    controller.set_scope(
        _edit(Mutation("update", _CHAPTER), extendable=True, extend_actions=("update",)),
        author_texts=_AUTHOR,
    )
    with pytest.raises(WorkspaceError) as actions:
        controller.set_scope(
            _edit(
                Mutation("update", _CHAPTER),
                extendable=True,
                extend_actions=("update", "delete"),
            ),
            author_texts=_AUTHOR,
        )
    assert _code(actions) == "TASK_SCOPE_DENIED_CONFLICT"


def test_reregistration_can_narrow_the_scope() -> None:
    """收窄可以直接接受：去掉路径、补充禁止项、改成只读。"""
    other = "正文/0004 书房外的脚印.md"
    controller = ScopeController()
    controller.set_scope(
        _edit(Mutation("update", _CHAPTER), Mutation("update", other)), author_texts=_AUTHOR
    )

    narrowed = controller.set_scope(
        _edit(Mutation("update", _CHAPTER), denied_paths=(_TRUTH,)), author_texts=_AUTHOR
    )
    assert narrowed.version == 2
    controller.require(Mutation("update", _CHAPTER))
    with pytest.raises(WorkspaceError) as outside:
        controller.require(Mutation("update", other))
    assert _code(outside) == "TASK_SCOPE_DENIED_CONFLICT"

    read_only = controller.set_scope(
        ScopeDraft(mode="read_only", summary="只讨论这一章", authorization="只改第三章"),
        author_texts=_AUTHOR,
    )
    assert read_only.version == 3
    with pytest.raises(WorkspaceError) as stopped:
        controller.require(Mutation("update", _CHAPTER))
    assert _code(stopped) == "TASK_SCOPE_DENIED_CONFLICT"


# -- 操作检查 ---------------------------------------------------------------


def test_require_without_scope_is_rejected() -> None:
    with pytest.raises(WorkspaceError) as required:
        ScopeController().require(Mutation("update", _CHAPTER))
    assert _code(required) == "TASK_SCOPE_REQUIRED_CONFLICT"


def test_require_reports_denied_paths_separately() -> None:
    controller = ScopeController()
    controller.set_scope(
        _edit(Mutation("update", _CHAPTER), denied_paths=(_PROFILE,)), author_texts=_AUTHOR
    )
    with pytest.raises(WorkspaceError) as denied:
        controller.require(Mutation("update", _PROFILE))
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"
    assert denied.value.details["denied_paths"] == [_PROFILE]


def test_require_rejects_actions_outside_the_scope() -> None:
    controller = ScopeController()
    controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)

    controller.require(Mutation("update", _CHAPTER))
    with pytest.raises(WorkspaceError) as deletion:
        controller.require(Mutation("delete", _CHAPTER))
    assert _code(deletion) == "TASK_SCOPE_DENIED_CONFLICT"
    # 「修改」不默认包含删除：提示里要能看到实际登记的操作。
    assert deletion.value.details["grants"] == [{"action": "update", "path": _CHAPTER}]


def test_versions_advance_and_old_versions_cannot_extend() -> None:
    controller = ScopeController()
    first = controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)
    second = controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)
    assert (first.version, second.version) == (1, 2)

    with pytest.raises(WorkspaceError) as stale:
        controller.extend(
            version=1,
            grants=(Mutation("update", "正文/0004 别章.md"),),
            evidence_path=_CHAPTER,
            evidence="第三章提到了这次问询",
            author_texts=_AUTHOR,
        )
    assert _code(stale) == "TASK_SCOPE_STALE_CONFLICT"


def test_fixed_scope_cannot_be_extended() -> None:
    controller = ScopeController()
    controller.set_scope(_edit(Mutation("update", _CHAPTER)), author_texts=_AUTHOR)
    with pytest.raises(WorkspaceError) as fixed:
        controller.extend(
            version=1,
            grants=(Mutation("update", "正文/0004 别章.md"),),
            evidence_path=_CHAPTER,
            evidence="相关章节",
            author_texts=_AUTHOR,
        )
    assert _code(fixed) == "TASK_SCOPE_DENIED_CONFLICT"


def test_extend_adds_paths_but_keeps_limits() -> None:
    controller = ScopeController()
    controller.set_scope(
        _edit(
            Mutation("update", _CHAPTER),
            denied_paths=(_PROFILE,),
            extendable=True,
            extend_actions=("update",),
        ),
        author_texts=_AUTHOR,
    )

    extended = controller.extend(
        version=1,
        grants=(Mutation("update", "正文/0004 书房外的脚印.md"),),
        evidence_path="正文/0004 书房外的脚印.md",
        evidence="第四章开头复述了这次问询",
        author_texts=_AUTHOR,
    )
    assert extended.version == 2
    controller.require(Mutation("update", "正文/0004 书房外的脚印.md"))

    # 原禁止项与操作类别限制在追加后继续有效。
    with pytest.raises(WorkspaceError) as denied:
        controller.require(Mutation("update", _PROFILE))
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"
    with pytest.raises(WorkspaceError) as wrong_action:
        controller.extend(
            version=2,
            grants=(Mutation("delete", "正文/0004 书房外的脚印.md"),),
            evidence_path="正文/0004 书房外的脚印.md",
            evidence="顺便删掉",
            author_texts=_AUTHOR,
        )
    assert _code(wrong_action) == "TASK_SCOPE_DENIED_CONFLICT"


def test_extend_requires_evidence() -> None:
    controller = ScopeController()
    controller.set_scope(
        _edit(Mutation("update", _CHAPTER), extendable=True, extend_actions=("update",)),
        author_texts=_AUTHOR,
    )
    with pytest.raises(WorkspaceError) as no_evidence:
        controller.extend(
            version=1,
            grants=(Mutation("update", "正文/0004 别章.md"),),
            evidence_path="正文/0004 别章.md",
            evidence="   ",
            author_texts=_AUTHOR,
        )
    assert _code(no_evidence) == "TASK_SCOPE_EVIDENCE_INVALID"


# -- 工具层接线 -------------------------------------------------------------


def _tools(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], RunContext]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="只改第三章"),
        author_messages=_AUTHOR,
    )
    return workspace, {tool.name: tool for tool in build_tools(workspace, run)}, run


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


@pytest.mark.anyio
async def test_write_without_scope_leaves_no_file(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    before = GitRepo(workspace.root).head()

    with pytest.raises(WorkspaceError) as required:
        await _call(tools["write_file"], path=_CHAPTER, text="# 书房\n")
    assert _code(required) == "TASK_SCOPE_REQUIRED_CONFLICT"
    assert not (workspace.root / _CHAPTER).exists()
    assert run.session.prepared is False
    assert GitRepo(workspace.root).head() == before


@pytest.mark.anyio
async def test_scope_then_write_succeeds(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    registered = await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "create", "path": _CHAPTER}],
    )
    assert registered["version"] == 1

    written = await _call(tools["write_file"], path=_CHAPTER, text="# 书房\n\n问询。\n")
    assert written["digest"]
    assert (workspace.root / _CHAPTER).is_file()


@pytest.mark.anyio
async def test_scope_blocks_denied_and_out_of_range_paths(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    workspace.write(_PROFILE, "# 林远\n", expected_digest=None)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "create", "path": _CHAPTER}],
        denied_paths=[_PROFILE],
    )

    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["write_file"], path=_PROFILE, text="# 林远改\n", digest="0" * 64)
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"
    assert (workspace.root / _PROFILE).read_text(encoding="utf-8") == "# 林远\n"

    with pytest.raises(WorkspaceError) as outside:
        await _call(tools["write_file"], path="正文/0004 别章.md", text="# 别章\n")
    assert _code(outside) == "TASK_SCOPE_DENIED_CONFLICT"


@pytest.mark.anyio
async def test_tool_rejects_bad_grants_shape(tmp_path: Path) -> None:
    _, tools, _ = _tools(tmp_path)
    with pytest.raises(WorkspaceError) as invalid:
        await _call(
            tools["set_task_scope"],
            mode="edit",
            summary="改写",
            authorization="只改第三章",
            grants=[{"action": "rewrite", "path": _CHAPTER}],
        )
    assert _code(invalid) == "TOOL_INPUT_INVALID"


@pytest.mark.anyio
async def test_tool_treats_blank_target_as_absent(tmp_path: Path) -> None:
    """可选字段给空串按没填处理：模型删掉值却留下字段时不该反复被拒绝。"""
    _, tools, _ = _tools(tmp_path)

    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "update", "path": _CHAPTER, "target": ""}],
    )


@pytest.mark.anyio
async def test_tool_resolves_authorization_seq_from_the_quote(tmp_path: Path) -> None:
    """模型看不到事件序号：填错的序号按引文落到实际作者消息，不让整轮白跑。"""
    _, tools, run = _tools(tmp_path)

    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        authorization_seq=7,
        grants=[{"action": "update", "path": _CHAPTER}],
    )

    assert run.scope.current is not None
    assert run.scope.current.draft.authorization_seq == 1


@pytest.mark.anyio
async def test_tool_reregistration_cannot_bypass_a_fixed_scope(tmp_path: Path) -> None:
    """复现路径：登记固定范围后再次调用 set_task_scope 换范围。"""
    workspace, tools, _ = _tools(tmp_path)
    workspace.write(_TRUTH, "# 真相\n\n凶手是管家。\n", expected_digest=None)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "create", "path": _CHAPTER}],
        denied_paths=[_TRUTH],
    )

    with pytest.raises(WorkspaceError) as widened:
        await _call(
            tools["set_task_scope"],
            mode="edit",
            summary="改写真相",
            authorization="只改第三章",
            grants=[{"action": "update", "path": _TRUTH}],
        )
    assert _code(widened) == "TASK_SCOPE_DENIED_CONFLICT"
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["write_file"], path=_TRUTH, text="# 真相改\n", digest="0" * 64)
    assert _code(denied) == "TASK_SCOPE_DENIED_CONFLICT"
    assert (workspace.root / _TRUTH).read_text(encoding="utf-8") == "# 真相\n\n凶手是管家。\n"


# -- 事件与承接 -------------------------------------------------------------


@pytest.mark.anyio
async def test_scope_tools_write_events(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    events: list[tuple[str, dict[str, Any]]] = []
    run.emit = lambda kind, payload: events.append((kind, payload))
    chapter = "正文/0004 书房外的脚印.md"
    workspace.write(chapter, "# 书房外的脚印\n\n第四章复述了这次问询。\n", expected_digest=None)

    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "update", "path": _CHAPTER}],
        extendable=True,
        extend_actions=["update"],
    )
    await _call(tools["read_file"], path=chapter)
    await _call(
        tools["extend_task_scope"],
        version=1,
        grants=[{"action": "update", "path": chapter}],
        evidence_path=chapter,
        evidence="第四章复述了这次问询。",
    )

    assert [kind for kind, _ in events if kind.startswith("task_scope")] == [
        "task_scope_set",
        "task_scope_extended",
    ]
    first = next(payload for kind, payload in events if kind == "task_scope_set")
    assert first["version"] == 1
    assert first["grants"] == [{"action": "update", "path": _CHAPTER}]
    assert first["authorization"] == "只改第三章"
    # 登记事件不写空的依据字段。
    assert "evidence_path" not in first
    second = next(payload for kind, payload in events if kind == "task_scope_extended")
    assert second["version"] == 2
    assert [item["path"] for item in second["grants"]] == [_CHAPTER, chapter]
    assert second["evidence_path"] == chapter
    assert second["evidence"] == "第四章复述了这次问询。"


@pytest.mark.anyio
async def test_extend_requires_evidence_from_a_read_range(tmp_path: Path) -> None:
    """关联证据不能凭印象写：必须来自本轮读过、且落在已提供区间里的原文。"""
    workspace, tools, run = _tools(tmp_path)
    chapter = "正文/0004 书房外的脚印.md"
    workspace.write(chapter, "# 书房外的脚印\n\n雪地上有两行脚印。\n", expected_digest=None)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写第三章的问询",
        authorization="只改第三章",
        grants=[{"action": "update", "path": _CHAPTER}],
        extendable=True,
        extend_actions=["update"],
    )

    # 一份文件都没读过，随手写个理由。
    with pytest.raises(WorkspaceError) as unread:
        await _call(
            tools["extend_task_scope"],
            version=1,
            grants=[{"action": "update", "path": chapter}],
            evidence_path=chapter,
            evidence="第四章和这一章有关",
        )
    assert _code(unread) == "REFERENCE_EVIDENCE_INVALID"
    assert run.scope.current is not None and run.scope.current.version == 1

    # 只读了标题，却引用下面一段。
    await _call(tools["read_file"], path=chapter, limit=1)
    with pytest.raises(WorkspaceError) as outside_range:
        await _call(
            tools["extend_task_scope"],
            version=1,
            grants=[{"action": "update", "path": chapter}],
            evidence_path=chapter,
            evidence="雪地上有两行脚印。",
        )
    assert _code(outside_range) == "REFERENCE_EVIDENCE_INVALID"

    # 读完再引用同一句，才允许追加。
    await _call(tools["read_file"], path=chapter)
    extended = await _call(
        tools["extend_task_scope"],
        version=1,
        grants=[{"action": "update", "path": chapter}],
        evidence_path=chapter,
        evidence="雪地上有两行脚印。",
    )
    assert extended["version"] == 2
    assert extended["evidence_path"] == chapter


def test_continue_must_carry_an_earlier_author_request() -> None:
    controller = ScopeController()
    authors = ((1, "改写第三章的问询。"), (2, "继续。"))
    with pytest.raises(WorkspaceError) as same_message:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _CHAPTER),),
                summary="继续改写第三章",
                authorization="继续",
                authorization_seq=2,
                carried_from_seq=2,
            ),
            author_texts=authors,
        )
    assert _code(same_message) == "TASK_SCOPE_EVIDENCE_INVALID"
    # 拒绝时要告诉模型可用的序号，否则它只能继续猜。
    assert "1、2" in same_message.value.fix

    with pytest.raises(WorkspaceError) as unknown:
        controller.set_scope(
            ScopeDraft(
                mode="edit",
                grants=(Mutation("update", _CHAPTER),),
                summary="继续改写第三章",
                authorization="继续",
                authorization_seq=2,
                carried_from_seq=9,
            ),
            author_texts=authors,
        )
    assert _code(unknown) == "TASK_SCOPE_EVIDENCE_INVALID"
    assert "1、2" in unknown.value.fix

    scope = controller.set_scope(
        ScopeDraft(
            mode="edit",
            grants=(Mutation("update", _CHAPTER),),
            summary="继续改写第三章",
            authorization="继续",
            authorization_seq=2,
            carried_from_seq=1,
        ),
        author_texts=authors,
    )
    assert scope.draft.carried_from_seq == 1


@pytest.mark.anyio
async def test_new_chapter_can_be_corrected_in_same_turn(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="写第三章",
        authorization="只改第三章",
        grants=[{"action": "create", "path": _CHAPTER}],
    )
    written = await _call(tools["write_file"], path=_CHAPTER, text="# 书房\n十三个人。\n")
    edited = await _call(
        tools["edit_file"],
        path=_CHAPTER,
        old_text="十三个人",
        new_text="十四个人",
        digest=written["digest"],
    )
    rewritten = await _call(
        tools["write_file"],
        path=_CHAPTER,
        text="# 书房\n十四个人到齐了。\n",
        digest=edited["digest"],
    )
    assert "十四个人到齐了" in workspace.read(_CHAPTER).text
    with pytest.raises(WorkspaceError) as stale:
        await _call(tools["write_file"], path=_CHAPTER, text="旧版本", digest=written["digest"])
    assert stale.value.code == "FILE_STALE_CONFLICT"
    for action in ("delete", "move"):
        with pytest.raises(WorkspaceError) as denied:
            run.scope.require(
                Mutation(action, _CHAPTER, "正文/0004 新名.md" if action == "move" else None)
            )
        assert denied.value.code == "TASK_SCOPE_DENIED_CONFLICT"
    assert rewritten["digest"] == workspace.read(_CHAPTER).digest
    # 下一轮没有继承创建记录，即使再次登记 create 也不能修改已有章。
    fresh = RunContext(
        workspace, WriteSession(workspace, instruction="只改第三章"), author_messages=_AUTHOR
    )
    fresh.scope.set_scope(_edit(Mutation("create", _CHAPTER)), author_texts=_AUTHOR)
    with pytest.raises(WorkspaceError):
        fresh.scope.require(Mutation("update", _CHAPTER))


@pytest.mark.anyio
async def test_failed_create_does_not_allow_updating_existing_chapter(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    view = workspace.write(_CHAPTER, "已有正文", expected_digest=None)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="写第三章",
        authorization="只改第三章",
        grants=[{"action": "create", "path": _CHAPTER}],
    )
    with pytest.raises(WorkspaceError):
        await _call(tools["write_file"], path=_CHAPTER, text="覆盖")
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["write_file"], path=_CHAPTER, text="覆盖", digest=view.digest)
    assert denied.value.code == "TASK_SCOPE_DENIED_CONFLICT"
    assert workspace.read(_CHAPTER).text == "已有正文"
    assert not run.session.prepared

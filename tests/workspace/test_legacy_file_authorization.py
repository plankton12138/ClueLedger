"""模块写作中的旧目录权限：拒绝误选路径，保留显式旧文件任务与无副作用恢复。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from hastings.library import ModuleLibrary
from hastings.workspace.agent import RunContext, build_tools
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.service import WriteSession
from hastings.workspace.task_scope import Mutation, ScopeController, ScopeDraft
from tau_agent.tools import AgentTool

TRUTH = "设定集/真相.md"
PROFILE = "设定集/角色小传/医生.md"
CHAPTER = "正文/0001 雨夜.md"
ERROR = "LEGACY_FILE_AUTHORIZATION_REQUIRED_CONFLICT"


def _draft(text: str, *grants: Mutation, **kwargs: Any) -> ScopeDraft:
    return ScopeDraft(
        mode="edit", summary="完成作者指定的修改", authorization=text, grants=grants, **kwargs
    )


@pytest.mark.parametrize(
    ("text", "path"),
    [
        ("撰写人物小传和聚集原因。", PROFILE),
        ("把医生改成死者的姐姐，其余不变。", PROFILE),
        ("新建人物模块，参考设定集/角色小传/医生.md。", PROFILE),
        ("参考设定集/真相.md修改第一章。", TRUTH),
        ("修改第一章并参考设定集/真相.md。", TRUTH),
        ("使用设定集/真相.md作为参考，建立人物模块。", TRUTH),
        ("不要修改设定集/真相.md，只写人物模块。", TRUTH),
        ("不要沿用旧结构，撰写人物小传。", PROFILE),
        ("他说「修改设定集/真相.md」，请讨论这条建议。", TRUTH),
        ("他说'修改设定集/真相.md'，请讨论这条建议。", TRUTH),
        ("他说‘修改设定集/真相.md’，请讨论这条建议。", TRUTH),
        ("修改设定集/线索.md。", TRUTH),
        ("修改设定集/角色小传/管家.md。", PROFILE),
        ("修改设定集/角色小传/女医生.md。", PROFILE),
        ("保存到设定集/角色小传/。", TRUTH),
        ("不修改设定集/真相.md，只改第一章。", TRUTH),
    ],
)
def test_generic_creation_reference_or_other_file_does_not_authorize_legacy_write(
    text: str, path: str
) -> None:
    controller = ScopeController()
    with pytest.raises(WorkspaceError) as denied:
        controller.set_scope(
            _draft(text, Mutation("update", path)),
            author_texts=((1, text),),
            module_content_available=True,
        )
    assert denied.value.code == ERROR
    assert controller.current is None
    assert "module_content_grants" in denied.value.fix


@pytest.mark.parametrize(
    ("text", "operation"),
    [
        (f"改写 {TRUTH}，把凶手身份悬置。", Mutation("update", TRUTH)),
        (f"改写「{TRUTH}」。", Mutation("update", TRUTH)),
        (f"改写 `{TRUTH}`。", Mutation("update", TRUTH)),
        ("修改真相.md。", Mutation("update", TRUTH)),
        ("修改「真相.md」。", Mutation("update", TRUTH)),
        ("改写「设定集/角色小传/改写者.md」。", Mutation("update", "设定集/角色小传/改写者.md")),
        ("修改 设定集/角色小传/Dr.X.md。", Mutation("update", "设定集/角色小传/Dr.X.md")),
        ("改写真相文件。", Mutation("update", TRUTH)),
        ("改写第八章和真相。只改已有文件，不要新建人物文件。", Mutation("update", TRUTH)),
        ("参考第一章改写设定集/真相.md。", Mutation("update", TRUTH)),
        ("沿用旧结构撰写人物小传。", Mutation("create", PROFILE)),
        ("撰写人物小传，保存到设定集/角色小传/。", Mutation("create", PROFILE)),
        (f"删除 {PROFILE}。", Mutation("delete", PROFILE)),
        (
            f"把 {PROFILE} 重命名为 设定集/角色小传/姐姐.md。",
            Mutation("move", PROFILE, "设定集/角色小传/姐姐.md"),
        ),
        (f"改写 {CHAPTER}。", Mutation("update", CHAPTER)),
    ],
)
def test_explicit_legacy_requests_and_chapter_edits_are_preserved(
    text: str, operation: Mutation
) -> None:
    controller = ScopeController()
    registered = controller.set_scope(
        _draft(text, operation),
        author_texts=((1, text),),
        module_content_available=True,
    )
    assert registered.scope.permits(operation)


def test_old_legacy_permission_is_not_inherited_without_explicit_continuation() -> None:
    authors = ((1, f"改写 {TRUTH}。"), (2, "撰写人物小传，其余不变。"))
    for carried in (None, 1):
        with pytest.raises(WorkspaceError) as denied:
            ScopeController().set_scope(
                _draft(authors[0][1], Mutation("update", TRUTH), carried_from_seq=carried),
                author_texts=authors,
                module_content_available=True,
            )
        assert denied.value.code == ERROR


@pytest.mark.parametrize("latest", ["继续。", f"继续，但不要修改 {TRUTH}。"])
def test_explicit_continuation_honors_current_restrictions(latest: str) -> None:
    controller = ScopeController()
    authors = ((1, f"改写 {TRUTH}。"), (2, latest))
    draft = _draft("继续", Mutation("update", TRUTH), authorization_seq=2, carried_from_seq=1)
    if "不要" in latest:
        with pytest.raises(WorkspaceError) as denied:
            controller.set_scope(draft, author_texts=authors, module_content_available=True)
        assert denied.value.code == ERROR
    else:
        assert controller.set_scope(draft, author_texts=authors, module_content_available=True)


def test_bare_continue_and_move_target_cannot_expand_legacy_permission() -> None:
    authors = ((1, f"改写 {TRUTH}。"), (2, "继续。"))
    with pytest.raises(WorkspaceError) as denied:
        ScopeController().set_scope(
            _draft("继续", Mutation("update", TRUTH)),
            author_texts=authors,
            module_content_available=True,
        )
    assert denied.value.code == ERROR
    text = f"重命名 {PROFILE}。"
    with pytest.raises(WorkspaceError) as target:
        ScopeController().set_scope(
            _draft(text, Mutation("move", PROFILE, "设定集/角色小传/姐姐.md")),
            author_texts=((1, text),),
            module_content_available=True,
        )
    assert target.value.details["path"] == "设定集/角色小传/姐姐.md"


def test_extending_a_chapter_scope_does_not_authorize_old_files_from_read_evidence() -> None:
    text = "改写第一章并同步相关章节。"
    controller = ScopeController()
    controller.set_scope(
        _draft(text, Mutation("update", CHAPTER), extendable=True, extend_actions=("update",)),
        author_texts=((1, text),),
        module_content_available=True,
    )
    with pytest.raises(WorkspaceError) as denied:
        controller.extend(
            version=1,
            grants=(Mutation("update", TRUTH),),
            evidence_path=TRUTH,
            evidence="真相与第一章相关。",
            author_texts=((1, text),),
            module_content_available=True,
        )
    assert denied.value.code == ERROR
    assert controller.current is not None and controller.current.version == 1
    assert not controller.current.scope.permits(Mutation("update", TRUTH))


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    return json.loads((await tool.execute("call-legacy", arguments)).text)


def _tools(tmp_path: Path, text: str) -> tuple[RunContext, dict[str, AgentTool], list[str]]:
    workspace = Workspace.create(tmp_path / "作品")
    GitRepo(workspace.root).init()
    events: list[str] = []
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction=text),
        author_messages=((1, text),),
        library=ModuleLibrary(tmp_path / "库"),
        state_root=tmp_path / "state",
        turn_id="legacy-test",
        emit=lambda kind, payload: events.append(kind),
    )
    return run, {tool.name: tool for tool in build_tools(workspace, run)}, events


@pytest.mark.anyio
async def test_rejected_file_scope_can_recover_to_module_saving_without_a_new_turn(
    tmp_path: Path,
) -> None:
    text = "撰写人物小传。"
    run, tools, events = _tools(tmp_path, text)
    with pytest.raises(WorkspaceError) as denied:
        await _call(
            tools["set_task_scope"],
            mode="edit",
            summary="保存小传",
            authorization=text,
            grants=[{"action": "create", "path": PROFILE}],
        )
    assert denied.value.code == ERROR
    assert run.scope.current is None and not run.session.prepared
    assert events == [] and not (run.workspace.root / PROFILE).exists()

    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="保存小传",
        authorization=text,
        module_content_grants=[{"action": "create", "slot": "cast"}],
    )
    prepared = await _call(
        tools["prepare_module_changes"],
        operations=[
            {
                "operation_id": "cast",
                "action": "create",
                "name": "人物小传",
                "body": "医生是死者的姐姐。",
            }
        ],
    )
    applied = await _call(tools["apply_module_changes"], change_set_id=prepared["change_set_id"])
    assert applied["applied"] and len(applied["assembly"]["bindings"]) == 1
    assert not (run.workspace.root / PROFILE).exists()


@pytest.mark.anyio
async def test_write_rechecks_policy_before_git_or_file_side_effects(tmp_path: Path) -> None:
    text = "撰写人物小传。"
    run, tools, events = _tools(tmp_path, text)
    # 模拟内部调用方预先登记过文件范围：写工具仍须按本轮实际能力和作者消息复核。
    run.scope.set_scope(_draft(text, Mutation("create", PROFILE)), author_texts=((1, text),))
    with pytest.raises(WorkspaceError) as denied:
        await _call(tools["write_file"], path=PROFILE, text="# 医生\n")
    assert denied.value.code == ERROR
    assert not run.session.prepared and events == []
    assert not (run.workspace.root / PROFILE).exists()


@pytest.mark.anyio
async def test_explicit_legacy_file_update_still_writes_with_module_tools_available(
    tmp_path: Path,
) -> None:
    text = f"改写 {TRUTH}，把凶手身份悬置。"
    run, tools, _ = _tools(tmp_path, text)
    run.workspace.write(TRUTH, "# 真相\n\n管家是真凶。\n", expected_digest=None)
    read = await _call(tools["read_file"], path=TRUTH)
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="改写真相",
        authorization=text,
        grants=[{"action": "update", "path": TRUTH}],
    )
    await _call(
        tools["edit_file"],
        path=TRUTH,
        old_text="管家是真凶。",
        new_text="凶手未定。",
        digest=read["digest"],
    )
    assert "凶手未定。" in run.workspace.read(TRUTH).text

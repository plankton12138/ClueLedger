"""材料来源、读取区间与写前检查（0.6 V06-A1）。

契约：只有真正返回给模型的原文才计入读取记录；目录、搜索与界面打开不计入。
整体覆盖要求当前版本全文，局部替换要求被替换区间，移动与删除要求读过那一版。
外部改动按版本失效处理，本轮自己的写入不会被旧依赖永久阻断。
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
from hastings.workspace.materials import (
    REFERENCE_PREREAD_CHARS,
    REFERENCE_PREREAD_TOTAL,
    ContextRequest,
    MaterialLedger,
    Reference,
    SourceRef,
    bounded_search_hits,
    freeze_context,
    merge_spans,
    missing_spans,
    prepare_references,
    read_slice,
    require_author_quote,
    verify_context,
)
from hastings.workspace.service import WriteSession
from tau_agent.tools import AgentTool

_CHAPTER = "正文/0001 雨夜.md"


def _workspace(tmp_path: Path) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    GitRepo(workspace.root).init()
    return workspace


def _tools(tmp_path: Path) -> tuple[Workspace, dict[str, AgentTool], RunContext]:
    workspace = _workspace(tmp_path)
    run = RunContext(
        workspace,
        WriteSession(workspace, instruction="修改第一章"),
        author_messages=((1, "修改第一章，参考第一章的细节。"),),
    )
    tools = {tool.name: tool for tool in build_tools(workspace, run)}
    return workspace, tools, run


async def _grant(tools: dict[str, AgentTool], *paths: str) -> None:
    """本文件的用例关注材料与读取前提，范围在这里一次登记好。"""
    await _call(
        tools["set_task_scope"],
        mode="edit",
        summary="修改第一章",
        authorization="修改第一章",
        grants=[
            {"action": action, "path": path}
            for path in (*paths, "正文/0002 雨夜.md")
            for action in ("create", "update", "delete")
        ]
        + [
            {"action": "move", "path": path, "target": "正文/0002 雨夜.md"}
            for path in paths
            if path == _CHAPTER
        ],
    )


async def _call(tool: AgentTool, **arguments: Any) -> dict[str, Any]:
    result = await tool.execute("call-1", arguments)
    return json.loads(result.text)


def _code(exc_info: pytest.ExceptionInfo[WorkspaceError]) -> str:
    return exc_info.value.code


# -- 区间 -------------------------------------------------------------------


def test_merge_spans_merges_overlap_and_drops_empty() -> None:
    assert merge_spans([(5, 9), (0, 3), (2, 6), (20, 20)]) == ((0, 9),)


def test_merge_spans_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        merge_spans([(4, 2)])


def test_missing_spans_reports_gaps_and_treats_empty_file_as_read() -> None:
    assert missing_spans(((0, 4), (8, 10)), 10) == ((4, 8),)
    assert missing_spans((), 0) == ()


# -- 有界读取 ---------------------------------------------------------------


def test_read_slice_pages_by_lines() -> None:
    text = "第一行\n第二行\n第三行\n"
    first = read_slice(text, cursor=None, offset=1, limit=2)
    assert first.text == "第一行\n第二行\n"
    assert first.start_line == 1
    assert first.end_line == 2
    assert first.truncated is True
    assert first.next_cursor == len("第一行\n第二行\n")

    second = read_slice(text, cursor=first.next_cursor, offset=1, limit=5)
    assert second.text == "第三行\n"
    assert second.start_line == 3
    assert second.truncated is False
    assert second.next_cursor is None


def test_read_slice_continues_inside_a_very_long_line() -> None:
    text = "甲" * 25_000
    first = read_slice(text, cursor=None, offset=1, limit=200, budget=16_000)
    assert first.truncated is True
    assert first.end_char == 16_000
    # 单行超过预算也要能继续，而不是永久卡在同一行。
    second = read_slice(text, cursor=first.next_cursor, offset=1, limit=200, budget=16_000)
    assert second.start_char == 16_000
    assert second.end_char == 25_000
    assert second.truncated is False


def test_read_slice_on_empty_file_returns_empty_without_error() -> None:
    chunk = read_slice("", cursor=None, offset=1, limit=10)
    assert chunk.text == ""
    assert chunk.truncated is False
    assert chunk.next_cursor is None


# -- 读取记录 ---------------------------------------------------------------


def test_ledger_merges_overlapping_spans_per_version() -> None:
    ledger = MaterialLedger()
    ref = SourceRef(_CHAPTER, "d1")
    ledger.record_read(ref, length=100, start=0, end=40)
    coverage = ledger.record_read(ref, length=100, start=30, end=60)
    assert coverage.spans == ((0, 60),)
    assert coverage.complete is False


def test_ledger_does_not_merge_across_versions() -> None:
    ledger = MaterialLedger()
    ledger.record_read(SourceRef(_CHAPTER, "d1"), length=100, start=0, end=100)
    ledger.record_read(SourceRef(_CHAPTER, "d2"), length=100, start=0, end=10)
    latest = ledger.coverage(_CHAPTER)
    assert latest is not None
    assert latest.ref.digest == "d2"
    assert latest.complete is False


def test_ledger_treats_empty_file_as_complete() -> None:
    ledger = MaterialLedger()
    coverage = ledger.record_read(SourceRef(_CHAPTER, "d1"), length=0, start=0, end=0)
    assert coverage.complete is True


# -- 目标读取前提 -----------------------------------------------------------


@pytest.mark.anyio
async def test_write_requires_full_read_of_existing_file(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    text = "# 雨夜\n\n侦探抵达旅馆。\n\n雪越下越大。\n"
    workspace.write(_CHAPTER, text, expected_digest=None)
    created = workspace.read(_CHAPTER)

    with pytest.raises(WorkspaceError) as unread:
        await _call(tools["write_file"], path=_CHAPTER, text="# 雨夜\n", digest=created.digest)
    assert _code(unread) == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    partial = await _call(tools["read_file"], path=_CHAPTER, limit=2)
    assert partial["truncated"] is True
    with pytest.raises(WorkspaceError) as unfinished:
        await _call(tools["write_file"], path=_CHAPTER, text="# 雨夜\n", digest=created.digest)
    assert _code(unfinished) == "REFERENCE_NOT_FULLY_READ_CONFLICT"
    assert unfinished.value.details["missing"]

    await _call(tools["read_file"], path=_CHAPTER, cursor=partial["next_cursor"])
    written = await _call(
        tools["write_file"], path=_CHAPTER, text="# 雨夜\n\n改写后的开场。\n", digest=created.digest
    )
    assert written["digest"] != created.digest
    assert run.materials.coverage(_CHAPTER) is not None


@pytest.mark.anyio
async def test_new_file_needs_no_prior_read(tmp_path: Path) -> None:
    _, tools, _ = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    created = await _call(tools["write_file"], path=_CHAPTER, text="# 雨夜\n")
    assert created["digest"]


@pytest.mark.anyio
async def test_edit_requires_only_the_replaced_range(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    body = "# 雨夜\n\n" + "铺垫。" * 4_000 + "\n\n结尾一句。\n"
    workspace.write(_CHAPTER, body, expected_digest=None)
    view = workspace.read(_CHAPTER)
    await _call(tools["read_file"], path=_CHAPTER, limit=2)

    # 只读了开头：替换结尾被拒绝，替换已读区间可以。
    with pytest.raises(WorkspaceError) as unread:
        await _call(
            tools["edit_file"],
            path=_CHAPTER,
            old_text="结尾一句。",
            new_text="新的结尾。",
            digest=view.digest,
        )
    assert _code(unread) == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    edited = await _call(
        tools["edit_file"],
        path=_CHAPTER,
        old_text="# 雨夜",
        new_text="# 雨夜（改）",
        digest=view.digest,
    )
    assert edited["digest"] != view.digest
    assert "（改）" in workspace.read(_CHAPTER).text


@pytest.mark.anyio
async def test_move_and_delete_require_a_read_version(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    workspace.write(_CHAPTER, "# 雨夜\n", expected_digest=None)
    view = workspace.read(_CHAPTER)

    with pytest.raises(WorkspaceError) as unread:
        await _call(
            tools["move_file"],
            source=_CHAPTER,
            target="正文/0002 雨夜.md",
            digest=view.digest,
        )
    assert _code(unread) == "REFERENCE_NOT_FULLY_READ_CONFLICT"

    await _call(tools["read_file"], path=_CHAPTER, limit=1)
    await _call(tools["move_file"], source=_CHAPTER, target="正文/0002 雨夜.md", digest=view.digest)
    moved = workspace.read("正文/0002 雨夜.md")
    assert moved.text == "# 雨夜\n"


@pytest.mark.anyio
async def test_delete_does_not_poison_later_operations(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    workspace.write(_CHAPTER, "# 雨夜\n", expected_digest=None)
    view = workspace.read(_CHAPTER)
    await _call(tools["read_file"], path=_CHAPTER, limit=1)
    await _call(tools["delete_file"], path=_CHAPTER, digest=view.digest)

    # 本轮自己的删除是执行事实：再创建同名文件不应被「来源丢失」永久阻断。
    recreated = await _call(tools["write_file"], path=_CHAPTER, text="# 雨夜（新）\n")
    assert recreated["digest"]


# -- 参考完成条件 -----------------------------------------------------------


@pytest.mark.anyio
async def test_required_reference_must_be_read_in_full(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    workspace.write(
        "正文/0001 参考.md", "# 参考\n\n" + "细节。" * 1_000 + "\n", expected_digest=None
    )
    reference = workspace.read("正文/0001 参考.md")
    run.materials.register(
        Reference(
            SourceRef(reference.path, reference.digest), kind="author_selected", required=True
        )
    )
    await _call(tools["read_file"], path=reference.path, limit=2)

    with pytest.raises(WorkspaceError) as unfinished:
        await _call(tools["write_file"], path=_CHAPTER, text="# 新章\n")
    assert _code(unfinished) == "REFERENCE_NOT_FULLY_READ_CONFLICT"
    assert unfinished.value.details["path"] == reference.path


@pytest.mark.anyio
async def test_reference_changed_after_read_blocks_writing(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    await _grant(tools, _CHAPTER)
    workspace.write("正文/0001 参考.md", "# 参考\n\n细节。\n", expected_digest=None)
    reference = workspace.read("正文/0001 参考.md")
    run.materials.register(
        Reference(
            SourceRef(reference.path, reference.digest), kind="author_selected", required=True
        )
    )
    await _call(tools["read_file"], path=reference.path)
    (workspace.root / "正文/0001 参考.md").write_text("# 参考\n\n外部改过。\n", encoding="utf-8")

    with pytest.raises(WorkspaceError) as stale:
        await _call(tools["write_file"], path=_CHAPTER, text="# 新章\n")
    assert _code(stale) == "CONTEXT_SOURCE_STALE_CONFLICT"


@pytest.mark.anyio
async def test_model_cannot_downgrade_an_author_selected_reference(tmp_path: Path) -> None:
    """作者选中的参考默认为必读，模型再次登记同一路径不能把它降为可选。"""
    workspace, tools, run = _tools(tmp_path)
    await _grant(tools, _CHAPTER)
    workspace.write("正文/0001 参考.md", "# 参考\n\n细节甲。\n细节乙。\n", expected_digest=None)
    reference = workspace.read("正文/0001 参考.md")
    run.materials.register(
        Reference(
            SourceRef(reference.path, reference.digest), kind="author_selected", required=True
        )
    )
    # 只读到第三行：引文落在读过的区间里，全文仍未读完。
    await _call(tools["read_file"], path=reference.path, limit=3)

    registered = await _call(
        tools["register_references"],
        references=[
            {
                "path": reference.path,
                "kind": "discovered",
                "quote": "细节甲。",
                "required": False,
            }
        ],
    )
    assert registered["required"] == [reference.path]

    with pytest.raises(WorkspaceError) as unfinished:
        await _call(tools["write_file"], path=_CHAPTER, text="# 新章\n")
    assert _code(unfinished) == "REFERENCE_NOT_FULLY_READ_CONFLICT"


def test_register_merges_duplicate_paths_without_downgrading(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write(_CHAPTER, "# 雨夜\n\n远处有灯光。\n", expected_digest=None)
    view = workspace.read(_CHAPTER)
    ref = SourceRef(view.path, view.digest)
    ledger = MaterialLedger()
    ledger.register(Reference(ref, kind="author_selected", required=True, quote="", seq=None))

    merged = ledger.register(Reference(ref, kind="discovered", required=False, note="补充"))

    assert merged.kind == "author_selected"
    assert merged.required is True
    assert ledger.references() == (merged,)
    assert ledger.required_paths() == (_CHAPTER,)


@pytest.mark.anyio
async def test_rejection_leaves_no_git_side_effect(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    before = GitRepo(workspace.root).head()
    with pytest.raises(WorkspaceError):
        await _call(tools["write_file"], path=_CHAPTER, text="# 新章\n", digest="0" * 64)
    assert run.session.prepared is False
    assert GitRepo(workspace.root).head() == before
    assert not (workspace.root / _CHAPTER).exists()


# -- 预读与搜索上限 ---------------------------------------------------------


def test_prepare_references_allocates_budget_in_order(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    long_text = "甲" * (REFERENCE_PREREAD_CHARS + 500)
    workspace.write("正文/0001 甲.md", long_text, expected_digest=None)
    workspace.write("正文/0002 乙.md", "乙" * 1_000, expected_digest=None)
    ledger = MaterialLedger()

    prepared = prepare_references(workspace, ledger, ["正文/0001 甲.md", "正文/0002 乙.md"])

    assert prepared[0].end == REFERENCE_PREREAD_CHARS
    assert prepared[0].truncated is True
    remaining = REFERENCE_PREREAD_TOTAL - REFERENCE_PREREAD_CHARS
    assert prepared[1].end == min(1_000, remaining)
    assert ledger.coverage("正文/0001 甲.md") is not None


def test_prepare_references_reports_missing_file(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as missing:
        prepare_references(workspace, MaterialLedger(), ["正文/0009 不存在.md"])
    assert _code(missing) == "CONTEXT_SOURCE_NOT_FOUND"


def test_bounded_search_hits_caps_count_and_chars() -> None:
    hits = [(f"正文/{index:04d}.md", 1, "摘" * 240) for index in range(60)]
    kept, truncated = bounded_search_hits(hits)
    assert truncated is True
    assert len(kept) <= 50
    assert sum(len(str(item["text"])) for item in kept) <= 8_000


# -- 工具层的记录边界 -------------------------------------------------------


@pytest.mark.anyio
async def test_directory_and_search_do_not_count_as_reading(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    workspace.write(_CHAPTER, "# 雨夜\n\n侦探抵达旅馆。\n", expected_digest=None)

    await _call(tools["list_files"])
    await _call(tools["search_text"], query="侦探")

    assert run.materials.coverage(_CHAPTER) is None


@pytest.mark.anyio
async def test_read_file_reports_actual_range_and_next_cursor(tmp_path: Path) -> None:
    workspace, tools, run = _tools(tmp_path)
    body = "# 雨夜\n\n" + "铺垫。" * 6_000 + "\n"
    workspace.write(_CHAPTER, body, expected_digest=None)

    first = await _call(tools["read_file"], path=_CHAPTER)
    assert first["start_char"] == 0
    assert first["end_char"] == 16_000
    assert first["total_chars"] == len(body)
    assert first["truncated"] is True
    assert first["next_cursor"] == 16_000

    second = await _call(tools["read_file"], path=_CHAPTER, cursor=first["next_cursor"])
    assert second["start_char"] == 16_000
    assert second["truncated"] is False
    coverage = run.materials.coverage(_CHAPTER)
    assert coverage is not None
    assert coverage.complete is True


@pytest.mark.anyio
async def test_read_file_rejects_negative_cursor(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    workspace.write(_CHAPTER, "# 雨夜\n", expected_digest=None)
    with pytest.raises(WorkspaceError) as bad:
        await _call(tools["read_file"], path=_CHAPTER, cursor=-1)
    assert _code(bad) == "TOOL_INPUT_INVALID"


@pytest.mark.anyio
async def test_list_files_pages_long_catalogs(tmp_path: Path) -> None:
    workspace, tools, _ = _tools(tmp_path)
    for index in range(1, 6):
        workspace.write(f"正文/{index:04d} 章{index}.md", f"# 章{index}\n", expected_digest=None)

    first = await _call(tools["list_files"], limit=2)
    assert len(first["chapters"]) == 2
    assert first["truncated"] is True
    assert first["next_offset"] == 3

    rest = await _call(tools["list_files"], offset=first["next_offset"], limit=10)
    assert len(rest["chapters"]) == 3
    assert rest["truncated"] is False


# -- 写后更新 ---------------------------------------------------------------


def test_after_edit_shifts_unaffected_spans(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write(_CHAPTER, "# 雨夜\n\n开头。\n\n结尾。\n", expected_digest=None)
    before = workspace.read(_CHAPTER)
    ledger = MaterialLedger()
    ledger.record_read(
        SourceRef(before.path, before.digest), length=len(before.text), start=0, end=8
    )
    ledger.record_read(
        SourceRef(before.path, before.digest),
        length=len(before.text),
        start=len(before.text) - 4,
        end=len(before.text),
    )
    updated = before.text.replace("开头。", "新的开头。")
    after = workspace.write(_CHAPTER, updated, expected_digest=before.digest)

    ledger.after_edit(
        before=SourceRef(before.path, before.digest),
        view=after,
        start=before.text.index("开头。"),
        end=before.text.index("开头。") + len("开头。"),
    )

    coverage = ledger.coverage(_CHAPTER)
    assert coverage is not None
    assert coverage.ref.digest == after.digest
    # 与改动区间重叠的那一段被丢弃；结尾在改动之后，整体右移后仍算已提供。
    assert coverage.spans[0] == (0, 6)
    assert coverage.spans[-1][1] == len(after.text)


def test_after_full_write_registers_complete_new_version(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ledger = MaterialLedger()
    view = workspace.write(_CHAPTER, "# 雨夜\n\n原稿。\n", expected_digest=None)
    ledger.after_full_write(view)

    coverage = ledger.coverage(_CHAPTER)
    assert coverage is not None
    assert coverage.complete is True
    assert coverage.ref.digest == view.digest


# -- 受理快照 ---------------------------------------------------------------


def test_freeze_context_keeps_order_and_dedupes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 甲.md", "# 甲\n", expected_digest=None)
    workspace.write("正文/0002 乙.md", "# 乙\n", expected_digest=None)
    request = ContextRequest(
        selected_path="正文/0001 甲.md",
        reference_paths=("正文/0002 乙.md", "正文/0001 甲.md", "正文/0002 乙.md"),
        observed_digests={"正文/0002 乙.md": "d2"},
    )

    snapshot = freeze_context(workspace, request)

    assert snapshot.reference_paths == ("正文/0002 乙.md", "正文/0001 甲.md")
    assert snapshot.identity()["observed_digests"] == {"正文/0002 乙.md": "d2"}


def test_freeze_context_rejects_missing_and_unknown_paths(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    with pytest.raises(WorkspaceError) as missing:
        freeze_context(workspace, ContextRequest(reference_paths=("正文/0009 无.md",)))
    assert _code(missing) == "CONTEXT_SOURCE_NOT_FOUND"

    workspace.write("正文/0001 甲.md", "# 甲\n", expected_digest=None)
    with pytest.raises(WorkspaceError) as unknown:
        freeze_context(
            workspace,
            ContextRequest(
                reference_paths=("正文/0001 甲.md",),
                observed_digests={"正文/0002 乙.md": "d2"},
            ),
        )
    assert _code(unknown) == "CONTEXT_INPUT_INVALID"


def test_verify_context_reports_stale_version(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    workspace.write("正文/0001 甲.md", "# 甲\n", expected_digest=None)
    snapshot = freeze_context(workspace, ContextRequest(reference_paths=("正文/0001 甲.md",)))
    verify_context(workspace, snapshot)

    (workspace.root / "正文/0001 甲.md").write_text("# 甲（外部改过）\n", encoding="utf-8")

    with pytest.raises(WorkspaceError) as stale:
        verify_context(workspace, snapshot)
    assert _code(stale) == "CONTEXT_SOURCE_STALE_CONFLICT"
    assert stale.value.details["sources"][0]["path"] == "正文/0001 甲.md"


def test_require_author_quote_rejects_invented_text() -> None:
    authors = ((1, "参考第一章修改第三章。"),)
    require_author_quote(authors, "参考第一章", 1, where="test")
    with pytest.raises(WorkspaceError) as invented:
        require_author_quote(authors, "模型觉得可以改", None, where="test")
    assert _code(invented) == "REFERENCE_EVIDENCE_INVALID"
    with pytest.raises(WorkspaceError) as wrong_seq:
        require_author_quote(authors, "参考第一章", 9, where="test")
    assert _code(wrong_seq) == "REFERENCE_EVIDENCE_INVALID"

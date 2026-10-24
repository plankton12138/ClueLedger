"""设定分区与装配尺寸（0.8 A）。

契约：

- 分区预算按**实际渲染出的字符数**计算：分区标题、截断提示与正文都占位；
- 固定设定超出预算时截短正文并写明还剩多少，读取入口不能整块消失；
- ``PROFILE_BUDGETS`` 是分区字符预算之和，不是整轮 token 上限，实际 token 另行记录。

三种长度的设定用同一个临时作品生成，不依赖本机样例目录。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hastings.workspace import assembly
from hastings.workspace.assembly import Partition, assemble
from hastings.workspace.context import build_assembled_context
from hastings.workspace.files import Workspace

_CHAPTER = "正文/0001 雨夜.md"
_CLUES = "设定集/线索.md"
_TRUTH = "设定集/真相.md"


def _workspace(tmp_path: Path, *, clues: str, truth: str) -> Workspace:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    workspace.write(_CHAPTER, "第一章 雨夜\n\n雪压住了山路。", expected_digest=None)
    for path, text in ((_CLUES, clues), (_TRUTH, truth)):
        workspace.write(path, text, expected_digest=workspace.read(path).digest)
    return workspace


def _settings_part(tmp_path: Path, size: int) -> tuple[assembly.AssembledContext, Partition]:
    workspace = _workspace(tmp_path, clues="线" * size, truth="真" * size)
    assembled = build_assembled_context(workspace)
    part = next(item for item in assembled.parts if item.key == "settings")
    return assembled, part


def test_settings_partition_is_rendered_within_its_budget(tmp_path: Path) -> None:
    assembled, part = _settings_part(tmp_path, 2_900)
    assert len(part.render()) <= assembly.SETTINGS_BUDGET
    assert assembled.used == sum(len(item.render()) for item in assembled.parts)
    assert "已截断" not in part.text


@pytest.mark.parametrize("size", [10, 2_990, 3_000, 3_500, 20_000])
def test_settings_partition_never_disappears(tmp_path: Path, size: int) -> None:
    """长设定只截短，不整块跳过：标题与实际装入的部分必须留在上下文里。"""
    assembled, part = _settings_part(tmp_path, size)

    assert "settings" not in [skip.key for skip in assembled.skipped]
    assert "## 设定集内容" in assembled.text
    assert len(part.render()) <= assembly.SETTINGS_BUDGET
    for path in (_CLUES, _TRUTH):
        assert path in part.text


@pytest.mark.parametrize("size", [3_000, 3_500, 20_000])
def test_truncation_notice_is_counted_in_the_budget(tmp_path: Path, size: int) -> None:
    """截断提示本身也占预算：算进去以后仍然不超，且告诉模型怎么继续读。"""
    _, part = _settings_part(tmp_path, size)

    assert "已截断" in part.text
    assert "read_file" in part.text
    assert len(part.render()) <= assembly.SETTINGS_BUDGET
    # 正文被真正截短，而不是把超长原文整段塞进去。
    assert part.text.count("线") < size


def test_short_settings_are_included_whole(tmp_path: Path) -> None:
    _, part = _settings_part(tmp_path, 10)
    assert "已截断" not in part.text
    assert "线" * 10 in part.text
    assert "真" * 10 in part.text


def test_partition_budget_counts_the_rendered_title() -> None:
    """预算比较的是实际渲染文本；``## 段落`` 这些字符也占位。"""
    header = len("## 段落\n\n")
    over = Partition(key="p", title="段落", text="字" * (100 - header + 1), budget=100)
    exact = Partition(key="q", title="段落", text="字" * (100 - header), budget=100)

    result = assemble("writer", [Partition("i", "要求", "要求", 100, required=True), over, exact])

    assert [(skip.key, skip.reason, skip.chars) for skip in result.skipped] == [
        ("p", "budget", 101)
    ]
    assert [part.key for part in result.parts] == ["i", "q"]
    assert result.used == len("## 要求\n\n要求") + 100


def test_summary_reports_rendered_sizes(tmp_path: Path) -> None:
    assembled, _ = _settings_part(tmp_path, 3_500)
    summary = assembled.summary()

    parts = {item["key"]: item["chars"] for item in summary["parts"]}  # type: ignore[index]
    assert parts["settings"] == len(
        next(part for part in assembled.parts if part.key == "settings").render()
    )
    assert summary["budget"] == {
        "limit": assembly.PROFILE_BUDGETS["writer"],
        "used": assembled.used,
    }  # type: ignore[index]


def test_writer_limit_is_a_partition_character_budget() -> None:
    """46,900 是分区字符预算之和；它不是整轮 token 上限，也不参与 token 统计。"""
    writer = assembly.PROFILE_BUDGETS["writer"]
    assert writer == (
        assembly.INSTRUCTIONS_BUDGET
        + assembly.CATALOG_BUDGET
        + assembly.MODULES_BUDGET
        + assembly.MATERIALS_BUDGET
        + assembly.INDEX_BUDGET
        + assembly.STATE_BUDGET
        + assembly.GLOSSARY_BUDGET
        + assembly.ROSTER_BUDGET
        + assembly.TIMELINE_BUDGET
        + assembly.CLUES_BUDGET
        + assembly.SETTINGS_BUDGET
        + assembly.HISTORY_BUDGET
    )
    assert writer == 46_900


def test_modules_partition_is_optional_and_reports_why_it_was_skipped(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path, clues="", truth="")
    assembled = build_assembled_context(workspace, modules="模块" * (assembly.MODULES_BUDGET + 1))

    assert "modules" not in {item.key for item in assembled.parts}
    skipped = next(item for item in assembled.skipped if item.key == "modules")
    assert skipped.reason == "budget"
    assert skipped.chars > assembly.MODULES_BUDGET

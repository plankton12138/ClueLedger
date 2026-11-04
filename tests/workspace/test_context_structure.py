"""结构层分区装配测试（v1.1 M3，Q2-B）。

装配顺序契约：STRUCTURE.md 唯一全局语义分区 → 模块 →
台账。这里验证分区在装配结果里的相对顺序、常驻不裁与空台账跳过。
"""

from __future__ import annotations

from pathlib import Path

from hastings.workspace.context import build_assembled_context
from hastings.workspace.files import Workspace
from hastings.workspace.prose_ledger import ProseLedger
from hastings.workspace.structure_assembly import (
    ledger_section,
    split_structure_layers,
    structure_section_text,
)

_STRUCTURE = "## 铁律\n不写超自然力量\n\n## 骨架\n- 第 1 章：雨夜"


def _assembled(tmp_path: Path, *, structure: str = "", ledger: str = "", modules: str = ""):
    workspace = Workspace.create(tmp_path / "作品")
    return build_assembled_context(
        workspace, structure=structure, ledger=ledger, modules=modules
    )


def _part_keys(assembled) -> list[str]:
    return [part.key for part in assembled.parts]


def test_structure_partition_sits_before_modules(tmp_path: Path) -> None:
    assembled = _assembled(
        tmp_path,
        structure=structure_section_text(split_structure_layers(_STRUCTURE)),
        modules="- 模块索引",
        ledger=ledger_section(ProseLedger(chapters={1: "rev-1"})),
    )

    keys = _part_keys(assembled)
    assert keys.index("structure") < keys.index("modules") < keys.index("ledger")


def test_structure_partition_is_required_never_skipped(tmp_path: Path) -> None:
    assembled = _assembled(
        tmp_path, structure=structure_section_text(split_structure_layers(_STRUCTURE))
    )

    assert "structure" in _part_keys(assembled)
    assert "structure" not in [skip.key for skip in assembled.skipped]
    part = next(part for part in assembled.parts if part.key == "structure")
    assert "- 不写超自然力量" in part.text
    assert "STYLE.md" not in part.text


def test_empty_structure_and_ledger_are_skipped_as_empty(tmp_path: Path) -> None:
    assembled = _assembled(tmp_path, structure="", ledger="")

    assert "structure" not in _part_keys(assembled)
    assert "ledger" not in _part_keys(assembled)


def test_ledger_partition_lists_registered_chapters(tmp_path: Path) -> None:
    ledger_text = ledger_section(ProseLedger(chapters={1: "rev-aaaaaaaa"}))
    assembled = _assembled(tmp_path, ledger=ledger_text)

    part = next(part for part in assembled.parts if part.key == "ledger")
    assert "第 1 章｜最近入档修订 rev-aaaa" in part.text
    assert "以本台账为准" in part.text

"""案例 schema：加载与锚点校验（不调模型）。"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from evaluation.harness.schema import CaseLoadError, load_case, load_cases

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "evaluation" / "cases"
WORKS = ROOT / "evaluation" / "works"


def _write_work(tmp: Path, *, chapter: str) -> Path:
    work = tmp / "works" / "base" / "inn"
    chapter_path = work / "正文" / "0003 第三章.md"
    chapter_path.parent.mkdir(parents=True)
    chapter_path.write_text(chapter, encoding="utf-8")
    (work / "写作约定.md").write_text("约定：人名勿混。\n", encoding="utf-8")
    return work


def _write_case(tmp: Path, payload: dict) -> Path:
    path = tmp / "cases" / "revise_snow.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_load_case_resolves_anchors(tmp_path: Path) -> None:
    chapter = "前文保留。\n林岚推开客栈的门，雨还在下。\n「你昨晚去了哪里？」\n她没有回答。\n"
    _write_work(tmp_path, chapter=chapter)
    case_path = _write_case(
        tmp_path,
        {
            "id": "revise_ch3_ending_snow",
            "version": 1,
            "work": "base/inn",
            "history": "short",
            "steps": [
                {
                    "author": "把第三章结尾改成雪夜，只改这一章，保留人物对话。",
                    "focus": "正文/0003 第三章.md",
                }
            ],
            "expect": {
                "region": {
                    "path": "正文/0003 第三章.md",
                    "from": "林岚推开客栈的门",
                    "to": "END",
                },
                "preserve": ["「你昨晚去了哪里？」"],
            },
            "checks": [
                {"id": "only_region_changed", "level": "must"},
                {"id": "preserved_verbatim", "level": "must"},
                {"id": "committed_via_candidate", "level": "must"},
                {
                    "id": "snow_present",
                    "level": "human",
                    "question": "改后的结尾是否明确发生在雪夜？",
                },
            ],
        },
    )
    case = load_case(case_path, works_root=tmp_path / "works")
    assert case.id == "revise_ch3_ending_snow"
    assert case.work_dir == tmp_path / "works" / "base" / "inn"
    assert case.expect.region.from_anchor == "林岚推开客栈的门"
    assert case.expect.preserve == ("「你昨晚去了哪里？」",)
    assert [c.id for c in case.checks] == [
        "only_region_changed",
        "preserved_verbatim",
        "committed_via_candidate",
        "snow_present",
    ]


def test_load_case_rejects_missing_anchor(tmp_path: Path) -> None:
    _write_work(tmp_path, chapter="没有目标句子。\n")
    case_path = _write_case(
        tmp_path,
        {
            "id": "bad_anchor",
            "version": 1,
            "work": "base/inn",
            "history": "short",
            "steps": [{"author": "改结尾", "focus": "正文/0003 第三章.md"}],
            "expect": {
                "region": {
                    "path": "正文/0003 第三章.md",
                    "from": "林岚推开客栈的门",
                    "to": "END",
                },
                "preserve": [],
            },
            "checks": [{"id": "only_region_changed", "level": "must"}],
        },
    )
    with pytest.raises(CaseLoadError, match="锚点"):
        load_case(case_path, works_root=tmp_path / "works")


def test_load_case_rejects_duplicate_anchor(tmp_path: Path) -> None:
    _write_work(
        tmp_path,
        chapter="林岚推开客栈的门\n中间\n林岚推开客栈的门\n",
    )
    case_path = _write_case(
        tmp_path,
        {
            "id": "dup_anchor",
            "version": 1,
            "work": "base/inn",
            "history": "short",
            "steps": [{"author": "改结尾", "focus": "正文/0003 第三章.md"}],
            "expect": {
                "region": {
                    "path": "正文/0003 第三章.md",
                    "from": "林岚推开客栈的门",
                    "to": "END",
                },
                "preserve": [],
            },
            "checks": [{"id": "only_region_changed", "level": "must"}],
        },
    )
    with pytest.raises(CaseLoadError, match="唯一"):
        load_case(case_path, works_root=tmp_path / "works")


def test_load_cases_lists_ids(tmp_path: Path) -> None:
    _write_work(tmp_path, chapter="林岚推开客栈的门，雨还在下。\n")
    _write_case(
        tmp_path,
        {
            "id": "revise_one",
            "version": 1,
            "work": "base/inn",
            "history": "short",
            "steps": [{"author": "改", "focus": "正文/0003 第三章.md"}],
            "expect": {
                "region": {
                    "path": "正文/0003 第三章.md",
                    "from": "林岚推开客栈的门",
                    "to": "END",
                },
                "preserve": [],
            },
            "checks": [{"id": "only_region_changed", "level": "must"}],
        },
    )
    cases = load_cases(tmp_path / "cases", works_root=tmp_path / "works")
    assert [c.id for c in cases] == ["revise_one"]

"""案例加载与锚点校验。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

CheckLevel = Literal["must", "quality", "human"]
HistoryKind = Literal["short", "long"]


class CaseLoadError(ValueError):
    """案例无法加载：格式错误或锚点在初态中不成立。"""


@dataclass(frozen=True)
class Step:
    author: str
    focus: str | None = None


@dataclass(frozen=True)
class Region:
    path: str
    from_anchor: str
    to_anchor: str  # "END" 或原文片段


@dataclass(frozen=True)
class Expect:
    region: Region
    preserve: tuple[str, ...]


@dataclass(frozen=True)
class Check:
    id: str
    level: CheckLevel
    question: str | None = None


@dataclass(frozen=True)
class Case:
    id: str
    version: int
    work: str
    work_dir: Path
    history: HistoryKind
    steps: tuple[Step, ...]
    expect: Expect
    checks: tuple[Check, ...]
    source: Path


def _require(mapping: dict[str, Any], key: str, *, where: str) -> Any:
    if key not in mapping:
        raise CaseLoadError(f"{where} 缺少字段 {key}")
    return mapping[key]


def _count_occurrences(text: str, needle: str) -> int:
    if not needle:
        return 0
    count = 0
    start = 0
    while True:
        index = text.find(needle, start)
        if index < 0:
            return count
        count += 1
        start = index + max(len(needle), 1)


def _assert_unique_anchor(text: str, anchor: str, *, label: str, path: str) -> None:
    count = _count_occurrences(text, anchor)
    if count == 0:
        raise CaseLoadError(f"锚点「{anchor}」在 {path} 中不存在（{label}）")
    if count > 1:
        raise CaseLoadError(f"锚点「{anchor}」在 {path} 中不唯一（出现 {count} 次，{label}）")


def _validate_expect(work_dir: Path, expect: Expect) -> None:
    target = work_dir / expect.region.path
    if not target.is_file():
        raise CaseLoadError(f"区域文件不存在：{expect.region.path}")
    text = target.read_text(encoding="utf-8")
    _assert_unique_anchor(
        text, expect.region.from_anchor, label="region.from", path=expect.region.path
    )
    if expect.region.to_anchor != "END":
        _assert_unique_anchor(
            text, expect.region.to_anchor, label="region.to", path=expect.region.path
        )
        from_at = text.find(expect.region.from_anchor)
        to_at = text.find(expect.region.to_anchor)
        if to_at < from_at:
            raise CaseLoadError(
                f"锚点顺序无效：to 出现在 from 之前（{expect.region.path}）"
            )
    for fragment in expect.preserve:
        if _count_occurrences(text, fragment) == 0:
            raise CaseLoadError(
                f"保留片段「{fragment}」在 {expect.region.path} 中不存在（preserve）"
            )


def _parse_case(raw: dict[str, Any], *, source: Path, works_root: Path) -> Case:
    where = str(source)
    case_id = str(_require(raw, "id", where=where))
    version = int(_require(raw, "version", where=where))
    work = str(_require(raw, "work", where=where))
    history = str(_require(raw, "history", where=where))
    if history not in {"short", "long"}:
        raise CaseLoadError(f"{where} history 须为 short 或 long")
    steps_raw = _require(raw, "steps", where=where)
    if not isinstance(steps_raw, list) or not steps_raw:
        raise CaseLoadError(f"{where} steps 须为非空列表")
    steps = tuple(
        Step(
            author=str(_require(item, "author", where=f"{where} steps[{index}]")),
            focus=(str(item["focus"]) if item.get("focus") is not None else None),
        )
        for index, item in enumerate(steps_raw)
        if isinstance(item, dict)
    )
    if len(steps) != len(steps_raw):
        raise CaseLoadError(f"{where} steps 项须为对象")
    expect_raw = _require(raw, "expect", where=where)
    region_raw = _require(expect_raw, "region", where=f"{where} expect")
    region = Region(
        path=str(_require(region_raw, "path", where=f"{where} expect.region")),
        from_anchor=str(_require(region_raw, "from", where=f"{where} expect.region")),
        to_anchor=str(_require(region_raw, "to", where=f"{where} expect.region")),
    )
    preserve_raw = expect_raw.get("preserve") or []
    if not isinstance(preserve_raw, list):
        raise CaseLoadError(f"{where} expect.preserve 须为列表")
    expect = Expect(region=region, preserve=tuple(str(item) for item in preserve_raw))
    checks_raw = _require(raw, "checks", where=where)
    if not isinstance(checks_raw, list) or not checks_raw:
        raise CaseLoadError(f"{where} checks 须为非空列表")
    checks: list[Check] = []
    for index, item in enumerate(checks_raw):
        if not isinstance(item, dict):
            raise CaseLoadError(f"{where} checks[{index}] 须为对象")
        level = str(_require(item, "level", where=f"{where} checks[{index}]"))
        if level not in {"must", "quality", "human"}:
            raise CaseLoadError(f"{where} checks[{index}].level 无效")
        question = item.get("question")
        if level == "human" and not question:
            raise CaseLoadError(f"{where} checks[{index}] 人工项须有 question")
        checks.append(
            Check(
                id=str(_require(item, "id", where=f"{where} checks[{index}]")),
                level=level,  # type: ignore[arg-type]
                question=str(question) if question is not None else None,
            )
        )
    work_dir = (works_root / work).resolve()
    if not work_dir.is_dir():
        raise CaseLoadError(f"基础作品不存在：{work}")
    case = Case(
        id=case_id,
        version=version,
        work=work,
        work_dir=work_dir,
        history=history,  # type: ignore[arg-type]
        steps=steps,
        expect=expect,
        checks=tuple(checks),
        source=source,
    )
    _validate_expect(work_dir, expect)
    return case


def load_case(path: Path, *, works_root: Path) -> Case:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CaseLoadError(f"{path} 根须为对象")
    return _parse_case(raw, source=path, works_root=works_root)


def load_cases(cases_dir: Path, *, works_root: Path) -> list[Case]:
    if not cases_dir.is_dir():
        raise CaseLoadError(f"案例目录不存在：{cases_dir}")
    paths = sorted(cases_dir.glob("*.yaml")) + sorted(cases_dir.glob("*.yml"))
    return [load_case(path, works_root=works_root) for path in paths]

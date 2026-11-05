"""手写伏笔账一次性迁移（v1.3 P3，规格见提案 §7）。

把 v1.2 之前手工维护的 markdown 表（``| 项 | 埋 | 收 | 状态 |``）解析成章级
线索草稿。迁移是**半自动**的，只做机械部分：

- 能确定的：编号、埋设章、回收章、状态映射；
- 不能确定的：锚。迁移条目一律 ``origin="migrated"``、锚留空，由该章下一次
  修订时逐条重锚（对账与正文保存都会持续提醒）。

三条边界：

- **不猜数字。** 埋/收章取单元格里的阿拉伯数字；取不到就跳过并说明原因，
  不按行号或上下文推算。
- **不强迁。** 埋收同章、状态无法识别、缺回收章的行一律跳过，列进报告由人处理。
- **不覆盖。** 只往章级文件里追加缺失条目；已有同编号或同内容条目视为已迁移。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from hastings.workspace.chapter_clues import (
    CLUES_DIRNAME,
    ClueEntry,
    CluePlan,
    ResolvesRef,
    Status,
    read_chapter_clues,
    write_chapter_clues,
)
from hastings.workspace.errors import WorkspaceError

__all__ = [
    "LedgerRow",
    "MigrationDraft",
    "MigrationEntry",
    "MigrationReport",
    "MigrationSkip",
    "apply_draft",
    "build_draft",
    "existing_ids",
    "parse_ledger_table",
    "suggest_plan_section",
]

_NUMBER = re.compile(r"\d+")
_SEPARATOR_CELL = re.compile(r"^:?-{3,}:?$")
#: 回收列说明文字两端的括号、顿号与破折号（如「5（老魏说出箱子来历）」）。
_DETAIL_TRIM = re.compile(r"^[\s（）()、,，;；:：\-–—/／]+|[\s（）()、,，;；:：\-–—/／]+$")
_EMPHASIS = re.compile(r"^[*_]+|[*_]+$")

#: 迁移条目使用的修订号（与真实章节修订区分开，便于日后辨认来源）。
MIGRATION_REVISION = "migration"


@dataclass(frozen=True)
class LedgerRow:
    """手写表里的一行。``status`` 为 ``None`` 表示状态列无法识别。"""

    line: int
    title: str
    plant_chapter: int | None
    resolve_chapter: int | None
    status: Status | None
    status_text: str = ""
    detail: str = ""


@dataclass(frozen=True)
class MigrationEntry:
    """一条待写入的章级条目。"""

    chapter: int
    entry: ClueEntry


@dataclass(frozen=True)
class MigrationSkip:
    """被跳过的行与原因（不静默丢弃）。"""

    line: int
    title: str
    reason: str


@dataclass(frozen=True)
class MigrationDraft:
    """解析结果：可写的条目、要补进结构层的计划、跳过的行。"""

    entries: tuple[MigrationEntry, ...] = ()
    plans: tuple[CluePlan, ...] = ()
    skipped: tuple[MigrationSkip, ...] = ()

    def chapters(self) -> list[int]:
        return sorted({item.chapter for item in self.entries})


@dataclass(frozen=True)
class MigrationReport:
    """落盘结果。"""

    files: tuple[str, ...] = ()
    added: int = 0
    duplicates: int = 0


def _cells(line: str) -> list[str]:
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|"):
        body = body[:-1]
    return [cell.strip() for cell in body.split("|")]


def _normalize_status(text: str) -> Status | None:
    value = text.replace(" ", "")
    if not value:
        # 状态列空着：按「有埋设章即已埋」处理，迁移报告里会标出来。
        return "planted"
    if "待埋" in value or "未埋" in value or value.startswith("计划"):
        return None  # 仅计划，调用方按 plan 处理
    if "不收" in value or "放弃" in value or "废弃" in value or "退役" in value:
        return "retired"
    if "已回收" in value or "已收" in value or "收毕" in value:
        return "resolved"
    if "部分" in value or "推进" in value or "半收" in value:
        return "advanced"
    if "未回收" in value or "未收" in value or "已埋" in value or "埋" in value:
        return "planted"
    return None


def _is_planned(text: str) -> bool:
    value = text.replace(" ", "")
    return "待埋" in value or "未埋" in value or value.startswith("计划")


def _header_columns(cells: list[str]) -> dict[str, int] | None:
    """识别表头列位置；认不出「埋/收/状态」就不当作伏笔账表。"""
    columns: dict[str, int] = {}
    for index, cell in enumerate(cells):
        plain = cell.replace("*", "").strip()
        if "状态" in plain and "status" not in columns:
            columns["status"] = index
        elif "埋" in plain and "plant" not in columns:
            columns["plant"] = index
        elif "收" in plain and "resolve" not in columns:
            columns["resolve"] = index
        elif any(key in plain for key in ("项", "线索", "伏笔")) and "title" not in columns:
            columns["title"] = index
    if "plant" not in columns or "resolve" not in columns or "status" not in columns:
        return None
    columns.setdefault("title", 0)
    return columns


def _cell_at(cells: list[str], index: int | None) -> str:
    if index is None or index >= len(cells):
        return ""
    return cells[index]


def _detail_of(resolve_cell: str) -> str:
    """回收列里除数字之外的说明（如「5（老魏说出箱子来历）」→「老魏说出箱子来历」）。"""
    text = _NUMBER.sub("", resolve_cell)
    text = _DETAIL_TRIM.sub("", text)
    return "" if text in {"无", "—", "-"} else text


def parse_ledger_table(text: str) -> list[LedgerRow]:
    """解析手写伏笔账表格；找不到表头时返回空列表（自由格式不做强迁）。"""
    rows: list[LedgerRow] = []
    columns: dict[str, int] | None = None
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line.startswith("|"):
            if columns is not None:
                break  # 表格结束
            continue
        cells = _cells(line)
        if columns is None:
            columns = _header_columns(cells)
            continue
        if all(_SEPARATOR_CELL.match(cell) for cell in cells if cell):
            continue
        title = _EMPHASIS.sub("", _cell_at(cells, columns["title"])).strip()
        if not title:
            continue
        plant_cell = _cell_at(cells, columns["plant"])
        resolve_cell = _cell_at(cells, columns["resolve"])
        status_cell = _cell_at(cells, columns["status"])
        plant_numbers = _NUMBER.findall(plant_cell)
        resolve_numbers = _NUMBER.findall(resolve_cell)
        if _is_planned(status_cell):
            status: Status | None = None
        else:
            status = _normalize_status(status_cell)
        rows.append(
            LedgerRow(
                line=number,
                title=title,
                plant_chapter=int(plant_numbers[0]) if plant_numbers else None,
                # 回收列可能写多个章（「20、46」）：取最后一章作为最终回收。
                resolve_chapter=int(resolve_numbers[-1]) if resolve_numbers else None,
                status=status,
                status_text=status_cell,
                detail=_detail_of(resolve_cell),
            )
        )
    return rows


def existing_ids(root: Path) -> set[str]:
    """已登记的线索编号（用于迁移分配新编号时不撞车）。"""
    directory = Path(root) / CLUES_DIRNAME
    ids: set[str] = set()
    if not directory.is_dir():
        return ids
    for path in sorted(directory.glob("ch*.json")):
        suffix = path.stem[2:]
        if not suffix.isdigit():
            continue
        try:
            document = read_chapter_clues(root, int(suffix))
        except WorkspaceError:
            continue  # 坏文件留给对账报告，不挡迁移
        if document is not None:
            ids.update(entry.id for entry in document.entries)
    return ids


def _allocate(index: int) -> str:
    return f"CL-{index:02d}" if index < 100 else f"CL-{index}"


def _note(title: str, detail: str) -> str:
    return f"{title}：{detail}" if detail else title


def build_draft(
    rows: list[LedgerRow] | tuple[LedgerRow, ...],
    *,
    reserved: set[str] | frozenset[str] = frozenset(),
) -> MigrationDraft:
    """把手写表行转换成章级草稿；不能机械迁移的行进 ``skipped``。"""
    used = {value.upper() for value in reserved}
    counter = 0
    entries: list[MigrationEntry] = []
    plans: list[CluePlan] = []
    skipped: list[MigrationSkip] = []
    planned_ids: set[str] = set()

    def next_id() -> str:
        nonlocal counter
        while True:
            counter += 1
            candidate = _allocate(counter)
            if candidate.upper() not in used:
                used.add(candidate.upper())
                return candidate

    def skip(row: LedgerRow, reason: str) -> None:
        skipped.append(MigrationSkip(line=row.line, title=row.title, reason=reason))

    for row in rows:
        plant = row.plant_chapter
        if plant is None:
            skip(row, "缺埋设章，无法落到章级文件")
            continue
        planned = _is_planned(row.status_text)
        status = row.status
        resolve = row.resolve_chapter
        if not planned:
            if status is None:
                skip(row, f"状态无法识别：{row.status_text!r}")
                continue
            if status == "resolved":
                if resolve is None:
                    skip(row, "已收但缺回收章")
                    continue
                if resolve == plant:
                    skip(row, "埋收同章，需人工拆成两条登记")
                    continue

        # 只在确定能产出条目/计划时才分配编号，避免跳过的行留下编号空洞。
        clue_id = next_id()
        note = _note(row.title, row.detail)

        if planned:
            plans.append(
                CluePlan(
                    id=clue_id,
                    plant_chapter=plant,
                    resolve_chapter=resolve,
                    note=row.title,
                )
            )
            continue

        # planned 分支已 continue，走到这里状态一定识别出来了。
        assert status is not None
        if status == "resolved":
            assert resolve is not None  # 缺回收章的行在上面已跳过
            entries.append(
                MigrationEntry(
                    chapter=plant,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status="planted",
                        origin="migrated",
                        note=note,
                    ),
                )
            )
            entries.append(
                MigrationEntry(
                    chapter=resolve,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status="resolved",
                        origin="migrated",
                        note=note,
                        resolves=ResolvesRef(clue_id=clue_id, planted_in=plant),
                    ),
                )
            )
        elif status == "advanced" and resolve is not None and resolve > plant:
            entries.append(
                MigrationEntry(
                    chapter=plant,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status="planted",
                        origin="migrated",
                        note=note,
                    ),
                )
            )
            entries.append(
                MigrationEntry(
                    chapter=resolve,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status="advanced",
                        origin="migrated",
                        note=note,
                    ),
                )
            )
        elif status == "retired":
            entries.append(
                MigrationEntry(
                    chapter=plant,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status="retired",
                        origin="migrated",
                        note=note or "迁移：原表声明不收",
                    ),
                )
            )
        else:  # planted / advanced（回收章与埋设章相同，按本章状态记）
            entries.append(
                MigrationEntry(
                    chapter=plant,
                    entry=ClueEntry(
                        id=clue_id,
                        kind="伏笔",
                        status=status,
                        origin="migrated",
                        note=note,
                    ),
                )
            )
        if resolve is not None and clue_id not in planned_ids:
            planned_ids.add(clue_id)
            plans.append(
                CluePlan(
                    id=clue_id,
                    plant_chapter=plant,
                    resolve_chapter=resolve,
                    note=row.title,
                )
            )

    return MigrationDraft(entries=tuple(entries), plans=tuple(plans), skipped=tuple(skipped))


def apply_draft(
    root: Path,
    draft: MigrationDraft,
    *,
    revision: str = MIGRATION_REVISION,
) -> MigrationReport:
    """把草稿并入章级线索文件：只追加缺失条目，已有同编号/同内容条目跳过。"""
    by_chapter: dict[int, list[ClueEntry]] = {}
    for item in draft.entries:
        by_chapter.setdefault(item.chapter, []).append(item.entry)

    files: list[str] = []
    added = 0
    duplicates = 0
    for chapter in sorted(by_chapter):
        document = read_chapter_clues(root, chapter)
        existing: list[ClueEntry] = list(document.entries) if document else []
        seen_ids = {entry.id for entry in existing}
        seen_keys = {(entry.note, entry.status) for entry in existing}
        fresh: list[ClueEntry] = []
        for entry in by_chapter[chapter]:
            key = (entry.note, entry.status)
            if entry.id in seen_ids or key in seen_keys:
                duplicates += 1
                continue
            seen_ids.add(entry.id)
            seen_keys.add(key)
            fresh.append(entry)
        if not fresh:
            continue
        write_chapter_clues(root, chapter, revision, existing + fresh)
        files.append(f"{CLUES_DIRNAME}/ch{chapter}.json")
        added += len(fresh)
    return MigrationReport(files=tuple(files), added=added, duplicates=duplicates)


def suggest_plan_section(plans: list[CluePlan] | tuple[CluePlan, ...]) -> str:
    """生成可直接粘进 ``STRUCTURE.md`` 的「计划线索」节（迁移不自动改结构文件）。"""
    if not plans:
        return ""
    lines = ["## 计划线索", ""]
    for plan in plans:
        tail = f"第{plan.plant_chapter}章埋"
        if plan.resolve_chapter is not None:
            tail += f"、第{plan.resolve_chapter}章收"
        title = f" {plan.note}" if plan.note else ""
        lines.append(f"- {plan.id}{title}：{tail}")
    return "\n".join(lines) + "\n"

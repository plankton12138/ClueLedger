"""v1.3 线索工具：手写伏笔账迁移与投影刷新。

两个子命令，都默认演练、只在显式加 ``--apply`` / ``--write`` 时落盘：

- ``migrate``：把手写伏笔账表格解析成章级线索草稿（锚留空，待逐章重锚）。
- ``project``：把章级线索聚合渲染成 ``伏笔账.md`` 投影（幂等，可直接进 Git）。

运行：

```text
uv run python scripts/clue_ledger_tools.py migrate --root <作品目录> --ledger <伏笔账.md>
uv run python scripts/clue_ledger_tools.py migrate --root <作品目录> --ledger <伏笔账.md> --apply
uv run python scripts/clue_ledger_tools.py project --root <作品目录> --write
```
"""

from __future__ import annotations

import argparse
from pathlib import Path

from hastings.workspace.chapter_clues import (
    CLUES_DIRNAME,
    CluePlan,
    aggregate_clues,
    parse_planned_clues,
)
from hastings.workspace.clue_migration import (
    MigrationDraft,
    apply_draft,
    build_draft,
    existing_ids,
    parse_ledger_table,
    suggest_plan_section,
)
from hastings.workspace.clue_projection import (
    PROJECTION_FILENAME,
    PROJECTION_MARKER,
    refresh_projection,
    render_ledger,
)
from hastings.workspace.prose_ledger import LEDGER_FILENAME, load_ledger


def _chapters(root: Path) -> list[int]:
    """已入档的章号；台账缺失时退回扫描章级线索文件。"""
    chapters = sorted(load_ledger(root / LEDGER_FILENAME).chapters)
    if chapters:
        return chapters
    directory = root / CLUES_DIRNAME
    if not directory.is_dir():
        return []
    numbers: list[int] = []
    for path in directory.glob("ch*.json"):
        suffix = path.stem[2:]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return sorted(numbers)


def _plans(root: Path) -> list[CluePlan]:
    structure = root / "STRUCTURE.md"
    if not structure.is_file():
        return []
    return parse_planned_clues(structure.read_text(encoding="utf-8"))


def _show_draft(draft: MigrationDraft) -> None:
    by_chapter: dict[int, list[str]] = {}
    for item in draft.entries:
        by_chapter.setdefault(item.chapter, []).append(item.entry.id)
    print(
        f"解析结果：可迁移 {len(draft.entries)} 条章级条目"
        f"（涉及 {len(by_chapter)} 章）、计划线索 {len(draft.plans)} 条、"
        f"跳过 {len(draft.skipped)} 行"
    )
    for chapter in sorted(by_chapter):
        print(f"  第 {chapter} 章：{'、'.join(by_chapter[chapter])}")
    for item in draft.skipped:
        print(f"  跳过第 {item.line} 行「{item.title}」：{item.reason}")
    section = suggest_plan_section(draft.plans)
    if section:
        print("\n建议追加到 STRUCTURE.md（迁移不自动改结构文件）：\n")
        print(section)


def _is_projection(path: Path) -> bool:
    """文件头带自动生成标记才算投影；手写伏笔账不会被顺手覆盖。"""
    try:
        head = path.read_text(encoding="utf-8")[:400]
    except OSError:
        return False
    return PROJECTION_MARKER in head


def _migrate(args: argparse.Namespace) -> int:
    root = Path(args.root)
    ledger = Path(args.ledger)
    if not root.is_dir():
        print(f"作品目录不存在：{root}")
        return 1
    if not ledger.is_file():
        print(f"伏笔账文件不存在：{ledger}")
        return 1
    text = ledger.read_text(encoding="utf-8")
    if PROJECTION_MARKER in text:
        print("这份文件已经是自动生成的投影，不能作为迁移来源；迁移请用手写稿。")
        return 1
    rows = parse_ledger_table(text)
    if not rows:
        print("没有找到可解析的表格：需要含「埋 / 收 / 状态」三列的表头。")
        return 1
    draft = build_draft(rows, reserved=existing_ids(root) | {plan.id for plan in _plans(root)})
    _show_draft(draft)
    if not args.apply:
        print("\n（演练模式，未写入；确认无误后加 --apply 落盘）")
        return 0
    report = apply_draft(root, draft)
    if report.files:
        print(f"\n已写入：{'、'.join(report.files)}（新增 {report.added} 条）")
    else:
        print("\n没有新增条目。")
    if report.duplicates:
        print(f"已存在而跳过 {report.duplicates} 条（重复运行不会重复写入）")
    if args.apply_plans:
        _append_plans(root, draft)
    print("迁移条目没有锚：该章下一次修订时会提示重锚，投影里显示「未锚定」。")
    return 0


def _append_plans(root: Path, draft: MigrationDraft) -> None:
    """把迁移出的计划线索追加进 STRUCTURE.md（只在不已有该节时追加）。"""
    structure = root / "STRUCTURE.md"
    section = suggest_plan_section(draft.plans)
    if not section:
        return
    if not structure.is_file():
        print("\nSTRUCTURE.md 不存在，计划线索未写入；先补结构文件再手工粘下面这段：")
        print(section)
        return
    current = structure.read_text(encoding="utf-8")
    if "## 计划线索" in current:
        print("\nSTRUCTURE.md 已有「计划线索」节，未自动合并；请手工把下面几条补进去：")
        print(section)
        return
    updated = current.rstrip("\n") + "\n\n" + section
    structure.write_text(updated, encoding="utf-8")
    print(f"\n已把 {len(draft.plans)} 条计划线索追加到 STRUCTURE.md。")


def _project(args: argparse.Namespace) -> int:
    root = Path(args.root)
    if not root.is_dir():
        print(f"作品目录不存在：{root}")
        return 1
    chapters = _chapters(root)
    plans = _plans(root)
    if not args.write:
        print(render_ledger(aggregate_clues(root, chapters, plans=plans), plans))
        return 0
    target = root / args.out
    if target.is_file() and not _is_projection(target) and not args.force:
        print(
            f"目标文件不是自动生成的投影：{target}\n"
            "先跑 migrate 把表格迁进章级线索；确认已迁移完成后加 --force 把该文件转为投影。"
        )
        return 1
    result = refresh_projection(root, chapters, plans=plans, filename=args.out)
    if result.changed:
        print(f"已刷新投影：{result.path}（{len(chapters)} 章）")
    else:
        print(f"投影无变化：{result.path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="v1.3 线索迁移与伏笔账投影")
    sub = parser.add_subparsers(dest="command", required=True)

    migrate = sub.add_parser("migrate", help="把手写伏笔账迁成章级线索草稿")
    migrate.add_argument("--root", required=True, help="作品目录")
    migrate.add_argument("--ledger", required=True, help="手写伏笔账 markdown 文件")
    migrate.add_argument("--apply", action="store_true", help="真正写入（默认演练）")
    migrate.add_argument(
        "--apply-plans",
        action="store_true",
        help="同时把计划线索追加到 STRUCTURE.md（仅在该节不存在时）",
    )
    migrate.set_defaults(handler=_migrate)

    project = sub.add_parser("project", help="刷新伏笔账投影")
    project.add_argument("--root", required=True, help="作品目录")
    project.add_argument("--out", default=PROJECTION_FILENAME, help="投影文件名（相对作品目录）")
    project.add_argument("--write", action="store_true", help="写盘（默认打印到标准输出）")
    project.add_argument("--force", action="store_true", help="覆盖没有生成标记的手写文件")
    project.set_defaults(handler=_project)

    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())

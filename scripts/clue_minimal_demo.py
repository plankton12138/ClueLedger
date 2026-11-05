"""v1.3 章级线索结构：最小流程演示（可行性切片）。

模拟一条线索的完整生命周期与对账行为，全部走 ``chapter_clues`` 纯模块：
埋设 → 推进 → 回收 → 聚合视图；外加三个反例（空响拦截、改稿失联、计划哑火）。

运行：``uv run python scripts/clue_minimal_demo.py``
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from hastings.workspace.chapter_clues import (
    ClueEntry,
    CluePlan,
    ResolvesRef,
    aggregate_clues,
    anchor_paragraph,
    clues_path,
    validate_commit,
    write_chapter_clues,
)
from hastings.workspace.errors import WorkspaceError

CHAPTER_1 = "老魏把箱子放在桌上。\n箱扣上的漆掉了一块，露出底下的铁色。\n他捻了捻烟丝，没点火。\n"
CHAPTER_3 = (
    "箱子还在原处。\n"
    "箱扣上的漆掉了一块，露出底下的铁色。我伸手碰了一下，凉得像井水。\n"
    "老魏瞪了我一眼。\n"
)
CHAPTER_5 = (
    "「这不是普通的箱子。」老魏终于开口。\n"
    "他把箱扣翻过来——内沿刻着一个年份，正是那批货出库的日子。\n"
)


def _show(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 52 - len(title)))


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        _show("第 1 章：埋设（计划线索 CL-01 + 意外线索 EM-1-1）")
        write_chapter_clues(
            root,
            1,
            "r1",
            [
                ClueEntry(
                    id="CL-01",
                    kind="伏笔",
                    status="planted",
                    origin="planned",
                    note="箱扣缺漆，箱子来历未明",
                    anchor=anchor_paragraph("箱扣上的漆掉了一块，露出底下的铁色。", "r1"),
                ),
                ClueEntry(
                    id="EM-1-1",
                    kind="物件",
                    status="planted",
                    origin="emergent",
                    note="捻烟丝不点火：老魏有心事（意外长出的细节）",
                    anchor=anchor_paragraph("他捻了捻烟丝，没点火。", "r1"),
                ),
            ],
        )
        print(clues_path(root, 1).read_text(encoding="utf-8"))

        _show("反例 A：空响（回收没埋过的 CL-99）→ 提交门拦截")
        stray = [
            ClueEntry(
                id="CL-99",
                kind="悬念",
                status="resolved",
                origin="planned",
                resolves=ResolvesRef(clue_id="CL-99", planted_in=1),
            )
        ]
        try:
            validate_commit(root, 2, stray)
            write_chapter_clues(root, 2, "r2", stray)
        except WorkspaceError as exc:
            print(f"  拦截：[{exc.code}] {exc.why}")
            print(f"  建议：{exc.fix}")

        _show("第 3 章：推进（CL-01 重新锚定到新段落）")
        write_chapter_clues(
            root,
            3,
            "r3",
            [
                ClueEntry(
                    id="CL-01",
                    kind="伏笔",
                    status="advanced",
                    origin="planned",
                    note="「我」伸手碰到箱扣，凉得反常",
                    anchor=anchor_paragraph(
                        "箱扣上的漆掉了一块，露出底下的铁色。我伸手碰了一下，凉得像井水。",
                        "r3",
                    ),
                )
            ],
        )
        print("  CL-01 → advanced（第 3 章锚点已更新）")

        _show("第 5 章：回收（CL-01 resolved，resolves 指回第 1 章埋设）")
        write_chapter_clues(
            root,
            5,
            "r5",
            [
                ClueEntry(
                    id="CL-01",
                    kind="伏笔",
                    status="resolved",
                    origin="planned",
                    note="老魏说出箱子来历（内沿年份）",
                    resolves=ResolvesRef(clue_id="CL-01", planted_in=1),
                )
            ],
        )
        print("  CL-01 → resolved")

        _show("聚合视图 + 对账（正常路径：无发现项）")
        plans = [
            CluePlan(id="CL-01", plant_chapter=1, resolve_chapter=5, note="箱扣缺漆"),
            CluePlan(id="EM-1-1", plant_chapter=1),
        ]
        aggregate = aggregate_clues(
            root,
            [1, 2, 3, 4, 5],
            prose_by_chapter={1: CHAPTER_1, 3: CHAPTER_3, 5: CHAPTER_5},
            plans=plans,
        )
        for clue_id, state in sorted(aggregate.states.items()):
            print(
                f"  {clue_id}: {state.status}"
                f"（埋第 {state.planted_in} 章"
                + (f"，收第 {state.resolved_in} 章" if state.resolved_in else "")
                + f"，最近更新第 {state.last_chapter} 章）"
            )
        print(f"  发现项：{len(aggregate.findings)} 条")
        for finding in aggregate.findings:
            print(f"  [{finding.code}] {finding.message}")

        _show("反例 B：改稿动到锚定段落 → 失联报告（带前缀，供重锚）")
        rewritten = CHAPTER_3.replace("凉得像井水", "凉得扎手")
        aggregate = aggregate_clues(root, [1, 3, 5], prose_by_chapter={3: rewritten})
        for finding in aggregate.findings:
            print(f"  [{finding.code}] {finding.message}")

        _show("反例 C：计划线索声明了却没写 → 哑火 / 悬枪（报告不拦截）")
        late_plans = plans + [
            CluePlan(id="CL-07", plant_chapter=2, resolve_chapter=5, note="怀表停走的时刻")
        ]
        aggregate = aggregate_clues(root, [1, 2, 3, 4, 5], plans=late_plans)
        for finding in aggregate.findings:
            print(f"  [{finding.code}] {finding.message}")

        _show("结论")
        print("  章级权威文件 × 3（ch1/ch3/ch5）落盘；聚合视图即时派生。")
        print("  空响在提交门拦截（确定性矛盾，不落盘）；失联/哑火进报告不堵主路径。")


if __name__ == "__main__":
    main()

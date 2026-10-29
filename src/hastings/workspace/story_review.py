"""候选正文的结构核对：必需呈现、真相边界与前提（1.0.1 P4）。

模型写的检查报告只能回答「它认为有没有问题」。这一层回答的是另外两件事：

- **哪些要求必须被逐条覆盖**：选中节点的线索呈现、作者真相不能被写成已发生的事实、
  角色在这一位置还不知道的事、必需前提。要求由应用生成，模型不能改。
- **哪些能用程序判断**：呈现是否真的出现、真相原文是否被直接写出来。其余（角色是否
  「说漏」、前提是否被违背）只能交给语义核对，并在要求里写明「需要判断」。

程序能判断的部分留位置（路径与字符区间），报告可以引用原文；判断不了的部分不伪装成
「已通过」，只作为要求交给核对阶段。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from hastings.workspace.story_context import StoryContextSnapshot
from hastings.workspace.story_models import StoryNode, StoryVariant

__all__ = [
    "StoryCheck",
    "StoryReview",
    "build_story_review",
]

CheckKind = Literal["present", "hidden", "semantic"]


@dataclass(frozen=True, slots=True)
class StoryCheck:
    """一条核对要求；``needle`` 只对程序能判断的两类有意义。"""

    requirement_id: str
    text: str
    kind: CheckKind
    needle: str = ""


@dataclass(frozen=True, slots=True)
class StoryReview:
    """核对阶段的结构要求、程序发现与渲染后的提示块。"""

    checks: tuple[StoryCheck, ...] = ()
    findings: tuple[dict[str, Any], ...] = ()
    text: str = ""

    @property
    def empty(self) -> bool:
        return not self.checks and not self.findings

    def summary(self) -> dict[str, object]:
        return {
            "checks": [
                {"id": item.requirement_id, "kind": item.kind} for item in self.checks
            ],
            "findings": len(self.findings),
        }


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _selected_nodes(
    variant: StoryVariant, frozen: StoryContextSnapshot
) -> list[StoryNode]:
    return [
        variant.nodes[node_id]
        for node_id in frozen.node_ids
        if node_id in variant.nodes
    ]


def build_story_review(
    variant: StoryVariant,
    frozen: StoryContextSnapshot,
    *,
    documents: Mapping[str, str],
) -> StoryReview:
    """按冻结结构生成核对要求，并对给定的正文做程序能判断的检查。"""
    nodes = _selected_nodes(variant, frozen)
    if not nodes:
        return StoryReview()

    normalized = {path: _normalize(text) for path, text in documents.items()}
    checks: list[StoryCheck] = []
    findings: list[dict[str, Any]] = []

    used_clues: list[str] = []
    for node in nodes:
        for use in node.clue_uses:
            if use.clue_id not in used_clues:
                used_clues.append(use.clue_id)
            requirement_id = f"present:{node.id}:{use.clue_id}"
            checks.append(
                StoryCheck(
                    requirement_id=requirement_id,
                    text=f"「{node.title}」要呈现：{use.presentation}",
                    kind="present",
                    needle=use.presentation,
                )
            )
            if not any(use.presentation in text for text in normalized.values()):
                findings.append(
                    {
                        "requirement_id": requirement_id,
                        "severity": "advisory",
                        "note": (
                            f"候选正文里没有找到这处呈现：{use.presentation}。"
                            "如果改用别的写法，请在报告里说明对应的原句。"
                        ),
                        "decision_source": "app",
                    }
                )

    for clue_id in used_clues:
        clue = variant.clues.get(clue_id)
        if clue is None or not clue.author_truth or len(clue.author_truth.strip()) < 2:
            continue
        requirement_id = f"hidden:{clue.id}"
        truth = clue.author_truth.strip()
        checks.append(
            StoryCheck(
                requirement_id=requirement_id,
                text=f"作者真相不能被写成已经发生的事实：{truth}",
                kind="hidden",
                needle=truth,
            )
        )
        for path, text in normalized.items():
            index = text.find(truth)
            if index < 0:
                continue
            findings.append(
                {
                    "requirement_id": requirement_id,
                    "severity": "blocking",
                    "note": (
                        "正文直接写出了作者真相。角色可以说、可以猜，"
                        "但叙述不能把它当成已经确认的事实。"
                    ),
                    "path": path,
                    "start": index,
                    "end": index + len(truth),
                    "quote": truth,
                    "decision_source": "app",
                }
            )

    for node in nodes:
        for change in node.knowledge_changes:
            checks.append(
                StoryCheck(
                    requirement_id=f"knowledge:{node.id}:{change.subject_id}",
                    text=(
                        f"{change.subject_id} 在「{node.title}」时"
                        f"{'知道' if change.stance == 'knows' else '相信'}：{change.claim}"
                        "（要判断：正文是否让这个角色越过了这条边界）"
                    ),
                    kind="semantic",
                )
            )

    text = _render(variant, checks, findings)
    return StoryReview(checks=tuple(checks), findings=tuple(findings), text=text)


def _render(
    variant: StoryVariant, checks: list[StoryCheck], findings: list[dict[str, Any]]
) -> str:
    if not checks and not findings:
        return ""
    lines = [
        "## 结构核对要求（由应用生成，不能改写或跳过）",
        f"方案 `{variant.id}` · 修订 `{variant.revision}`。逐条核对下面的要求，"
        "在报告里用要求编号说明覆盖情况；不能修改要求本身。",
    ]
    for item in checks:
        lines.append(f"- [{item.requirement_id}] {item.text}")
    if findings:
        lines.append("")
        lines.append("## 应用已经发现的问题（必须处理或说明）")
        for finding in findings:
            where = ""
            if finding.get("path"):
                where = f"（{finding['path']}:{finding['start']}-{finding['end']}）"
            lines.append(
                f"- [{finding['requirement_id']}] {finding['severity']}：{finding['note']}{where}"
            )
    return "\n".join(lines)

"""结构的可读投影：正文关联是否仍然成立、核对是否过期（1.0.1 P1）。

这一层不写任何东西，也不调用模型。它回答两个界面必须如实显示的问题：

* 这条「节点 ↔ 正文」的关联现在还指得到原文吗？（文件变了、区间变了、文件没了）
* 这次核对还算数吗？（结构改过、正文改过）

判定刻意保守：结构有任何变化就认为核对过期。更细的「只有被改到的对象才失效」
需要变更与作用域的对应关系，那是 P3 装配实际输入时才有条件做的事；在那之前，
把过期的核对继续显示为「已核对」比多报几次过期危险得多。
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from hastings.workspace.story_models import Assessment, ProseBinding, StoryVariant

__all__ = [
    "AssessmentState",
    "BindingState",
    "assessment_state",
    "binding_state",
    "normalize_prose",
    "variant_summary",
]

BindingStatus = Literal["valid", "stale", "missing"]
AssessmentStatus = Literal["valid", "stale"]


@dataclass(frozen=True, slots=True)
class BindingState:
    binding_id: str
    status: BindingStatus
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AssessmentState:
    assessment_id: str
    status: AssessmentStatus
    reason: str = ""


def normalize_prose(raw: bytes) -> str:
    """与服务端读取规则一致：UTF-8 解码后把 CRLF／CR 统一成 LF。

    位置一律按规范化后的 Unicode 码点计算；前端不能拿 UTF-16 下标直接切正文。
    """
    return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")


def binding_state(binding: ProseBinding, raw: bytes | None) -> BindingState:
    if raw is None:
        return BindingState(binding.id, "missing", "正文文件不存在")
    if hashlib.sha256(raw).hexdigest() != binding.digest:
        return BindingState(binding.id, "stale", "正文在关联之后被修改")
    text = normalize_prose(raw)
    if not 0 <= binding.start < binding.end <= len(text):
        return BindingState(binding.id, "stale", "关联区间超出当前正文长度")
    if text[binding.start : binding.end] != binding.quote:
        return BindingState(binding.id, "stale", "关联区间的内容已经不是原来的句子")
    return BindingState(binding.id, "valid")


def assessment_state(
    assessment: Assessment,
    variant: StoryVariant,
    read: Callable[[str], bytes | None],
) -> AssessmentState:
    if assessment.structure_revision != variant.revision:
        return AssessmentState(
            assessment.id, "stale", "结构在这次核对之后有变化，需要重新核对"
        )
    for path, digest in assessment.prose_digests.items():
        raw = read(path)
        if raw is None:
            return AssessmentState(assessment.id, "stale", f"正文文件不存在：{path}")
        if hashlib.sha256(raw).hexdigest() != digest:
            return AssessmentState(assessment.id, "stale", f"正文在这次核对之后有变化：{path}")
    return AssessmentState(assessment.id, "valid")


def variant_summary(
    variant: StoryVariant, read: Callable[[str], bytes | None]
) -> dict[str, object]:
    """界面上「当前构思」的概览数字；不推断任何语义结论。"""
    bindings = [binding_state(item, read(item.path)) for item in variant.bindings.values()]
    checks = [assessment_state(item, variant, read) for item in variant.assessments.values()]
    unresolved = [
        clue.id for clue in variant.clues.values() if clue.resolution.intent != "explain"
    ]
    return {
        "revision": variant.revision,
        "stages": len(variant.stages),
        "nodes": len(variant.nodes),
        "clues": len(variant.clues),
        "threads": len(variant.threads),
        "constraints": len(variant.constraints),
        "unresolved_clue_ids": sorted(unresolved),
        "stale_binding_ids": sorted(item.binding_id for item in bindings if item.status != "valid"),
        "stale_assessment_ids": sorted(
            item.assessment_id for item in checks if item.status == "stale"
        ),
    }

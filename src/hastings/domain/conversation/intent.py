"""V03-7.1 结构化意图三分：讨论 / 候选 / 执行（计划 M10 §1–4）。

作者的自然语言消息先被分类成三种意图之一，分类结果决定能否写正式内容：

- ``discussion``（讨论）：**无写权限**。「假如姐姐是凶手会怎样」一类假设只产生回复，
  不携带任何非空写范围；
- ``candidate``（候选）：「给我一个替代写法」「先看看方案」产生可比较的候选/方案，
  仍是只读（``write_scope=None``），不得隐式进入发布；
- ``execution``（执行）：范围明确授权的执行请求（「按这个整体修改」）才携带写范围。

分类是**确定性纯函数**：只看作者文本，不看模型输出。模型说「已获授权」不构成授权
（M10 第 2 条）——凭据来源只能是 ``author_message`` / ``author_button``，由应用层
在凭据构建边界落地。无法判断时一律降级为 ``discussion``（最安全），不猜执行意图。

本模块不依赖应用层（依赖方向：domain ← application）。``function`` 字段使用与
``application/execution.GrantFunction`` 相同的字面量，由应用层收编校验。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

__all__ = [
    "CANDIDATE_FUNCTION",
    "EXECUTION_FUNCTIONS",
    "IntentClassification",
    "IntentType",
    "KeyChoice",
    "classify_author_intent",
    "classify_intent",
]

IntentType = Literal["discussion", "candidate", "execution"]

#: 候选默认功能：产生可比较的草案/候选，无正式写权限。
CANDIDATE_FUNCTION = "draft"
#: 执行功能：整体调整 → holistic_revision；定向修订 → revision。
EXECUTION_FUNCTIONS = ("revision", "holistic_revision")

# 假设/讨论线索：只回应，不改故事（M10 第 1 条）。
_DISCUSSION_CUES: tuple[str, ...] = (
    "假如",
    "如果",
    "假设",
    "要是",
    "会不会",
    "会怎样",
    "怎么样呢",
    "我想知道",
    "你觉得",
    "讨论",
    "聊聊",
    "说说",
    "探讨",
    "为什么",
)

# 只读方案线索：「先看看方案」不改正式内容（M10 第 4 条）。
_READONLY_CUES: tuple[str, ...] = (
    "先看看方案",
    "先看方案",
    "看看方案",
    "看看要改什么",
    "看看有什么",
    "只是看看",
    "只看看",
    "先分析",
    "先不改",
    "别改",
    "不要改",
    "先别动",
    "暂时不改",
    "不动正式",
    "不改正式",
)

# 候选线索：产生候选/备选，不是正式写入。
_CANDIDATE_CUES: tuple[str, ...] = (
    "替代写法",
    "另一个写法",
    "另一种写法",
    "另一版",
    "另一个版本",
    "备选",
    "试写",
    "试着写",
    "试一下",
    "草稿",
    "生成候选",
    "给我写",
    "给一个",
    "方案",
    "建议",
)

# 执行线索：范围明确的授权写入。
_EXECUTION_CUES: tuple[str, ...] = (
    "按这个改",
    "按此改",
    "按这个修改",
    "按这个整体修改",
    "按此整体修改",
    "整体修改",
    "统一修改",
    "全部修改",
    "全部改成",
    "改成",
    "改为",
    "换成",
    "换掉",
    "替换为",
    "替换",
    "重写第",
    "去掉",
    "删掉",
    "直接改",
    "执行修改",
)

# 整体调整线索：把执行升级为 holistic_revision。
_OVERALL_CUES: tuple[str, ...] = (
    "整体",
    "统一",
    "全部",
    "全书",
    "更换凶手",
    "换凶手",
    "换手法",
    "改手法",
    "换背景",
    "改背景",
)


def _hit(text: str, cues: Sequence[str]) -> str | None:
    """返回第一个命中的线索词，便于把分类依据写进 reason。"""
    for cue in cues:
        if cue in text:
            return cue
    return None


def classify_intent(text: str) -> IntentType:
    """作者文本 → 三分意图；未知一律降级为 discussion（最安全）。

    优先级：假设/讨论 > 只读方案 > 候选 > 执行。假设线索优先，保证
    「假如改成……会怎样」这类假想不会被误判成执行写入。
    """
    stripped = text.strip()
    if not stripped:
        return "discussion"
    if _hit(stripped, _DISCUSSION_CUES):
        return "discussion"
    if _hit(stripped, _READONLY_CUES) or _hit(stripped, _CANDIDATE_CUES):
        return "candidate"
    if _hit(stripped, _EXECUTION_CUES):
        return "execution"
    return "discussion"


@dataclass(frozen=True, slots=True)
class KeyChoice:
    """影响执行的关键选择（最多 1–2 个）；合法空结果时返回空元组。"""

    where: str
    question: str
    options: tuple[str, ...]

    def to_wire(self) -> dict[str, Any]:
        return {"where": self.where, "question": self.question, "options": list(self.options)}


@dataclass(frozen=True, slots=True)
class IntentClassification:
    """三分意图 + 分析/写范围 + 待澄清选择；只读，不含任何写能力。"""

    intent: IntentType
    function: str | None
    analysis_scope: tuple[str, ...]
    write_scope: tuple[str, ...] | None
    requires_choice: bool
    choices: tuple[KeyChoice, ...]
    reason: str

    def to_wire(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "function": self.function,
            "analysis_scope": list(self.analysis_scope),
            "write_scope": None if self.write_scope is None else list(self.write_scope),
            "requires_choice": self.requires_choice,
            "choices": [c.to_wire() for c in self.choices],
            "reason": self.reason,
        }


def _execution_choices(function: str | None, target_ids: tuple[str, ...]) -> tuple[KeyChoice, ...]:
    """执行意图的澄清问题：范围已知则空（同范围不重复审批）。"""
    if target_ids:
        return ()
    choices: list[KeyChoice] = [
        KeyChoice(
            where="intent.scope",
            question="这次修改要覆盖哪些内容？",
            options=("当前章", "指定章节", "全书相关章节"),
        )
    ]
    if function == "holistic_revision":
        choices.append(
            KeyChoice(
                where="intent.preserve",
                question="已写正文里哪些内容要保留？",
                options=("保留背景与视角", "保留已揭示线索", "由你判断"),
            )
        )
    return tuple(choices[:2])


def classify_author_intent(
    text: str,
    *,
    target_ids: Sequence[str] = (),
) -> IntentClassification:
    """作者文本 + 已知目标 → 完整意图分类（讨论/候选/执行 + 范围 + 选择）。

    ``analysis_scope`` 是只读分析范围（来自作者显式引用/目标）；
    ``write_scope`` 只有 execution 才非 None，discussion/candidate 一律 None。
    """
    intent = classify_intent(text)
    scope = tuple(dict.fromkeys(str(t) for t in target_ids if str(t)))
    if intent == "discussion":
        return IntentClassification(
            intent=intent,
            function=None,
            analysis_scope=scope,
            write_scope=None,
            requires_choice=False,
            choices=(),
            reason="假设/讨论线索：只回应，不改正式内容（M10 第 1 条）",
        )
    if intent == "candidate":
        cue = _hit(text, _READONLY_CUES) or _hit(text, _CANDIDATE_CUES) or ""
        return IntentClassification(
            intent=intent,
            function=CANDIDATE_FUNCTION,
            analysis_scope=scope,
            write_scope=None,
            requires_choice=False,
            choices=(),
            reason=f"候选线索（{cue}）：产生可比较的候选，不隐式发布（M10 第 4 条）",
        )
    cue = _hit(text, _EXECUTION_CUES) or ""
    overall = _hit(text, _OVERALL_CUES)
    function = "holistic_revision" if overall else "revision"
    choices = _execution_choices(function, scope)
    return IntentClassification(
        intent=intent,
        function=function,
        analysis_scope=scope,
        write_scope=scope,
        requires_choice=bool(choices),
        choices=choices,
        reason=(
            f"执行线索（{cue}）：范围明确授权"
            if scope
            else f"执行线索（{cue}）：写范围待作者选定关键选择"
        ),
    )

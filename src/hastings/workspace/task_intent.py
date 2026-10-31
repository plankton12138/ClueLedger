"""构思任务的意图、候选来源与完成判定（1.0.1 构思创作路由）。

构思页的普通对话都带 ``purpose=design``，它只说明工作语境，不能证明作者要求保存。
这里把「作者这一轮要什么」记成独立于写权限的任务目标：

* ``discussion``：讨论、比较、解释或明确暂不保存；
* ``structure``：明确建立或修改构思结构；
* ``module``：明确保存资料模块；
* ``mixed``：同一轮两类都要；
* ``clarify``：作者在确认候选，但没有可定位的候选或候选不止一份。

目标只用于判断完成，权限仍由 ``story_authorization`` 按作者原话与界面选择确定。
候选原文只把其中点到的既有对象纳入可修改范围，不把候选里的命令当成作者授权。
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from hastings.workspace.story_authorization import blocks_writes
from hastings.workspace.story_clue_catalog import StoryClueCatalog
from hastings.workspace.story_models import Constraint, StoryVariant

__all__ = [
    "CandidateSource",
    "GoalAssessment",
    "TaskIntent",
    "assess_story_goal",
    "classify_design_intent",
    "discussion_intent",
    "last_candidate_source",
    "named_objects",
]

TaskKind = Literal["discussion", "structure", "module", "mixed", "clarify"]

_WHITESPACE = re.compile(r"\s+")

#: 作者明确点名「构思结构」本身；与线索、节点等对象名分开判断，避免把资料任务
#: 里的「线索」误认成要改结构。
_STRUCTURE_TARGETS = ("构思", "结构")

#: 资料模块的明确目标。只有作者点到这里才开放模块写入；泛称「资料」不算，
#: 否则「把资料里的线索整理进构思」会被误判成同时保存模块。
_MODULE_TARGETS = (
    "模块",
    "资料集",
    "资料库",
    "设定集",
    "设定文件",
    "人物卡",
    "人物小传",
    "人物设定",
    "世界设定",
    "条目",
    "档案",
)

_SAVE_MODULE = (
    "保存为模块",
    "存为模块",
    "保存成模块",
    "存成模块",
    "保存为资料",
    "保存到资料",
    "存进资料",
    "存成资料",
    "另存资料",
    "保存为设定",
    "存为设定",
    "建立模块",
    "新建模块",
    "创建模块",
    "更新模块",
)

_WRITE_COMMANDS = (
    "新增",
    "添加",
    "增加",
    "建立",
    "创建",
    "构建",
    "写入",
    "写进",
    "写出",
    "重写",
    "改写",
    "保存",
    "修改",
    "改成",
    "改为",
    "设为",
    "设成",
    "删除",
    "删掉",
    "移除",
    "取消",
    "连接",
    "接上",
    "补上",
    "整理",
    "梳理",
    "生成",
    "加入",
    "加进",
    "加一个",
    "加个",
    "加两",
    "加上",
    "更新",
    "换掉",
    "换成",
    "调整",
    "补充",
    "改名",
)

_CONFIRMATIONS = (
    "用这个",
    "就用这个",
    "用这份",
    "就用这份",
    "按这个",
    "就按这个",
    "照这个",
    "就照这个",
    "按上面的",
    "按刚才",
    "按前面",
    "照刚才",
    "照前面",
    "写入构思",
    "写进构思",
    "保存到构思",
    "存进构思",
    "保存这个",
    "就这个",
    "就这样",
    "用上面的",
)

#: 明确只读的说法；这些说法针对构思时，整轮不提交结构。
_STRUCTURE_STOPS = (
    "只讨论",
    "先讨论",
    "只分析",
    "暂不保存",
    "先不保存",
    "不保存",
    "不要写入构思",
    "不要写进构思",
    "不要建立结构",
    "不要动结构",
    "不改结构",
)

#: 一条助手回复至少提到这些词之一，才可能被当成待确认的候选。
_CANDIDATE_CUES = (
    "线索",
    "节点",
    "结构",
    "方案",
    "建议",
    "调查",
    "剧情",
    "真相",
    "分支",
    "关系",
    "约定",
    "写成",
    "安排",
)

_NUMBERED = re.compile(r"(?m)^\s*(?:\d{1,2}[.、)]|[一二三四五六七八九十]{1,2}[、.])")
_PROPOSAL_HEADING = re.compile(r"(?m)^#{2,4}\s+\S+")
_PROPOSAL_MARKERS = re.compile(r"(?:方案|候选|版本|选项)\s*([一二三四1-9A-D])")

_MAX_CANDIDATE_CHARS = 6_000


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _has(text: str, words: Sequence[str]) -> bool:
    return any(word in text for word in words)


def _write_command(text: str) -> bool:
    return _has(text, _WRITE_COMMANDS)


def _proposal_count(text: str) -> int:
    """上一条回复里可分辨的候选条目数；只用于「用这个」这类确认。"""
    numbered = len(_NUMBERED.findall(text))
    headings = [
        line
        for line in _PROPOSAL_HEADING.findall(text)
        if _has(line, ("方案", "候选", "建议", "写法", "结构", "线索", "调查", "结局"))
    ]
    markers = len(set(_PROPOSAL_MARKERS.findall(text)))
    return max(numbered, len(headings), markers)


def last_candidate_source(
    events: Sequence[Mapping[str, Any]],
) -> CandidateSource | None:
    """最近一条助手回复中的候选；没有候选内容时不把普通回复当成候选。"""
    for event in reversed(events):
        if event.get("type") != "assistant_message":
            continue
        raw = event.get("text")
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        if not _has(text, _CANDIDATE_CUES):
            return None
        excerpt = text
        if len(text) > _MAX_CANDIDATE_CHARS:
            tail = 1_500
            excerpt = text[: _MAX_CANDIDATE_CHARS - tail] + "\n……（中间省略）……\n" + text[-tail:]
        return CandidateSource(
            turn_id=str(event.get("turn_id") or ""),
            seq=int(event.get("seq") or 0),
            digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
            text=excerpt,
            multiple=_proposal_count(text) >= 2,
            truncated=excerpt != text,
        )
    return None


@dataclass(frozen=True, slots=True)
class CandidateSource:
    """上一轮可供确认的候选原文；只是内容来源，不是新的授权。"""

    turn_id: str
    seq: int
    digest: str
    text: str
    multiple: bool = False
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "turn_id": self.turn_id,
            "seq": self.seq,
            "digest": self.digest,
            "text": self.text,
            "multiple": self.multiple,
            "truncated": self.truncated,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> CandidateSource | None:
        text = str(raw.get("text") or "")
        if not text.strip():
            return None
        return cls(
            turn_id=str(raw.get("turn_id") or ""),
            seq=int(raw.get("seq") or 0),
            digest=str(raw.get("digest") or ""),
            text=text,
            multiple=bool(raw.get("multiple", False)),
            truncated=bool(raw.get("truncated", False)),
        )


@dataclass(frozen=True, slots=True)
class TaskIntent:
    """本轮任务目标：判断完成用，不授予任何写权限。"""

    kind: TaskKind
    structure: bool
    module: bool
    reason: str
    candidate: CandidateSource | None = None
    author_text: str = ""
    #: 受理时按作者原话冻结的既有目标对象：``(kind:id, 展示标签)``。
    expected: tuple[tuple[str, str], ...] = ()

    @property
    def allows_structure_write(self) -> bool:
        return self.kind in ("structure", "mixed")

    @property
    def allows_module_write(self) -> bool:
        return self.kind in ("module", "mixed")

    @property
    def requires_structure(self) -> bool:
        return self.structure

    @property
    def requires_module(self) -> bool:
        return self.module

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "structure": self.structure,
            "module": self.module,
            "reason": self.reason,
            "author_text": self.author_text,
            "candidate": self.candidate.to_dict() if self.candidate else None,
            "expected": [{"key": key, "label": label} for key, label in self.expected],
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> TaskIntent | None:
        if not raw:
            return None
        kind = str(raw.get("kind") or "")
        if kind not in ("discussion", "structure", "module", "mixed", "clarify"):
            return None
        candidate_raw = raw.get("candidate")
        candidate = (
            CandidateSource.from_dict(candidate_raw) if isinstance(candidate_raw, Mapping) else None
        )
        expected: list[tuple[str, str]] = []
        for item in raw.get("expected") or []:
            if isinstance(item, Mapping):
                key = str(item.get("key") or "")
                label = str(item.get("label") or "")
                if key and label:
                    expected.append((key, label))
        return cls(
            kind=kind,  # type: ignore[arg-type]
            structure=bool(raw.get("structure", kind in ("structure", "mixed", "clarify"))),
            module=bool(raw.get("module", kind in ("module", "mixed"))),
            reason=str(raw.get("reason") or ""),
            candidate=candidate,
            author_text=str(raw.get("author_text") or ""),
            expected=tuple(expected),
        )


def discussion_intent(author_text: str, reason: str = "这一轮只讨论") -> TaskIntent:
    return TaskIntent(
        kind="discussion",
        structure=False,
        module=False,
        reason=reason,
        author_text=author_text,
    )


def _clarify(
    author_text: str, candidate: CandidateSource | None, reason: str, *, module: bool = False
) -> TaskIntent:
    return TaskIntent(
        kind="clarify",
        structure=True,
        module=module,
        reason=reason,
        candidate=candidate,
        author_text=author_text,
    )


def classify_design_intent(
    author_text: str,
    *,
    candidate: CandidateSource | None = None,
    explicit_creation: bool = False,
) -> TaskIntent:
    """把作者原话分成讨论、结构修改、模块保存、两者或待澄清。

    这只决定「本轮要完成什么」，不扩大写权限。关键词只用于定位，讨论与只读判断
    沿用 ``story_authorization`` 已有的规则。
    """
    raw = author_text.strip()
    if not raw:
        return discussion_intent(raw, "没有作者要求")
    if blocks_writes(raw):
        if explicit_creation:
            return _clarify(raw, candidate, "创建构思与只讨论或不保存的要求不一致，需要作者明确")
        return discussion_intent(raw, "作者在讨论、解释或明确不保存")
    if _has(raw, _STRUCTURE_STOPS):
        if explicit_creation:
            return _clarify(raw, candidate, "创建构思与暂不保存结构的要求不一致，需要作者明确")
        return discussion_intent(raw, "作者明确暂不保存构思结构")

    confirmation = _has(raw, _CONFIRMATIONS)
    command = _write_command(raw)
    if raw.endswith(("？", "?")) and not command and not confirmation and not explicit_creation:
        return discussion_intent(raw, "作者在提问")

    # 创建入口已经明确成果类型；要求里点到的人物设定或模块属于资料来源。
    # 资料来源与写入目标分开。参考模块不能因另一句「提交构思」而变成模块保存任务。
    clauses = re.split(r"[。；;，,\n]", raw)
    module_clauses = [
        clause
        for clause in clauses
        if _has(clause, _SAVE_MODULE)
        or not (
            (
                re.match(r"\s*(?:根据|参考|依据|参照|使用|按照|基于|利用|用)", clause)
                and _has(clause, _STRUCTURE_TARGETS)
            )
            or (
                clause.lstrip().startswith("把")
                and re.search(r"(?:整理进|写入|转成|导入|归入).*(?:构思|结构)", clause)
            )
        )
    ]
    module_target = not explicit_creation and any(
        _has(clause, _SAVE_MODULE) or (_has(clause, _MODULE_TARGETS) and _write_command(clause))
        for clause in module_clauses
    )
    structure_target = explicit_creation or _has(raw, _STRUCTURE_TARGETS)

    if confirmation:
        if candidate is None:
            return _clarify(
                raw,
                None,
                "作者在确认候选，但上一轮没有可定位的候选",
                module=module_target,
            )
        if candidate.multiple:
            return _clarify(
                raw,
                candidate,
                "上一轮提出的候选不止一份，需要作者指定",
                module=module_target,
            )
        if module_target:
            return TaskIntent(
                kind="mixed",
                structure=True,
                module=True,
                reason="作者确认上一轮候选，并明确要求保存资料",
                candidate=candidate,
                author_text=raw,
            )
        return TaskIntent(
            kind="structure",
            structure=True,
            module=False,
            reason="作者确认上一轮候选",
            candidate=candidate,
            author_text=raw,
        )

    if structure_target and module_target:
        return TaskIntent(
            kind="mixed",
            structure=True,
            module=True,
            reason="作者同时要求修改构思与保存资料",
            candidate=candidate,
            author_text=raw,
        )
    if structure_target:
        return TaskIntent(
            kind="structure",
            structure=True,
            module=False,
            reason=(
                "作者通过创建构思入口提交要求" if explicit_creation else "作者明确要求修改构思结构"
            ),
            candidate=candidate,
            author_text=raw,
        )
    if module_target:
        if command or _has(raw, _SAVE_MODULE):
            return TaskIntent(
                kind="module",
                structure=False,
                module=True,
                reason="作者明确要求保存资料模块",
                candidate=candidate,
                author_text=raw,
            )
        return discussion_intent(raw, "作者只在讨论资料")
    if command:
        # 构思页上的修改要求默认落在结构上；资料目标会在上面先被识别。
        return TaskIntent(
            kind="structure",
            structure=True,
            module=False,
            reason="构思页的修改要求",
            candidate=candidate,
            author_text=raw,
        )
    return discussion_intent(raw, "没有明确的保存要求")


def _named(title: str, text: str) -> bool:
    """名称至少两个字，按原样出现在原话里才算点到；与授权规则保持一致。"""
    name = _flat(title)
    return len(name) >= 2 and name in text


def named_objects(
    variant: StoryVariant,
    author_text: str,
    *,
    catalog: StoryClueCatalog | None = None,
) -> list[tuple[str, str]]:
    """作者点到的既有对象：``(kind:id, 展示标签)``，用于核对预期目标。"""
    text = _flat(author_text)
    found: list[tuple[str, str]] = []

    def add(kind: str, objects: Mapping[str, object], label: str) -> None:
        for object_id, item in objects.items():
            title = str(getattr(item, "title", "") or "")
            if _named(title, text):
                found.append((f"{kind}:{object_id}", f"{label}「{title}」"))

    add("node", variant.nodes, "节点")
    if catalog is not None:
        for fact in catalog:
            if _named(fact.title, text):
                found.append((f"clue:{fact.clue_id}", f"线索「{fact.title}」"))
    add("thread", variant.threads, "分支")
    for constraint in variant.constraints.values():
        if _named(_constraint_text(constraint), text):
            found.append(
                (f"constraint:{constraint.id}", f"约定「{_constraint_text(constraint)[:20]}」")
            )
    return found


def _constraint_text(constraint: Constraint) -> str:
    return constraint.text.strip()


@dataclass(frozen=True, slots=True)
class GoalAssessment:
    """任务结束时的程序判定：只说明能否确认目标已完成。"""

    complete: bool
    missing: tuple[str, ...] = ()

    def note(self) -> str:
        if self.complete:
            return ""
        return "；".join(self.missing) + "。已提交的内容保留。"


def _touched_keys(receipts: Sequence[Mapping[str, Any]]) -> set[str]:
    touched: set[str] = set()
    for receipt in receipts:
        changed = receipt.get("changed")
        if isinstance(changed, list):
            touched.update(str(item) for item in changed)
        objects = receipt.get("objects")
        if isinstance(objects, list):
            for item in objects:
                if isinstance(item, Mapping):
                    kind = str(item.get("kind") or "")
                    object_id = str(item.get("id") or "")
                    if kind and object_id:
                        touched.add(f"{kind}:{object_id}")
    return touched


def _module_saved(receipts: Sequence[Mapping[str, Any]]) -> bool:
    return any(
        receipt.get("kind") == "module" and bool(receipt.get("saved")) for receipt in receipts
    )


def assess_story_goal(
    intent: TaskIntent,
    *,
    variant: StoryVariant | None,
    receipts: Sequence[Mapping[str, Any]],
    structure_readonly: bool = False,
) -> GoalAssessment:
    """按实际收据核对本轮目标；不把文字回复或模块保存当成结构完成。"""
    missing: list[str] = []
    story_receipts = [
        receipt for receipt in receipts if receipt.get("kind") in ("story", "story_noop")
    ]
    module_receipts = [receipt for receipt in receipts if receipt.get("kind") == "module"]

    if intent.requires_structure and not structure_readonly:
        matching = [
            receipt
            for receipt in story_receipts
            if variant is not None and receipt.get("variant_id") == variant.id
        ]
        if not matching:
            missing.append("构思结构没有提交")
        else:
            touched = _touched_keys(matching)
            expected = list(intent.expected) or (
                named_objects(variant, intent.author_text) if variant else []
            )
            absent = [label for key, label in expected if key not in touched]
            if absent:
                missing.append(f"作者点到的{'、'.join(absent)}没有出现在本次提交中")

    if intent.requires_module and not _module_saved(module_receipts):
        missing.append("资料模块没有保存")

    return GoalAssessment(complete=not missing, missing=tuple(missing))

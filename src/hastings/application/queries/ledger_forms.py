"""账本编辑表单的描述（V03-3.2）：前端按同一份描述渲染，不在页面里硬编码十种表单。

字段白名单的唯一事实源在 ``domain/story/canon/entries.py``；本模块只补展示信息
（中文标签、控件类型、下拉选项、引用目标种类）。测试会核对两边一致，防止
「表单能填、后端拒绝」的口径漂移。
"""

from __future__ import annotations

from typing import Any

from hastings.domain.story import kinds
from hastings.domain.story.canon.entries import LEDGER_KINDS
from hastings.domain.story.canon.models import CHARACTER_ROLES
from hastings.domain.story.mystery.models import KNOWLEDGE_MODES

__all__ = ["LEDGER_FORMS", "ledger_schema"]

_ROLE_LABELS = {
    "protagonist": "主角",
    "antagonist": "对手",
    "accomplice": "协助者",
    "support": "配角",
    "witness": "证人",
}

_KNOWLEDGE_LABELS = {
    "knows": "知道",
    "suspects": "怀疑",
    "misbelieves": "误信",
    "unaware": "不知道",
}

_SELECT_OPTIONS = {
    "role": [{"value": value, "label": _ROLE_LABELS[value]} for value in sorted(CHARACTER_ROLES)],
    "mode": [
        {"value": value, "label": _KNOWLEDGE_LABELS[value]}
        for value in ("knows", "suspects", "misbelieves", "unaware")
        if value in KNOWLEDGE_MODES
    ],
}

_CHARACTER = [kinds.KIND_CHARACTER]
_PLACE = [kinds.KIND_PLACE]
_TRUTH = [kinds.KIND_TRUTH_FACT]
_FACT = [kinds.KIND_TRUTH_FACT, kinds.KIND_MYSTERY_TRUTH]


def _field(key: str, label: str, kind: str = "text", **extra: Any) -> dict[str, Any]:
    return {"key": key, "label": label, "type": kind, **extra}


LEDGER_FORMS: dict[str, dict[str, Any]] = {
    kinds.KIND_CHARACTER: {
        "label": "人物",
        "fields": [
            _field("name", "姓名", required=True),
            _field("identity", "身份"),
            _field("goal", "目标", "textarea"),
            _field("need", "需求", "textarea"),
            _field("secret", "秘密", "textarea"),
            _field("arc", "变化", "textarea"),
            _field("role", "故事作用", "select", options=_SELECT_OPTIONS["role"]),
            _field("traits", "特征（一行一条）", "lines"),
        ],
    },
    kinds.KIND_PLACE: {
        "label": "地点",
        "fields": [
            _field("name", "地点名", required=True),
            _field("description", "描述", "textarea"),
            _field("entry_conditions", "进入条件（一行一条）", "lines"),
            _field("adjacent", "相邻地点", "refs", ref_kinds=_PLACE),
            _field("features", "重要特征（一行一条）", "lines"),
        ],
    },
    kinds.KIND_RELATIONSHIP: {
        "label": "关系",
        "fields": [
            _field("source_id", "一方", "ref", ref_kinds=_CHARACTER, required=True),
            _field("target_id", "另一方", "ref", ref_kinds=_CHARACTER, required=True),
            _field("relation_type", "关系类型"),
            _field("public_state", "公开状态", "textarea"),
            _field("true_state", "真实状态", "textarea"),
            _field("evolution", "变化", "textarea"),
        ],
    },
    kinds.KIND_CLUE: {
        "label": "线索",
        "fields": [
            _field("statement", "线索内容", "textarea", required=True),
            _field("source", "来源"),
            _field("points_to", "指向的真相", "refs", ref_kinds=_FACT),
            _field("necessity", "破案必需", "checkbox"),
        ],
    },
    kinds.KIND_MYSTERY_TRUTH: {
        "label": "案件真相",
        "fields": [
            _field("culprit_id", "凶手", "ref", ref_kinds=_CHARACTER, required=True),
            _field("motive", "动机", "textarea"),
            _field("method", "手法", "textarea"),
            _field("cover_up", "掩饰", "textarea"),
            _field("accomplice_ids", "协助者", "refs", ref_kinds=_CHARACTER),
            _field("true_timeline", "真实时间线", "refs", ref_kinds=[kinds.KIND_TIMELINE_EVENT]),
        ],
    },
    kinds.KIND_TRUTH_FACT: {
        "label": "事实",
        "fields": [
            _field("statement", "实际发生的事", "textarea", required=True),
            _field("event_id", "对应事件", "ref", ref_kinds=[kinds.KIND_TIMELINE_EVENT]),
        ],
    },
    kinds.KIND_KNOWLEDGE: {
        "label": "认知 / 证词",
        "fields": [
            _field("character_id", "谁的认知", "ref", ref_kinds=_CHARACTER, required=True),
            _field("fact_id", "关于哪件事", "ref", ref_kinds=_TRUTH, required=True),
            _field("mode", "认知状态", "select", options=_SELECT_OPTIONS["mode"]),
            _field("note", "证词或备注", "textarea"),
        ],
    },
    kinds.KIND_TIMELINE_EVENT: {
        "label": "时间线事件",
        "fields": [
            _field("time", "时间", required=True),
            _field("place_id", "地点", "ref", ref_kinds=_PLACE),
            _field("participants", "参与人物", "refs", ref_kinds=_CHARACTER),
            _field("before_state", "之前", "textarea"),
            _field("after_state", "之后", "textarea"),
        ],
    },
    kinds.KIND_WORLD_RULE: {
        "label": "世界规则",
        "fields": [
            _field("rule", "规则", "textarea", required=True),
            _field("conditions", "成立条件（一行一条）", "lines"),
            _field("exceptions", "例外（一行一条）", "lines"),
            _field("cost", "代价"),
        ],
    },
    kinds.KIND_STORY_PREMISE: {
        "label": "故事前提",
        "fields": [
            _field("premise", "核心命题", "textarea", required=True),
            _field("genre", "类型"),
            _field("reader_promise", "读者承诺", "textarea"),
            _field("thematic_bounds", "主题边界（一行一条）", "lines"),
        ],
    },
}


def ledger_schema() -> list[dict[str, Any]]:
    """按 ``LEDGER_KINDS`` 顺序返回可编辑种类与字段描述。"""
    return [
        {"kind": kind, "label": LEDGER_FORMS[kind]["label"], "fields": LEDGER_FORMS[kind]["fields"]}
        for kind in LEDGER_KINDS
    ]

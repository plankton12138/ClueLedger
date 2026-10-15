"""主编只选择用例，不决定审批；明确意图直接前进，歧义才提问。"""

from typing import Any


def route_intent(text: str, *, change_set_id: str | None = None) -> dict[str, Any]:
    value = text.strip()
    if change_set_id and any(word in value for word in ("继续", "修", "审阅", "解释")):
        return {"intent_kind": "continue_change", "change_set_id": change_set_id}
    for kind, words in (
        ("open_change", ("改为", "改成", "设定变化", "改变角色")),
        ("audit", ("审计", "检查矛盾", "一致性", "提前知情")),
        ("draft", ("写一章", "写第", "生成正文", "写草稿")),
        ("plan", ("规划", "节拍", "安排场景")),
        ("manual_command", ("保存", "接受", "拒绝", "撤销")),
        ("explain", ("为什么", "解释")),
        ("query", ("查询", "列出", "查看")),
    ):
        if any(word in value for word in words):
            return {"intent_kind": kind, "instruction": value}
    return {
        "kind": "clarification",
        "questions": ["你希望先规划故事、生成正文、修改设定，还是检查现有内容？"],
    }

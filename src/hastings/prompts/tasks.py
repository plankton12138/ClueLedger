"""非会话任务的一次性提示：对话摘要与连接测试。"""

from __future__ import annotations

__all__ = [
    "PROBE_PROMPT",
    "PROBE_SYSTEM",
    "SUMMARY_CLUE_REFERENCES_HEAD",
    "SUMMARY_PREFIX",
    "SUMMARY_SYSTEM",
    "continuation_prompt",
    "probe_tool_prompt",
    "summary_instruction_prompt",
    "summary_merge_prompt",
]

#: 摘要提示词版本由任务代码维护；这里只放文本。
SUMMARY_SYSTEM = """你在为一段写作对话整理可续写的摘要。

只输出一个 JSON 对象，不要解释、不要 Markdown 代码块之外的任何文字：

{
  "summary": "一段话概括这段讨论",
  "items": [
    {"kind": "...", "text": "...", "quote": "...", "sources": ["t1"]}
  ]
}

规则：
- kind 只能是 goal、constraint、author_decision、suggestion、pending、reference；
- 每条要点必须给出 sources，写它来自哪个轮次 ID（用户消息里用方括号标出）；
- 只写对话里确实出现过的内容，不要补写、不要推测；
- 作者确认的决定、助手的建议、尚未确定的事项要分开写；
- 作者最新的修正与被否定的方案都要保留，不能只留结论；
- 不要复述改了哪些文件、保存是否成功，那些由应用自己的记录说明。"""

SUMMARY_PREFIX = "（应用整理的较早对话摘要，供继续任务使用，不是作者的新要求）\n\n"

#: 摘要里单独列出被引用过的线索身份；它由应用记录，不依赖摘要模型是否提到。
SUMMARY_CLUE_REFERENCES_HEAD = (
    "- 曾被引用的线索（身份与版本，不是原文依据；需要时重新读取当前正文核对）："
)

PROBE_PROMPT = "请只回答两个字：你好。"

PROBE_SYSTEM = "你是一个简洁的连接测试助手，只回答被要求的最小内容。"


def probe_tool_prompt(tool_name: str) -> str:
    """工具测试的指令：必须调用指定探针工具，再据结果作答。"""
    return (
        f"调用 {tool_name} 工具，参数 echo 传「ping」，"
        "然后根据工具结果用一句话说明你收到了什么。"
    )


def summary_merge_prompt(carry: str, text: str) -> str:
    """已有一份摘要时，把新覆盖的轮次并进来，而不是重写全部历史。"""
    return (
        f"先前有效摘要：\n{carry}\n\n合并下列新增完整轮次；保留仍有效的约束、来源和原话，"
        "根据作者的新修正替换旧决定。\n\n" + text
    )


def summary_instruction_prompt(instruction: str, merged: str) -> str:
    """作者这次整理补充的要求：进入每一次摘要提示，不改变来源引用。"""
    return (
        "作者对这次整理补充的要求：\n"
        f"{instruction.strip()}\n\n"
        "在不改变来源引用的前提下按这个要求整理。\n\n" + merged
    )


def continuation_prompt(text: str) -> str:
    """继续与插话发给模型的说明；明确标成应用补的，不冒充作者原话。"""
    if text.strip():
        return (
            "（应用转达）作者在生成过程中追加了要求：\n"
            f"{text.strip()}\n\n"
            "请在保留已经写出的内容的前提下按这个要求调整；已经完成的段落不要重复。"
            "还没有写出正文时直接按新要求开始，不要说明进度。"
        )
    return (
        "（应用转达）继续完成上一条要求：已经写出的内容不要重复，也不要重新开始，"
        "只补完还没完成的部分。如果还没有写出正文，就直接开始写，不要说明进度或道歉。"
    )

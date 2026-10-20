"""账本更新建议（V03-4.4）：模型提案的形状、提示词与严格解析。

建议不是账本事实，也不是「未保存的条目」——它是**带来源的候选**：

- 每项必须带来源原句（``quote``）与生成时的章基础版本；没有来源的提案直接拒绝，
  因为无法核对、也无法在账本里解释「这条从哪来」。
- 证词与猜测保留原类型（``story.knowledge`` + ``mode``），不因为被采用就升级为
  客观事实；模型自报的 ``hypothesis`` 只作为提示，采用时仍由候选规则拦截。
- 输出格式严格：解析失败整批拒绝，不做「尽力猜」的容错——把半份错误的设定写入
  候选，比让作者重试一次代价大得多。

本模块只做纯函数：拼提示词、解析文本、给出上限；调用模型与暂存在应用服务里。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, NoReturn

from hastings.domain.story import error_codes as ec
from hastings.domain.story.canon.entries import LEDGER_FIELDS
from hastings.domain.story.errors import violation

__all__ = [
    "MAX_SUGGESTIONS",
    "SUGGESTION_SYSTEM_PROMPT",
    "Proposal",
    "parse_proposals",
    "render_suggestion_prompt",
]

#: 一次整理的上限：建议太多等于让作者重读一遍正文，也失去「简短结论」的意义。
MAX_SUGGESTIONS = 12

SUGGESTION_SYSTEM_PROMPT = (
    "你是作者的设定整理助手：从正文里找出值得记进故事账本的变化，"
    "只依据给定正文与现有账本，不补充、不推测。区分「发生了什么」与"
    "「谁声称/猜测什么」；不确定的内容标成假设。"
)


@dataclass(frozen=True, slots=True)
class Proposal:
    """一条待确认的账本变更提案。"""

    kind: str
    fields: dict[str, Any]
    quote: str
    hypothesis: bool = False


def render_suggestion_prompt(
    *,
    chapter_title: str,
    text: str,
    ledger_lines: list[str],
    guidance: str = "",
) -> str:
    """确定性提示词：正文 + 现有账本目录 + 输出格式。"""
    allowed = "、".join(sorted(LEDGER_FIELDS))
    parts = [
        f"## 章节\n{chapter_title or '（未命名）'}",
        f"## 正文\n{text.strip()}",
        "## 现有账本\n" + ("\n".join(ledger_lines) if ledger_lines else "（空）"),
    ]
    if guidance.strip():
        parts.append(f"## 作者附加要求\n{guidance.strip()}")
    parts.append(
        "## 输出要求\n"
        "只输出 JSON，不要解释。格式：\n"
        '{"suggestions":[{"kind":"story.character","fields":{"name":"姐姐"},'
        '"quote":"正文里支持这条的原句","hypothesis":false}]}\n'
        f"- kind 只能是：{allowed}；\n"
        "- fields 只写该种类确实来自正文的字段，引用别人物时用已有条目的 id；\n"
        "- quote 必须是正文原文片段，不能改写；\n"
        "- 证词、猜测写 story.knowledge 并保留 mode（knows/suspects/misbelieves/unaware）；\n"
        '- 没有值得记录的变化时输出 {"suggestions":[]}；\n'
        f"- 最多 {MAX_SUGGESTIONS} 条，按重要性排序。"
    )
    return "\n\n".join(parts)


def _strip_fence(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _invalid(why: str, fix: str, **details: Any) -> NoReturn:
    raise violation(ec.STORY_INVALID_COMMAND, "suggestions.parse", why, fix, **details)


def parse_proposals(raw: str) -> list[Proposal]:
    """严格解析模型输出；结构不对整批拒绝，不返回半份结果。"""
    text = _strip_fence(raw)
    if not text:
        _invalid("模型没有返回内容", "重试一次；持续为空时换模型或缩小章节范围")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        _invalid(
            f"输出不是合法 JSON：{exc.msg}",
            '只输出 {"suggestions":[...]} 形式的 JSON，不要加解释文字',
        )
    if isinstance(payload, dict):
        rows = payload.get("suggestions")
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = None
    if not isinstance(rows, list):
        _invalid(
            "输出缺少 suggestions 数组",
            '使用 {"suggestions":[...]}；没有建议时给空数组',
            got=type(payload).__name__,
        )
    if len(rows) > MAX_SUGGESTIONS:
        _invalid(
            f"一次给出 {len(rows)} 条建议，超过上限 {MAX_SUGGESTIONS}",
            "只保留与本章最相关的几条，其余在后续章节再整理",
            count=len(rows),
        )
    proposals: list[Proposal] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            _invalid(f"第 {index + 1} 条建议不是对象", "每条建议写成 {kind, fields, quote} 对象")
        kind = row.get("kind")
        if kind not in LEDGER_FIELDS:
            _invalid(
                f"第 {index + 1} 条建议的 kind {kind!r} 不是可写入账本的种类",
                f"改用以下之一：{sorted(LEDGER_FIELDS)}",
                index=index,
                kind=kind,
            )
        fields = row.get("fields")
        if not isinstance(fields, dict) or not fields:
            _invalid(
                f"第 {index + 1} 条建议缺少 fields",
                "给出该种类的字段；不确定的字段留空而不是编造",
                index=index,
            )
        quote = row.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            _invalid(
                f"第 {index + 1} 条建议缺少来源原句",
                "补上正文里支持这条的原句；没有原句的提案不入候选",
                index=index,
            )
        proposals.append(
            Proposal(
                kind=str(kind),
                fields=dict(fields),
                quote=quote.strip(),
                hypothesis=bool(row.get("hypothesis", False)),
            )
        )
    return proposals

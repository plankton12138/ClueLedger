"""正文文本解析（V03-2.3 / 计划 M06）：字节 → 章节草稿。

解析只回答「这段文本会被切成哪几章、每章实际文本与指纹是什么」，不做任何
存储；预览与确认看到的是同一份结果，确认后的正文必须等于预览（M06 验收）。

格式与分章规则（实施基线，不是产品承诺）：

- 只接受 ``.txt`` / ``.md``；UTF-8 与带 BOM 的 UTF-8，其他编码显式报错，
  不静默按替换字符导入；
- Markdown：优先用 ``##`` 标题分章（有 ``##`` 时 ``#`` 视为书名/卷名）；
  没有 ``##`` 才用 ``#``；
- 纯文本：连续出现两行以上「第X章 / 第X节 / 第X回 / Chapter N」时按行分章；
- 都不成立时整篇作为一章，标题取文件名（不含扩展名）；
- 首个标题之前的正文若达到 40 个字符（不含标题行），作为「前言」一章，
  否则视为书名/卷名等元信息丢弃。

指纹按规范化文本与章节边界计算，**文件名不参与**——去重看内容不看名字
（计划 M06 第 6 条）。
"""

from __future__ import annotations

import codecs
import re
from dataclasses import dataclass
from pathlib import PurePath

from hastings.domain.manuscript.diff import split_paragraphs
from hastings.domain.repository.serialization import content_hash

__all__ = [
    "SUPPORTED_SUFFIXES",
    "ParsedChapter",
    "ParsedManuscript",
    "decode_manuscript_bytes",
    "parse_manuscript_text",
]

SUPPORTED_SUFFIXES = (".txt", ".md", ".markdown")

_MD_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_CHAPTER_LINE = re.compile(
    r"^\s*(第\s*[0-9零〇一二三四五六七八九十百千两]+\s*[章节回卷]|chapter\s+\d+)\b.*$",
    re.IGNORECASE,
)

#: 首个标题前的正文达到这个长度才算「前言」，否则视为书名/卷名等元信息。
_PREFACE_MIN_CHARS = 40


@dataclass(frozen=True, slots=True)
class ParsedChapter:
    """解析出的单章：标题 + 规范化正文 + 内容指纹。"""

    title: str
    text: str
    order_key: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class ParsedManuscript:
    """解析结果：章节、批次指纹与识别到的格式。"""

    chapters: tuple[ParsedChapter, ...]
    batch_hash: str
    format: str


def decode_manuscript_bytes(data: bytes) -> str:
    """按 UTF-8 / UTF-8 BOM 解码；其他编码显式失败，不静默乱码。"""
    from hastings.application.errors import ViolationError

    if data.startswith(codecs.BOM_UTF8):
        return data.decode("utf-8-sig")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ViolationError(
            code="IMPORT_ENCODING_UNSUPPORTED",
            where="imports.decode",
            why=f"文件不是 UTF-8（第 {exc.start} 字节起无法解码）",
            fix="先用编辑器把稿件另存为 UTF-8 再导入；本版不做自动转码",
        ) from exc


def parse_manuscript_text(text: str, *, filename: str) -> ParsedManuscript:
    """把一段文本切成章节草稿（纯函数，不做上限与空内容校验）。"""
    normalized = _normalize(text)
    suffix = PurePath(filename).suffix.lower()
    stem = PurePath(filename).stem.strip() or "导入正文"
    fmt = "markdown" if suffix in (".md", ".markdown") else "text"

    blocks = _split_by_markdown_headings(normalized)
    if blocks is None:
        blocks = _split_by_chapter_lines(normalized)
    if blocks is None:
        body = normalized.strip()
        single = [_chapter(stem, body, 1)] if body else []
        return _result(single, fmt)

    chapters: list[ParsedChapter] = []
    preface, sections = blocks
    if preface:
        chapters.append(_chapter("前言", preface, 1))
    for title, body in sections:
        chapters.append(_chapter(title, body, len(chapters) + 1))
    return _result(chapters, fmt)


# -- 内部 -------------------------------------------------------------------


def _result(chapters: list[ParsedChapter], fmt: str) -> ParsedManuscript:
    return ParsedManuscript(
        chapters=tuple(chapters),
        batch_hash=content_hash({"chapters": [c.content_hash for c in chapters]}),
        format=fmt,
    )


def _chapter(title: str, text: str, index: int) -> ParsedChapter:
    body = text.strip()
    return ParsedChapter(
        title=title.strip() or f"第 {index} 章",
        text=body,
        order_key=f"{index:04d}",
        # 按段落序列取指纹：与库里「章的活动段落文本」同一口径，空行数量
        # 不同不影响判等（去重看内容，不看排版空白）。
        content_hash=content_hash({"paragraphs": split_paragraphs(body)}),
    )


def _normalize(text: str) -> str:
    """统一换行、去掉行尾空格，保留段内换行与段落空行。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip(" \t") for line in lines)


def _split_by_markdown_headings(
    text: str,
) -> tuple[str, list[tuple[str, str]]] | None:
    lines = text.split("\n")
    headings: list[tuple[int, int, str]] = []  # (line index, level, title)
    for index, line in enumerate(lines):
        match = _MD_HEADING.match(line)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))
    if not headings:
        return None
    # 有二级标题时以 ## 分章，# 视为书名/卷名；否则用最小层级。
    levels = {level for _, level, _ in headings}
    level = 2 if 2 in levels else min(levels)
    chapter_headings = [(i, t) for i, lv, t in headings if lv == level]
    preface = _meaningful_preface("\n".join(lines[: chapter_headings[0][0]]))
    sections: list[tuple[str, str]] = []
    for position, (line_index, title) in enumerate(chapter_headings):
        end = (
            chapter_headings[position + 1][0]
            if position + 1 < len(chapter_headings)
            else len(lines)
        )
        sections.append((title, "\n".join(lines[line_index + 1 : end])))
    return preface, sections


def _split_by_chapter_lines(text: str) -> tuple[str, list[tuple[str, str]]] | None:
    lines = text.split("\n")
    marks: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if _CHAPTER_LINE.match(line):
            marks.append((index, line.strip()))
    if len(marks) < 2:
        return None
    preface = _meaningful_preface("\n".join(lines[: marks[0][0]]))
    sections: list[tuple[str, str]] = []
    for position, (line_index, title) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(lines)
        sections.append((title, "\n".join(lines[line_index + 1 : end])))
    return preface, sections


def _meaningful_preface(text: str) -> str:
    """首个标题前的正文：只有达到门槛的非标题内容才作为「前言」。"""
    kept = [line for line in text.split("\n") if line.strip() and not _MD_HEADING.match(line)]
    body = "\n".join(kept).strip()
    return body if len(body) >= _PREFACE_MIN_CHARS else ""

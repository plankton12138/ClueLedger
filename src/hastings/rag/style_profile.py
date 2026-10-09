"""文风档案纯函数（P7 T6，计划 D6）：零 LLM、零向量库的确定性统计。

口径（计划 D6 逐条钉住）：
- 分句：按 ``。！？；…`` 切，连续标点合并为一个终止符；句长按中文字符数；
- 六桶：1-10 / 11-20 / 21-30 / 31-40 / 41-50 / 51+；
- 对话占比：``「」『』""`` 引号内字符数 / 总非空白字符数；
- 段落：空行分段；总字数去空白口径（与 bible/stats._count_non_whitespace 同款）。

泄压阀（D6）：单文件读码失败（UnicodeDecodeError）是「合法但部分不可读」
——跳过该文件并把三要素告警放进 warnings，不 500 整体；OSError（盘故障/
权限）是环境故障，交 REST 层归因 500。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final

# 分句终止符（连续出现合并为一个切点）。
_TERMINATORS = frozenset("。！？；…")

# 对话引号对（开，闭）：直角引号 / 双直角 / 弯引号。
_QUOTE_PAIRS = (("「", "」"), ("『", "』"), ("\u201c", "\u201d"))

# 句长六桶上沿（末桶 51+ 无上沿）。
_BUCKET_EDGES: Final[tuple[tuple[int, str], ...]] = (
    (10, "1-10"),
    (20, "11-20"),
    (30, "21-30"),
    (40, "31-40"),
    (50, "41-50"),
)
_BUCKET_LAST = "51+"


@dataclass(frozen=True, slots=True)
class StyleProfile:
    """全语料聚合档案（style-profile 端点响应的数据底座）。"""

    total_chars: int
    sentence_count: int
    avg_sentence_length: float
    paragraph_count: int
    avg_paragraph_length: float
    dialogue_ratio: float
    sentence_buckets: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def count_non_whitespace(text: str) -> int:
    """总字数（去空白口径，与 stats 同款）。"""
    return sum(1 for ch in text if not ch.isspace())


def split_sentences(text: str) -> list[str]:
    """按终止符分句（连续标点合并）；去掉终止符本身，空白句丢弃。"""
    sentences: list[str] = []
    buf: list[str] = []
    for ch in text:
        if ch in _TERMINATORS:
            sentence = "".join(buf).strip()
            if sentence:
                sentences.append(sentence)
            buf.clear()
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        sentences.append(tail)  # 末句无终止符也算句（作者没写完的尾巴是真实句长）
    return sentences


def sentence_length_bucket(length: int) -> str:
    """句长 → 桶标签；非正数句长不会出现（split_sentences 不产空句）。"""
    for edge, label in _BUCKET_EDGES:
        if length <= edge:
            return label
    return _BUCKET_LAST


def dialogue_ratio(text: str) -> float:
    """对话占比 = 引号内字符数 / 总非空白字符数；分母 0 → 0.0（空文本合法）。"""
    total = count_non_whitespace(text)
    if total == 0:
        return 0.0
    quoted = 0
    for opening, closing in _QUOTE_PAIRS:
        depth = 0
        for ch in text:
            if ch == opening:
                depth += 1
            elif ch == closing and depth > 0:
                depth -= 1
            elif depth > 0 and not ch.isspace():
                quoted += 1
    return quoted / total


def split_paragraphs(text: str) -> list[str]:
    """空行分段（连续空白行视作一个分隔）；纯空白段丢弃。"""
    paragraphs: list[str] = []
    buf: list[str] = []
    for line in text.splitlines():
        if line.strip() == "":
            paragraph = "\n".join(buf).strip()
            if paragraph:
                paragraphs.append(paragraph)
            buf.clear()
        else:
            buf.append(line)
    tail = "\n".join(buf).strip()
    if tail:
        paragraphs.append(tail)
    return paragraphs


def build_style_profile(texts: dict[str, str]) -> StyleProfile:
    """聚合全语料档案。

    ``texts`` = {文件名: 已解码文本}；解码失败的文件由调用方跳过并经
    ``warnings`` 带入三要素告警（本函数只管算术，不做 IO）。
    """
    full = "\n".join(texts.values())
    sentences = split_sentences(full)
    lengths = [count_non_whitespace(s) for s in sentences]
    buckets = {label: 0 for _, label in _BUCKET_EDGES}
    buckets[_BUCKET_LAST] = 0
    for length in lengths:
        buckets[sentence_length_bucket(length)] += 1
    paragraphs = split_paragraphs(full)
    total = count_non_whitespace(full)
    return StyleProfile(
        total_chars=total,
        sentence_count=len(sentences),
        avg_sentence_length=(sum(lengths) / len(lengths)) if lengths else 0.0,
        paragraph_count=len(paragraphs),
        avg_paragraph_length=(total / len(paragraphs)) if paragraphs else 0.0,
        dialogue_ratio=dialogue_ratio(full),
        sentence_buckets=buckets,
    )

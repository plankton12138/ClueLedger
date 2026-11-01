"""正文选段的统一解析：界面、模型工具与候选提交共用一套坐标与失败分类。

这里只回答「作者或模型指的到底是哪一段原文」，不做语义判断，也不写文件。三条规则：

* 坐标是规范化 Markdown 源文本的 Unicode 码点半开区间 ``[start, end)``，与前端
  ``SourceHighlight`` 的转换一致；渲染后的可见文字不能反过来代替源坐标。
* 提供了源区间就严格比对，宁可在区间不匹配时失败，也不在别处「找到一个差不多」的位置。
* 只给引文时要求全文唯一；重叠出现也算多次，否则「哈哈」会从「哈哈哈」里挑一个。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, NoReturn

from pydantic import BaseModel, ConfigDict, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import (
    BindProse,
    ProseBinding,
    StoryChangeSet,
    StoryOperation,
    StoryVariant,
)

__all__ = [
    "Anchor",
    "AnchorState",
    "BindProseInput",
    "Span",
    "anchor_state",
    "build_binding",
    "locate_saved_span",
    "normalize_bindings",
    "resolve_selection",
    "text_digest",
    "unique_span",
]

AnchorState = Literal["exact", "relocated", "missing", "ambiguous", "unavailable"]


@dataclass(frozen=True, slots=True)
class Span:
    start: int
    end: int
    quote: str


@dataclass(frozen=True, slots=True)
class Anchor:
    """当前正文里这条引文的定位结果；``span`` 为 ``None`` 表示不能高亮。"""

    state: AnchorState
    span: Span | None = None


class BindProseInput(BaseModel):
    """写入正文关联的公开字段；``id``、``document_id``、摘要由服务端补齐。"""

    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    clue_id: str | None = None
    path: str = Field(min_length=1)
    quote: str = Field(min_length=1)
    start: int | None = Field(default=None, ge=0)
    end: int | None = Field(default=None, gt=0)
    observation: str = Field(default="", max_length=2000)


def text_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _anchor_error(code: str, why: str, fix: str) -> NoReturn:
    raise WorkspaceError(code=code, where="prose.anchor", why=why, fix=fix)


def unique_span(text: str, quote: str) -> Span:
    """引文在正文里唯一出现时返回其区间；否则按缺失或歧义失败。"""
    if not quote:
        _anchor_error("PROSE_ANCHOR_MISMATCH", "引文为空", "重新选择正文片段")
    first = text.find(quote)
    if first < 0:
        _anchor_error("PROSE_ANCHOR_MISSING", "当前正文没有对应原句", "刷新后重新选择片段")
    # 从 first + 1 继续查找：重叠重复也要算作多次，不能用非重叠计数判断唯一。
    if text.find(quote, first + 1) >= 0:
        _anchor_error(
            "PROSE_ANCHOR_AMBIGUOUS",
            "引文在当前正文里出现多次",
            "指定准确源区间，或把引文扩大到唯一片段",
        )
    return Span(first, first + len(quote), quote)


def resolve_selection(
    *,
    text: str,
    actual_digest: str,
    expected_digest: str,
    quote: str,
    start: int | None,
    end: int | None,
) -> Span:
    """写入前解析选段：先校验章节版本，再校验区间与引文。"""
    if actual_digest != expected_digest:
        _anchor_error(
            "PROSE_SOURCE_STALE",
            "正文在选段后已经修改",
            "刷新章节并重新选择片段",
        )
    if start is None and end is None:
        return unique_span(text, quote)
    if start is None or end is None:
        _anchor_error("PROSE_ANCHOR_MISMATCH", "源区间不完整", "重新选择连续原文")
    if not 0 <= start < end <= len(text) or text[start:end] != quote:
        _anchor_error("PROSE_ANCHOR_MISMATCH", "源区间与引文不一致", "重新选择连续原文")
    return Span(start, end, quote)


def locate_saved_span(*, text: str, current_digest: str, saved_digest: str, saved: Span) -> Span:
    """读取已保存关联的当前位置：摘要未变就验证旧区间，变了只接受唯一引文重定位。"""
    if current_digest == saved_digest:
        return resolve_selection(
            text=text,
            actual_digest=current_digest,
            expected_digest=saved_digest,
            quote=saved.quote,
            start=saved.start,
            end=saved.end,
        )
    return unique_span(text, saved.quote)


def anchor_state(binding: ProseBinding, text: str, current_digest: str) -> Anchor:
    """把定位结果分成读取用的五种状态；失败状态保留原引用，不返回猜测位置。"""
    saved = Span(binding.start, binding.end, binding.quote)
    try:
        span = locate_saved_span(
            text=text, current_digest=current_digest, saved_digest=binding.digest, saved=saved
        )
    except WorkspaceError as exc:
        if exc.code == "PROSE_ANCHOR_MISSING":
            return Anchor(state="missing")
        if exc.code == "PROSE_ANCHOR_AMBIGUOUS":
            return Anchor(state="ambiguous")
        return Anchor(state="unavailable")
    return Anchor(state="exact" if span == saved else "relocated", span=span)


def build_binding(
    binding_id: str,
    request: BindProseInput,
    *,
    text: str,
    chapter_digest: str,
    document_id: str,
    observed_digest: str | None = None,
    contract_digest: str | None = None,
    source: Literal["candidate", "outline_from_prose", "manual", "inferred"] = "manual",
) -> ProseBinding:
    """把一次选段解析成完整关联；摘要与区间都由服务端按当前正文计算。

    ``observed_digest`` 是调用方看到的章节版本；它与 ``chapter_digest`` 不同表示正文在
    选段之后被改过，此时不写入。
    """
    if request.node_id is None and request.clue_id is None:
        raise WorkspaceError(
            code="PROSE_BINDING_TARGET_REQUIRED",
            where="prose.anchor",
            why="这次正文关联既没有构思节点也没有线索",
            fix="先选择一条线索或一个构思节点",
        )
    span = resolve_selection(
        text=text,
        actual_digest=chapter_digest,
        expected_digest=observed_digest or chapter_digest,
        quote=request.quote,
        start=request.start,
        end=request.end,
    )
    return ProseBinding(
        id=binding_id,
        node_id=request.node_id,
        clue_id=request.clue_id,
        document_id=document_id,
        path=request.path,
        digest=chapter_digest,
        span_digest=text_digest(span.quote),
        source=source,
        start=span.start,
        end=span.end,
        quote=span.quote,
        observation=request.observation,
        contract_digest=contract_digest,
    )


def _document_id(path: str) -> str:
    return "doc_" + hashlib.sha256(path.encode()).hexdigest()[:24]


def normalize_bindings(
    workspace: Workspace,
    variant: StoryVariant | None,
    change: StoryChangeSet,
) -> StoryChangeSet:
    """把界面或模型给的轻量关联换成服务端校验过的完整关联。

    区间、引文、章节摘要与目标都由服务端按当前正文重算；调用方提供的摘要只用于发现
    「选段之后正文又改过」。路径必须是受管章节，不能写任意本机路径。
    """
    if not any(isinstance(operation, BindProse) for operation in change.operations):
        return change
    chapters = {
        entry.path
        for entry in workspace.catalog().chapters
        if entry.exists and entry.chapter_number is not None
    }
    files: dict[str, tuple[str, str]] = {}

    def file_of(path: str) -> tuple[str, str]:
        if path not in files:
            view = workspace.read(path)
            files[path] = (view.text, view.digest)
        return files[path]

    replacements: list[ProseBinding] = []
    for operation in change.operations:
        if not isinstance(operation, BindProse):
            continue
        binding = operation.binding
        if binding.path not in chapters:
            raise WorkspaceError(
                code="PROSE_CHAPTER_NOT_FOUND",
                where="prose.anchor",
                why=f"所选路径不是当前正文章节：{binding.path}",
                fix="刷新目录后重新选择章节",
                details={"path": binding.path},
            )
        text_value, digest = file_of(binding.path)
        span = resolve_selection(
            text=text_value,
            actual_digest=digest,
            expected_digest=binding.digest,
            quote=binding.quote,
            start=binding.start,
            end=binding.end,
        )
        contract: str | None = None
        if binding.node_id is not None and variant is not None:
            from hastings.workspace.story_sync import contract_digest

            node = variant.nodes.get(binding.node_id)
            if node is not None:
                contract = contract_digest(variant, node)
        replacements.append(
            binding.model_copy(
                update={
                    "document_id": _document_id(binding.path),
                    "digest": digest,
                    "span_digest": text_digest(span.quote),
                    "start": span.start,
                    "end": span.end,
                    "quote": span.quote,
                    "contract_digest": contract,
                }
            )
        )
    index = 0
    operations: list[StoryOperation] = []
    for operation in change.operations:
        if isinstance(operation, BindProse):
            operations.append(BindProse(op="bind_prose", binding=replacements[index]))
            index += 1
        else:
            operations.append(operation)
    return change.model_copy(update={"operations": operations})

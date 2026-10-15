"""Edit Operation：正文编辑的领域输入、规范化与稳定 hunk 身份（T14.1）。

作者与 Agent 都通过「操作」而非「整章覆盖」表达修改：操作可被逐条校验、
逐条接受或拒绝（T14.4 Patch），并留下稳定的 hunk 身份。`normalize_operation`
把一次操作规范成 `Hunk`，其 `hunk_id` 只由「操作种类 + 目标 + 内容」决定，
与执行时新生成的段落 ID 无关——因此同一编辑在重试/多人协作中身份不变。

段落粒度为第一版（Phase 14 计划 §1）：split/merge 负责段内/段间边界变化。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

__all__ = [
    "OP_DELETE",
    "OP_INSERT",
    "OP_MERGE",
    "OP_MOVE",
    "OP_REPLACE",
    "OP_SPLIT",
    "Delete",
    "EditOperation",
    "Hunk",
    "Insert",
    "Merge",
    "Move",
    "Replace",
    "Split",
    "modified_unit_ids",
    "normalize_operation",
    "operation_from_content",
    "operation_to_content",
]

OP_INSERT = "insert"
OP_REPLACE = "replace"
OP_DELETE = "delete"
OP_SPLIT = "split"
OP_MERGE = "merge"
OP_MOVE = "move"

_HUNK_PREFIX = "hunk_"
_HEX_LEN = 24


@dataclass(frozen=True, slots=True)
class Insert:
    """在锚点之后插入一个或多个段落；after_unit_id 为 None 表示章首。"""

    after_unit_id: str | None
    text: str


@dataclass(frozen=True, slots=True)
class Replace:
    """替换某段落的文本（单一段落；跨段用 delete+insert）。"""

    unit_id: str
    text: str


@dataclass(frozen=True, slots=True)
class Delete:
    """删除某段落（保留 Artifact，正文视图不再显示）。"""

    unit_id: str


@dataclass(frozen=True, slots=True)
class Split:
    """在段内字符偏移处拆分段落；offset 必须落在 (0, len(text)) 内。"""

    unit_id: str
    offset: int


@dataclass(frozen=True, slots=True)
class Merge:
    """合并相邻两段落；first 保留身份并获谱系，second 进入 deleted。"""

    first_unit_id: str
    second_unit_id: str


@dataclass(frozen=True, slots=True)
class Move:
    """调整段落顺序；after_unit_id 为 None 表示移到章首。"""

    unit_id: str
    after_unit_id: str | None


EditOperation = Insert | Replace | Delete | Split | Merge | Move


@dataclass(frozen=True, slots=True)
class Hunk:
    """规范化后的编辑块：稳定身份 + 种类 + 目标 + 内容。"""

    hunk_id: str
    kind: str
    unit_ids: tuple[str, ...] = ()
    anchor_unit_id: str | None = None
    text: str = ""
    offset: int | None = None


def modified_unit_ids(op: EditOperation) -> tuple[str, ...]:
    """该操作会改动/消费的既有段落 ID；插入是新建，不在此列（不参与重叠判定）。"""
    if isinstance(op, Replace | Delete | Split | Move):
        return (op.unit_id,)
    if isinstance(op, Merge):
        return (op.first_unit_id, op.second_unit_id)
    return ()


def normalize_operation(op: EditOperation) -> Hunk:
    """把操作规范成稳定 Hunk；hunk_id 与执行期新生成的 ID 无关。"""
    if isinstance(op, Insert):
        return Hunk(
            hunk_id=_identity(OP_INSERT, (), op.text, None, op.after_unit_id),
            kind=OP_INSERT,
            unit_ids=(),
            anchor_unit_id=op.after_unit_id,
            text=op.text,
        )
    if isinstance(op, Replace):
        return Hunk(
            hunk_id=_identity(OP_REPLACE, (op.unit_id,), op.text, None, None),
            kind=OP_REPLACE,
            unit_ids=(op.unit_id,),
            text=op.text,
        )
    if isinstance(op, Delete):
        return Hunk(
            hunk_id=_identity(OP_DELETE, (op.unit_id,), "", None, None),
            kind=OP_DELETE,
            unit_ids=(op.unit_id,),
        )
    if isinstance(op, Split):
        return Hunk(
            hunk_id=_identity(OP_SPLIT, (op.unit_id,), "", op.offset, None),
            kind=OP_SPLIT,
            unit_ids=(op.unit_id,),
            offset=op.offset,
        )
    if isinstance(op, Merge):
        return Hunk(
            hunk_id=_identity(OP_MERGE, (op.first_unit_id, op.second_unit_id), "", None, None),
            kind=OP_MERGE,
            unit_ids=(op.first_unit_id, op.second_unit_id),
        )
    return Hunk(
        hunk_id=_identity(OP_MOVE, (op.unit_id,), "", None, op.after_unit_id),
        kind=OP_MOVE,
        unit_ids=(op.unit_id,),
        anchor_unit_id=op.after_unit_id,
    )


def _identity(
    kind: str,
    unit_ids: tuple[str, ...],
    text: str,
    offset: int | None,
    anchor: str | None,
) -> str:
    payload = json.dumps(
        {"kind": kind, "units": list(unit_ids), "text": text, "offset": offset, "anchor": anchor},
        sort_keys=True,
        ensure_ascii=False,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return f"{_HUNK_PREFIX}{digest[:_HEX_LEN]}"


# ---------------------------------------------------------------------------
# 序列化（T14.4）：操作随 Patch 提案存入 Artifact content，必须 JSON 兼容
# ---------------------------------------------------------------------------
def operation_to_content(op: EditOperation) -> dict[str, Any]:
    """把操作序列化为可直接落盘的 JSON 兼容字典（kind + 字段）。"""
    if isinstance(op, Insert):
        return {"kind": OP_INSERT, "after_unit_id": op.after_unit_id, "text": op.text}
    if isinstance(op, Replace):
        return {"kind": OP_REPLACE, "unit_id": op.unit_id, "text": op.text}
    if isinstance(op, Delete):
        return {"kind": OP_DELETE, "unit_id": op.unit_id}
    if isinstance(op, Split):
        return {"kind": OP_SPLIT, "unit_id": op.unit_id, "offset": op.offset}
    if isinstance(op, Merge):
        return {
            "kind": OP_MERGE,
            "first_unit_id": op.first_unit_id,
            "second_unit_id": op.second_unit_id,
        }
    return {"kind": OP_MOVE, "unit_id": op.unit_id, "after_unit_id": op.after_unit_id}


def operation_from_content(content: dict[str, Any]) -> EditOperation:
    """从落盘内容还原操作；未知 kind 是服务端内容损坏（非用户输入）→ ValueError。"""
    kind = content.get("kind")
    if kind == OP_INSERT:
        return Insert(after_unit_id=content.get("after_unit_id"), text=str(content.get("text", "")))
    if kind == OP_REPLACE:
        return Replace(unit_id=str(content["unit_id"]), text=str(content.get("text", "")))
    if kind == OP_DELETE:
        return Delete(unit_id=str(content["unit_id"]))
    if kind == OP_SPLIT:
        return Split(unit_id=str(content["unit_id"]), offset=int(content["offset"]))
    if kind == OP_MERGE:
        return Merge(
            first_unit_id=str(content["first_unit_id"]),
            second_unit_id=str(content["second_unit_id"]),
        )
    if kind == OP_MOVE:
        return Move(unit_id=str(content["unit_id"]), after_unit_id=content.get("after_unit_id"))
    raise ValueError(f"未知 EditOperation kind：{kind!r}（内容应为服务端生成的合法操作）")

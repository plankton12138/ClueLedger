"""结构文档的读取与写入门禁（v1.1 M1/M2）。

``STRUCTURE.md`` 是模型自维护、作者只读的全书工作记忆：自由格式 Markdown，
直接写入策略（无暂存确认门），走 ``files.py`` 的指纹写入路径，随作品 Git 逐次
入档、事后可回退。本模块提供工具层用的确定性检查与读写入口：

* 预算超限在**写入时**拒绝（``STRUCTURE_BUDGET_CONFLICT``）——压缩版就是一次
  预算内的写入，等效于契约的「入档事务要求先提交压缩版」，且磁盘上不留超限文件；
* 首次起草必须以铁律段开头（``STRUCTURE_MARKER_REQUIRED``）；更新允许标记丢失
  （外部编辑如实落盘，装配层降级全文注入并提示），不猜、不静默修复。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import STRUCTURE_PATH, FileView, Workspace
from hastings.workspace.structure_document import (
    STRUCTURE_FILENAME,
    check_budget,
    split_iron_rules,
)

__all__ = ["StructureState", "read_structure_state", "write_structure_document"]

assert STRUCTURE_FILENAME == STRUCTURE_PATH, "结构文档路径常量必须一致"


@dataclass(frozen=True, slots=True)
class StructureState:
    """结构文档当前状态；起步前缺失视为空。"""

    exists: bool
    text: str
    digest: str | None


def read_structure_state(workspace: Workspace) -> StructureState:
    """读结构文档；缺失视为空（digest 为 ``None``，写入按创建处理）。"""
    try:
        view = workspace.read(STRUCTURE_PATH)
    except WorkspaceError:
        return StructureState(exists=False, text="", digest=None)
    return StructureState(exists=True, text=view.text, digest=view.digest)


def write_structure_document(
    workspace: Workspace,
    *,
    text: str,
    expected_digest: str | None,
    creating: bool,
) -> FileView:
    """带门禁的结构写入：预算超限拒绝；首次起草要求铁律段开头。

    ``expected_digest=None`` 且文件已存在、或非 ``None`` 且文件不存在，都由
    ``workspace.write`` 的指纹核对拒绝（与正文同一冲突语义）。
    """
    normalized = text.strip()
    if not normalized:
        raise WorkspaceError(
            code="STRUCTURE_EMPTY",
            where=f"tools.write_structure:{STRUCTURE_PATH}",
            why="结构文档不能为空",
            fix="补齐铁律、路线、骨架与模块索引后再写入",
        )
    report = check_budget(normalized)
    if not report.ok:
        raise WorkspaceError(
            code="STRUCTURE_BUDGET_CONFLICT",
            where=f"tools.write_structure:{STRUCTURE_PATH}",
            why=f"结构全文 {report.length} 字符超出冻结预算 {report.limit}",
            fix="先提交压缩版结构（保留铁律与骨架，细节下沉模块），压缩随本次写入入档",
            details={"length": report.length, "limit": report.limit},
        )
    if creating and not split_iron_rules(normalized).ok:
        raise WorkspaceError(
            code="STRUCTURE_MARKER_REQUIRED",
            where=f"tools.write_structure:{STRUCTURE_PATH}",
            why="第一版结构必须以「## 铁律」段开头（文件首个非空行）",
            fix="把铁律段放在文件开头，再依次写路线、骨架与模块索引",
        )
    return workspace.write(STRUCTURE_PATH, normalized, expected_digest=expected_digest)

"""人工保存用例（Phase 14 T14.2）：保存先落 Commit，分析由 Outbox 消费者独立调度。

顺序（计划 §T14.2）：读取 base revision → 解析/验证编辑 → no-op 检查 → Commit
（业务事件与 Commit 同事务写入 Outbox，ADR-007）→ **返回**。本用例不等待任何后台
任务：正文提交是唯一事实，分析只是它的事件消费者（四透镜③泄压阀）。

把「调度分析任务」从保存里拿掉，是为了修掉评审 #3 的两个缺陷：

- **保存不被后台故障拖住**：旧实现 ``save()`` 继续 ``await jobs.create()``，任务库
  挂起时用户拿不到保存成功响应；现在保存路径只做提交，任务创建在消费者里发生。
- **补偿与去重不再只存于返回值/进程内存**：旧实现的失败补偿只活在一个返回字段里、
  去重只活在一个实例字典里，进程重启即丢。现在「待调度」就是 Outbox 里的待发布
  事件（持久化），「已调度」就是 ``mark_published``（持久化）——重启后未发布事件
  会被重新投递（补偿），已发布事件不再出现（去重）。
  **0.3（V03-0.4）**：保存事件的订阅者已改为确定性投影端
  （``event_projection.CountingProjectionSink``），不再创建分析 Job；
  ``AnalysisDispatcher`` 仅作单元历史回归。

合法空结果（no-op）不产生新版本 → 无新事件 → 不调度分析；每个正文 Commit 至多
一个同类型分析 Job，由消费者按 event_id 保证（见 ``analysis_dispatch``）。

保存意图的范围由领域命令签名保证：``chapter_id`` 必填，操作只在该章活动段落上
生效；应用层不放宽（计划 §T14.2「保存意图只允许当前章/场景范围」）。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.application.handlers.manuscript_commands import (
    ManuscriptCommand,
    ManuscriptCommandHandler,
    ManuscriptCommandResult,
    manuscript_command,
)
from hastings.domain.manuscript import commands as manuscript_commands
from hastings.domain.manuscript.operations import EditOperation
from hastings.domain.repository.ids import ProjectId

__all__ = [
    "ANALYSIS_JOB_TYPE",
    "CommitManualEdit",
    "ManualEditIntent",
    "ManualEditResult",
]

ANALYSIS_JOB_TYPE = "manuscript.analysis"

_CONTENT_REQUIRED = (
    "保存意图必须且只能提供 operations 或 full_text 之一"
    "（为什么错：两者都给无从确定意图，都不给没有内容可提交；"
    "怎样算对：优先给 operations，兼容模式给 full_text）"
)


@dataclass(frozen=True, slots=True)
class ManualEditIntent:
    """一次人工保存意图：章范围内提交编辑（操作优先，完整文本为兼容兜底）。"""

    project_id: ProjectId
    chapter_id: str
    idempotency_key: str
    summary: str = "保存正文"
    operations: tuple[EditOperation, ...] | None = None
    full_text: str | None = None
    expected_chapter_base: str | None = None

    def __post_init__(self) -> None:
        if (self.operations is None) == (self.full_text is None):
            raise ValueError(_CONTENT_REQUIRED)

    def command(self) -> ManuscriptCommand:
        """把意图绑成领域命令（稳定身份 + 规范化意图 + 纯函数本体）。"""
        if self.operations is not None:
            return manuscript_command(
                manuscript_commands.apply_edits,
                chapter_id=self.chapter_id,
                operations=tuple(self.operations),
                expected_chapter_base=self.expected_chapter_base,
            )
        if self.full_text is not None:
            return manuscript_command(
                manuscript_commands.save_full_text,
                chapter_id=self.chapter_id,
                text=self.full_text,
                expected_chapter_base=self.expected_chapter_base,
            )
        raise ValueError(_CONTENT_REQUIRED)  # 不可达：__post_init__ 已保证二者其一


@dataclass(frozen=True, slots=True)
class ManualEditResult:
    """保存结果：Commit（未提交时为 no_change）+ 告警。

    不含 ``analysis_job_id``：分析调度不再属于保存路径（评审 #3）。保存成功后，
    下游分析由持久化 Outbox 消费者独立推进。
    """

    edit: ManuscriptCommandResult
    warnings: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        return self.edit.status

    @property
    def is_committed(self) -> bool:
        return self.edit.is_committed

    @property
    def is_no_change(self) -> bool:
        return self.edit.is_no_change

    @property
    def commit_id(self) -> str | None:
        return self.edit.commit_id

    @property
    def new_head(self) -> str | None:
        return self.edit.new_head


class CommitManualEdit:
    """提交保存意图；分析调度交给 Outbox 消费者，不在本路径等待后台任务。"""

    def __init__(self, handler: ManuscriptCommandHandler) -> None:
        self._handler = handler

    async def save(self, intent: ManualEditIntent) -> ManualEditResult:
        """提交保存意图并立即返回；不做任何后台任务调度。"""
        result = self._handler.execute(
            intent.command(),
            project_id=intent.project_id,
            idempotency_key=intent.idempotency_key,
            summary=intent.summary,
        )
        return ManualEditResult(edit=result)

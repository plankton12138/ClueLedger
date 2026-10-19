"""整体调整领域稳定错误码（V03-7.2）。

每个违规错误都携带三要素：错误位置（where）、违反的规则（why）、
怎样算对（fix）。合法空结果不设异常码——「该章已确认无需修改」是正常返回，
不是错误；「任务尚未覆盖全部章」也是正常查询结果（``uncovered`` 列表），
只有**把未做全文检查的章标成 checked_unchanged**才是违规。
"""

from __future__ import annotations

# 覆盖台账：未知章、未做全文检查却要标「无需修改」、状态非法迁移
COVERAGE_UNKNOWN_CHAPTER = "COVERAGE_UNKNOWN_CHAPTER"
#: 只读摘要/目录或区间未覆盖整章，却要标 checked_unchanged（V03-7.2 硬约束）。
COVERAGE_NOT_FULLY_READ = "COVERAGE_NOT_FULLY_READ"
COVERAGE_INVALID_TRANSITION = "COVERAGE_INVALID_TRANSITION"

# 内部工作版本记录本身
REVISION_WORK_NOT_FOUND = "REVISION_WORK_NOT_FOUND"
REVISION_WORK_EMPTY_PROJECT = "REVISION_WORK_EMPTY_PROJECT"

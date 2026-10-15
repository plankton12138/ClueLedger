# Phase 16 验收报告（2026-10-15）

本轮把模型的角色行为与仓库的硬边界分开验证。模型只处理合成的怀表谜案夹具，密钥只用于既有 Provider 认证。

| 角色 | 模型 | 结果 |
|---|---|---|
| Coordinator | deepseek-flash | 选择 draft；items 为空，不抢跑 |
| Planner | deepseek-flash | proposed 人物，通过领域预演 |
| Writer | deepseek-flash | 当前章候选，遵守 Beat 与披露要求 |
| Reviser | deepseek-flash | 指定段落修改，Revision 与范围检查通过 |
| Auditor | deepseek-flash | 可定位原文 Finding，只读工具 |
| Style Observer | deepseek-flash | 数字变化分类为 fact |

[完整分项结果](report.json)与各角色同名 JSON 保存输入 Context、结构化输出和事件。每角色一次定向走查，不能据此推断通用成功率。

[真实断线恢复](provider-recovery.json)：在真实响应已收到输出后注入 OSError；任务进入 retryable_failed，从磁盘重建运行时后，同一 job_id/context_id 返回 waiting。SQLite 临时库不作为交付物。

[闭环转录](closed-loop-transcript.json)使用 FakeProvider，经过真实 API、Tau 与仓库：空项目→作者接受规划→生成一章候选→作者接受→人工改稿→偏好候选→角色变化→批准修订→故意越权后纠正→部分合并→带证据审计→重启。Fake 闭环与真实 Provider 恢复是两份独立证据。

质量门：Python **1469 passed**；mypy **219 个源文件通过**；Ruff 全部通过。前端 **288 passed**，生产构建通过；全新隔离服务的 Playwright **8 passed**（包含新增 J8）。`git diff --check` 通过。

界面证据：[创作任务截图](agent-workflow.png)。界面分别展示候选、任务状态与正式 Commit 证明，规划、观察和审计输出按作者可读的内容呈现。

过程中发现并修复：提案工具的嵌套 `$ref` 根作用域错误；主编把选用例误解为执行正文；Tau 工具结果的 terminate 字段不自动停止后续调用；接受正文缺失 Beat→TextUnit 血缘。均有相应契约或闭环回归。没有改动 Tau 上游源码。

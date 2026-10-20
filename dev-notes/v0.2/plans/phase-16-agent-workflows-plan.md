# Phase 16 计划：无状态 Agent、任务上下文与持久工作流

> 状态：已完成（2026-10-15）；实际交付与取舍见 [构建笔记](../phase-16-agent-workflows.md)，验收与转录见 [报告](../../../eval/runs/2026-10-15-phase16-real-complete/README.md)。
> 依赖：Phase 15 完成。
> 目标：在已经可靠的仓库、命令和修订管线上接入协作主编、策划、写作、修订、审计和风格观察工作者。

## 1. 开始条件

- 所有核心写入可在没有 LLM 的情况下通过命令/Fake Worker 完成；
- Change/Approval/Patch/Commit 边界稳定；
- Job、Lease 和 Outbox 能持久化；
- `tau_agent` 当前公开接口和限制重新核实，适配器不依赖旧 Hastings tools。

## 2. 阶段交付

1. AgentRunner 端口的 Tau 适配器；
2. 六类 Worker 的输入/输出 Schema；
3. Draft/Repair/Audit/Style 四类 Context Builder；
4. 协作主编的意图分类与用例选择；
5. 持久 Job runner、恢复、重试和取消；
6. V2 LLM 工具面和 Run Capability；
7. 输出 Schema 纠正与一次性自修复；
8. Agent 事件与 UI 进度；
9. FakeProvider 回放、真模型走查和分角色评测；
10. V2 新项目完成“规划→一章→人工修改→学习→设定变化→修复→审计”的闭环。

## 3. 非目标

- 不让 Worker 拥有长期私有记忆；
- 不让多个 Worker 直接互聊形成不可观察状态；
- 不引入自动模型路由优化；
- 不做微调；
- 不允许 Audit Worker 写入；
- 不用自然语言确认替代 Approval Receipt。

## 4. 实施任务

### T16.1 Worker Schema

先为每个 Worker 写输入/输出解析测试和非法输出用例。输出必须属于 StoryProposal、ManuscriptPatchProposal、ImpactSupplement、FindingSet、ObservationSet、ClarificationRequest、NoResult 或 WorkerFailure。

### T16.2 Context Builder

每类 Builder 先实现确定性选择，不调用模型摘要。测试目标选择、依赖扩展、偏好范围、前后文窗口、token budget 和 omissions。对同一 Snapshot/参数输出稳定清单。

### T16.3 Tau AgentRunner Adapter

适配器把 Context Package 转换成 system/user/tool 输入，并把事件映射到 Job stream。仅注册 Run Capability 允许的 V2 工具。不得注册旧 `bible_upsert/write_chapter/story_stage`。

### T16.4 输出纠正

模型 Schema 错误时，错误结果告诉模型 where/why/expected，并允许同一 Context 最多一次纠正。第二次失败保存原输出和错误，Job 进入 terminal/review 状态，不无限重试。

### T16.5 协作主编

把用户请求分类为：查询、直接人工命令、建立 Change、继续已存在 Change、审计、生成草稿、解释结果。模糊请求优先读取项目快照并提出少量高价值问题；已经足够明确时直接建立 Proposal，不重复确认。

协作主编不能决定 Approval 是否成立，只能把待确认对象交给界面。

### T16.6 Draft Workflow

已接受 Beat → Draft Context → Writer Patch → Guard → Review → Commit。正文 Writer 不得改变 Canon；发现缺失设定时返回 Clarification/StoryProposal 建议，而不是在正文中偷偷决定。

### T16.7 Repair Workflow

Revision Step → Repair Context → Reviser Patch → Scope/Version/Domain Guard → revision branch。测试 Worker 故意修改 scope 外文本时被拒，并能用错误消息生成缩小后的新 Proposal。

### T16.8 Audit Workflow

先运行确定性审计，再把未解决或语义问题交给 Critic。Critic 只见只读工具，Finding 必须引用 Artifact/TextUnit 和依据；没有定位的泛泛评价进入 editorial note，不算一致性 Finding。

### T16.9 Style Workflow

人工编辑事件 → Style Context → Observer ObservationSet → 规则过滤/归纳 → candidate。明确验证事实修改不会变成文风；Observer 不能直接 active 偏好。

### T16.10 Job Runner

实现 queued/running/waiting/retryable_failed/terminal_failed/completed/cancelled。重启后恢复；环境失败按策略退避；用户取消后停止续租，迟到的 Worker 输出不能提交。

## 5. 建议文件边界

```text
src/hastings/agents/{schemas.py,coordinator.py,planner.py,writer.py,reviser.py,auditor.py,style_observer.py}
src/hastings/application/contexts/{base.py,draft.py,repair.py,audit.py,style.py}
src/hastings/application/workflows/{draft.py,repair.py,audit.py,style_learning.py}
src/hastings/infrastructure/agents/tau_runner.py
src/hastings/infrastructure/jobs/runner.py
tests/v2/agents/
eval/v2/
```

## 6. 工具面验收

每个 Worker 的工具白名单：

| Worker | 只读 | 可提案 | 可执行 |
|---|---|---|---|
| Coordinator | snapshot/read/search/change_read | change_open | 无 |
| Planner | snapshot/read/search/dependency | canon/plan proposal | 无 |
| Writer | read/search/profile | manuscript proposal | 无 |
| Reviser | read/diff/dependency/change_read | canon/plan/manuscript proposal（按步骤） | revision_execute 由工作流调用，不直接给模型 |
| Auditor | read/search/dependency/audit_read | FindingSet 输出 | audit_run 由工作流调用 |
| Style Observer | diff/profile read | observation proposal | 无 |

工具 Schema、错误码和 render 摘要全部做快照/契约测试。

## 7. 评测

### 确定性

- FakeProvider 回放每个 Worker 的成功、空结果、Schema 错误、越权和环境失败；
- Context selection golden tests；
- Job 恢复和迟到输出测试；
- Agent 事件不伪装 Commit。

### 真模型

一个测试点一个 `eval/` 文件，分别评：

- Planner 是否把候选与已确认事实分开；
- Writer 是否遵守 Beat 和 Disclosure；
- Reviser 是否只改影响范围；
- Auditor 是否给出可定位证据；
- Style Observer 是否区分事实与文风；
- Coordinator 是否选择正确用例而不抢跑。

报告按 Worker 分项，不只给一个总分。

## 8. 退出条件

- 四条 Context Builder 有可解释选择与 omissions；
- 所有 Agent 只产生 Proposal/Finding；
- Run Capability 实际限制工具；
- 真实 Provider 中断后 Job 可恢复；
- V2 闭环验收完成并保存转录；
- 旧 V1 Agent 仍只服务 V1 项目；
- 模块文档、eval README 和构建笔记收口。

## 9. 风险控制

- 弱模型反复违反 Schema：最多一次纠正，然后交给人工；
- Context 超长：减少选择范围并记录 omissions，不删除授权/版本/失败证据；
- Worker 角色漂移：工具白名单和输出 Schema 硬限制；
- 模型自称完成：UI 只以 Commit/Job 状态显示完成。

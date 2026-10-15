# Phase 15 构建笔记：变更影响、修订分支与系统修复

> 2026-10-15。状态：已完成（T15.1–T15.9 与最小 Change Center）。

## 1. 交付内容

这一阶段解决的是「改掉早期事实后，如何知道后面要改什么，并让作者保留最后决定权」。交付闭环为：ChangeSet → 确定性影响与正文引用 → 四层 RevisionPlan → 作者凭证 → revision branch → PatchSet/hunk 审阅 → 选中内容原子提交 → 残余影响与审计。

| 任务 | 实际交付与证据 |
|---|---|
| T15.1–3 | ChangeSet 状态/审计历史、带多路径和截断证据的依赖遍历、mentions 的来源/置信度、可注入语义补充。语义故障保存 coverage/warnings，确定性报告保留。`test_change_set_status`、`test_impact_traversal`、`test_mention_scan`、`test_semantic_scope` |
| T15.4 | Canon → Plan/State → Manuscript → Audit 分组与依赖 DAG，最小环证据、拆组、收缩范围、接受风险；章节 scope 展开为正文与结构对象范围。`test_revision_plan`、`test_phase15_acceptance` |
| T15.5 | 作者签发、报告/计划/种子/base/scope 指纹、撤销、一次消费、一小时有效期。签发/批准时拒绝分析后故事变化；新执行检查到期，已启动恢复不因超时失去恢复资格。`test_approval_receipt`、`test_branch_executor` |
| T15.6 | 按批准计划在分支执行；范围、内容 Schema、身份/归属保护和 Lease 守卫；结果与状态同提交。持久 execution-job 绑定逻辑操作，已提交步骤从仓库幂等结果读取，重启、更换客户端请求键也不重新调用 Worker。`test_branch_executor`、`test_patch_guards`、SQLite 三章验收 |
| T15.7 | 冻结分支头与 base 的 PatchSet；结构字段与正文行块 hunk 可选择；三方比较拒绝覆盖人工冲突。选中内容、review→committed、审阅决定和重算报告同 Commit。拒绝/部分接受项保留 open，accepted risk 可见。`test_review_workflow` |
| T15.8 | 复用时间线、认知顺序、Setup/Payoff、必要线索披露规则；Finding 保存证据 Commit。复查历史定位实际消除问题的内容 Commit，工作流元数据提交不能冒充修复。`test_audit_workflow` |
| T15.9 | 从任意 Commit 开 rewrite branch，锁定 TextUnit，按 Chapter 顺序分组；提供 execute/abort/resume/compare/abandon，分支成功或失败都不替换 main 正文。分支提交与进度记录之间中断可恢复。`test_rewrite_workflow` |
| API/UI | `/api/v2/projects/{id}/changes`、分析/计划/批准/执行/审阅/合并、审计、重写接口。前端「变更中心」展示阶段、路径、分级、确认范围、差异选择与风险；人工内容经 Worker 端口提交。`test_change_center`、浏览器 J7 |

三章核心验收见 `tests/v2/changes/test_phase15_acceptance.py`：协助者改为幕后操纵者，影响覆盖关系、认知、两个 Beat 和三处 TextUnit；限制前三章，第四章被截断；Canon 提交后模拟进程退出，重新连接 SQLite 后续跑；接受两处正文、拒绝第三处，main 只包含接受内容，剩余一项 open；审计仍指出提前知情。

## 2. 关键决策

- **事实、授权、执行、审阅是不同事实。** 执行分支隔离故事内容；main 可以保存工作流进度，但不能混入半成品正文。「main 未改」指故事内容不变，不要求分支指针完全不前移。
- **重启依赖仓库事实。** Changes handler 将命令指纹和领域结果与业务变化同 Commit 保存；恢复读取原结果，不重新分配对象 ID。RepositoryReader 新增 `find_commit_result`、`commit_history`，均为受限只读查询。
- **接受不是自动证明故事一致。** 完整接受某对象的补丁表示该影响已处理；部分 hunk、拒绝项及原有风险继续可见。确定性依赖重新遍历并保留新路径，独立审计仍能发现修订后冲突。committed 只表示本轮作者选择已落库，不等于所有问题消失。
- **正文块以行段为最小审阅块。** 同一 TextUnit 内独立行块可分别接受。当前对被修改字段采用保守冲突策略：人工改过同一字段即要求重新比较，其他字段的人工编辑可保留。
- **内容修订不隐式重接依赖图。** 通用 Patch 不允许改变稳定 ID、章节/场景归属和依赖字段；这些变更继续使用已有领域命令。避免正文 Worker 通过修改引用字段破坏图与内容的一致性。
- **本阶段不接真实模型。** API 接收人工候选，测试用 FakeWorker；真实 Reviser/Writer、持久后台任务调度与上下文预算属于 Phase 16。
- **不迁移旧工作区。** 服务器可通过 `--v2-db <path>` 显式装配独立 V2 仓库；V1 slug 不映射为 V2 project ID。新增 Artifact kind 和可选 JSON 字段，不修改 SQLite 表结构，不改 `tau_agent` / `tau_ai`。

## 3. Agent 设计经验

**① 错误消息是喂给模型的 API。** 过期批准、越界对象、人工冲突分别给出哪里错、为什么错、怎样恢复；前端直接展示结构化错误的 why/fix。语义服务异常保留在 coverage/warnings，不能伪装为作者违规。

**② 硬约束放工具层。** 确认页只是展示；真正授权取决于作者 actor、凭证指纹、期限、计划目标、内容守卫、基础版本和 Lease。通用正文 Patch 无权移动段落归属，即使对象 ID 在批准列表内也必须拒绝。

**③ 泄压阀区分违规、空结果、环境故障。** 没有下游影响不建空修订分支；Worker 可以合法返回空提案。语义补充故障仍交付确定性报告；执行故障保留分支和失败记录，正式故事内容保持。合并后的审计环境故障返回 pending，可独立重跑，不把已成功的正文提交描述为失败。

**④ 可观测性是事件契约的一部分。** 影响、凭证、执行步、审阅、审计和重写事件均与相应 Commit 一起写入 Outbox。分支提交结果是恢复证据；Finding 必须引用检查版本和真实修复版本，不能凭 UI 状态或任务口头结论关闭。

## 4. 评审与验证记录

- 本轮先补失败测试，再实现恢复、审批到期、三方冲突、非法归属修改、跨重启命令重放、语义故障降级及修复 Commit 归因。
- 初始中断恢复失败的根因：重试生成新 Job ID，而 Job ID 进入提交事件指纹；旧步骤的幂等键因此冲突。修复为恢复读取已提交步骤，并持久保存原逻辑操作键。
- 浏览器验收发现新表单按钮沿用了组件默认 `type=button`，点击不会提交；改为显式 submit。后续必须先构建最新产物，避免复用测试服务时验证旧页面。
- 质量门：Python 全量 **1402 passed**；前端单测 **288 passed**；独立临时服务上的浏览器旅程 **7/7 passed**（含 J7）；`ruff check src tests`、相关文件格式检查、mypy（206 源文件）和前端生产构建通过。构建保留既有大 chunk 提示，Python 有一条 TestClient 依赖弃用提示，均非失败。
- 全量浏览器检查修正了旧 J2 的过期断言：intake 未经确认不能写角色，测试现在断言两次拒绝且真实账本为空，不以 Fake 的「已建档」口头结论作为成功证据。未改 V1 生产守卫。
- J3/J5 会修改/删除预置工作区，复用旧服务会污染第二次运行。最终在新端口、全新临时数据服务上验证全部七条；Playwright 现支持 `HASTINGS_E2E_PORT`，显式指定时禁止复用旧服务。
- Windows 上自动管理的测试服务退出曾等待不结束；最终将服务和浏览器运行器独立启动，在全新 8934 临时服务上取得 `7 passed (5.8s)`、运行器退出码 0。临时浏览器配置已删除，不进入交付。

## 5. 使用与边界

```powershell
uv run python -m hastings.server --v2-db ./data/v2.sqlite
uv run pytest tests/v2/changes tests/v2/server/test_change_center.py -q
cd frontend
npm run build
$env:HASTINGS_E2E_PORT = "8932"  # 使用未占用端口，启动全新临时数据服务
npm run test:e2e -- e2e/j7-change-center.spec.ts
```

使用已有 V2 项目编号进入变更中心。人工候选以对象编号到完整新内容的映射输入；正文与 Story 的真实模型生成不在本阶段。全局重写本轮只提供应用层与 HTTP 控制面，比较/放弃无需切换正式稿；正式采用重写稿仍需后续产品交互。V1 与 V2 的默认切换和旧链路退出仍属于 Phase 17。

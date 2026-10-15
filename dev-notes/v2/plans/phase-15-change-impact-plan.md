# Phase 15 计划：变更影响、修订分支与系统修复

> 状态：已完成（2026-10-15）。三章核心验收、重启恢复与质量门证据见 [Phase 15 构建笔记](../phase-15-change-impact.md)。
> 依赖：Phase 13、Phase 14 完成。
> 目标：实现 Hastings 的核心差异——修改故事事实后，系统能解释影响、规划修订、在分支上生成/接收补丁并重新验证。

## 1. 开始条件

- Story Domain 能产生显式依赖边和 Story views；
- Manuscript TextUnit 能关联 Scene/Beat/Canon 或保存 mentions proposal；
- Branch、Diff、Approval、Lease 的基础能力可用；
- 已有基准三章悬疑 fixture，并能提交到 V2 Repository。

## 2. 阶段交付

1. ChangeSet、ImpactItem、RevisionPlan、PatchSet、Finding 状态模型；
2. 确定性依赖遍历与因果路径；
3. 语义影响补充端口，第一版可用 Fake 实现；
4. 影响分级和 scope 截断；
5. 修订计划依赖排序；
6. revision/rewrite branch 工作流；
7. 审批凭证、执行 Lease 和 Patch 范围守卫；
8. 逐 Artifact/hunk 审阅与部分接受；
9. 提交后重新分析未解决影响；
10. 最小 Change Center API/UI。

## 3. 非目标

- 不接入最终真实 Reviser/Writer；Fake Worker 或人工 Patch 足以验收管线；
- 不自动解决所有 `review` 项；
- 不做跨多个用户的协同审批；
- 不用向量检索替代显式依赖图；
- 不在主分支直接执行批量修订。

## 4. 实施任务

### T15.1 ChangeSet 与状态机

先写所有合法/非法迁移测试。状态变化必须记录 actor、时间、原因和基础 Commit。`Analyzed → Approved` 仅接受作者用例产生的 Approval；Agent tool 不具备该命令。

### T15.2 确定性影响遍历

实现按边类型配置的传播规则和默认严重度。输出完整 path、visited 证明、scope cutoff 和分析统计。用小图、环图、菱形多路径、大图边界分别测试。

### T15.3 正文引用补充

显式 `renders` 边优先；从旧正文或人工文本抽取的 mentions 保存 source/confidence。低置信 mentions 只产生 `review`，不能升级为 must_fix。

### T15.4 Revision Plan

把 ImpactItem 组织成 Canon → Plan/State → Manuscript → Audit 四层 DAG。检测计划环路并输出最小环路径；允许作者手工拆组、调整范围或接受风险。

### T15.5 Approval Receipt

作者确认页面展示：变化原因、基础版本、允许对象、预计 Patch 数、must_fix 和风险。签发 Receipt 后任何范围或 base 变化使其失效。测试过期、撤销、次数和 scope。

### T15.6 Branch 执行器

从 base Commit 建 revision branch，按步骤获取 Lease、调用 Worker 端口、验证 Proposal、提交到 branch。第一版用 Fake Worker 返回可预测 Patch。失败保存 Job 状态，不移动 main。

### T15.7 差异审阅与合并

按 Artifact 和 hunk 展示。作者可全部接受、部分接受、拒绝或标记 accepted risk。部分接受形成新 Patch/Commit 后自动重算剩余影响，原 ImpactItem 不静默删除。

### T15.8 审计闭环

实现最小确定性审计：时间线、知识顺序、Setup/Payoff、必要线索披露。Finding 引用 Commit 和证据。只有包含对应修复的 Commit 成功后才能转 `fixed`。

### T15.9 全局重写

从任意 Commit 建 rewrite branch；允许设定锁定 TextUnit；按 Chapter group 执行。验收重点是可中止、可比较、可放弃，不追求本阶段生成质量。

## 5. 建议文件边界

```text
src/hastings/domain/changes/{models.py,status.py,impact.py,planning.py}
src/hastings/domain/audit/{models.py,rules.py}
src/hastings/application/workflows/{change_analysis.py,revision.py,rewrite.py,audit.py}
src/hastings/application/authorization/{models.py,service.py}
src/hastings/application/guards/{scope.py,approval.py,lease.py}
src/hastings/interfaces/api/changes.py
tests/v2/changes/
tests/v2/audit/
```

## 6. 测试先行清单

- ChangeSet 全状态机；
- 多跳路径和环路去重；
- 多路径保留；
- scope 截断；
- 语义补充失败降级；
- plan DAG 与环路错误；
- Approval 绑定 base/scope；
- Lease 丢失后旧 Job 不能提交；
- branch 中途失败不动 main；
- 部分 hunk 接受；
- 提交后重算剩余影响；
- accepted risk 保持可见；
- Finding 没有 Commit 不得 fixed；
- 全局重写放弃后旧稿不变。

## 7. 核心验收用例

基准故事中把“协助者”改为“幕后操纵者”：

1. Change Set 绑定 main head；
2. 影响报告至少到达角色关系、KnowledgeState、两个 Beat 和三处 TextUnit；
3. 每项显示依赖路径和严重度；
4. 作者把范围限制在前三章并批准；
5. revision branch 先更新 Plan，再产生三处正文 Patch；
6. 作者接受两处、拒绝一处；
7. main 得到只含接受内容的新 Commit；
8. 重新分析仍显示一项 open；
9. 审计指出对应提前知情或动机冲突；
10. 任何步骤都可重启恢复。

## 8. 退出条件

- 核心验收用例通过，证据保存为测试和构建笔记；
- 确定性影响不依赖模型；
- Agent/Fake Worker 只能在批准范围内提案；
- 主分支不会因执行中失败出现半修订；
- Change Center 能区分 analyzed、executing、review、committed；
- no-impact 为合法空结果且不建空分支；
- 模块文档更新为 as-built。

## 9. 停止条件

- Story/Manuscript 仍没有稳定 ID 关联；
- 为展示效果要求跳过 Approval 或直接改 main；
- 影响图把低置信语义边当硬依赖；
- 部分接受后无法重新计算剩余影响。


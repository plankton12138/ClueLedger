# Command Gateway：命令、守卫、工具与授权

> 状态：target，基础能力 Phase 12 交付，领域命令随 Phase 13–15 扩充。
> 目标包：`hastings.application.commands`、`hastings.application.guards`、`hastings.application.authorization`。

## 0. Phase 15 已交付边界

变更工作流的作者凭证、一次消费、撤销、期限、base/scope/种子/计划/报告指纹、Lease、提案范围、Schema 与身份保护已交付。实现位于 `domain/changes/{receipt,commands,execution,patch_guard}.py`、Changes handler 与修订/审阅用例，未拆成独立通用框架。作者签发与合并使用受信任应用用例，Worker 只返回 Proposal。Phase 16 已接通角色与 Capability 工具交集、固定版本读回、结构化候选、一次纠正及共享应用守卫；审计只读、Observer 不能激活偏好，详见 [编排与上下文 §0](orchestration-context.md)。见 [Phase 15 构建笔记](../phase-15-change-impact.md)。

## 1. 职责

Command Gateway 是所有领域写入的唯一入口。它把“模型调用了工具”与“项目状态真的改变”分开：工具只能提交命令或 Proposal；Gateway 决定该请求在当前版本、范围、授权和领域规则下能否形成 Commit。

守卫不负责说服模型。它必须给出稳定、可恢复、能被人和模型理解的结果。

## 2. 处理管线

```mermaid
flowchart LR
    I[Command Input] --> S[Schema Guard]
    S --> C[Capability Guard]
    C --> V[Version Guard]
    V --> R[Scope Guard]
    R --> D[Domain Guard]
    D --> A[Approval Guard]
    A --> M[Commit Service]
    M --> O[Commit + Outbox]
```

只读查询在 Schema、Capability 和 Scope 后执行，不经过 Approval；任何写操作都不能绕过 Version、Domain 和 Commit。

## 3. 三种权力证明

### 3.1 User Intent

用户当前操作表达的直接意图。例如在编辑器中点击“保存当前章节”，它只授权提交该编辑器传来的章节差异。

### 3.2 Approval Receipt

用于 Agent 或批量工作流。字段：

```text
approval_id
project_id
change_set_id
base_commit_id
allowed_command_types
allowed_scope
max_uses
expires_at
issued_by
summary_shown_to_user
revoked_at
```

Approval 不能跨 Change Set、跨基础版本或无限期复用。基础版本变化后，Gateway 应要求重新分析或重新确认。

### 3.3 Run Capability

分配给一次 Agent/Job 的最小工具能力，例如“读取当前项目 + 为 ch05 生成正文 Patch”。Capability 只能缩小 Approval 的范围，不能扩大授权。

## 4. LLM 可见工具面

工具数量保持小而意图清楚。下表是 V2 稳定目标，具体 JSON Schema 在对应阶段冻结。

### 4.1 只读工具

| 工具 | 作用 | 典型返回 |
|---|---|---|
| `workspace_snapshot` | 获取指定 Commit 的项目摘要与 stale 状态 | 版本、里程碑、开放变更和目标索引 |
| `artifact_read` | 按稳定 ID 读取 Canon、Plan、正文或偏好 | 内容、revision、来源、关系摘要 |
| `artifact_search` | 按类型、名称、标签和范围检索 | 稳定 ID 列表，不返回整库全文 |
| `dependency_trace` | 查看对象之间的依赖路径 | 路径、边类型、来源和置信度 |
| `repository_diff` | 比较 Commit/Revision | 结构化 diff 与 hunk IDs |
| `change_read` | 读取 Change/Impact/Plan/Patch 状态 | 当前状态和下一合法动作 |
| `audit_read` | 读取 Finding 和证据 | 严重度、范围、状态、引用 |
| `author_profile_read` | 读取当前任务可用偏好 | 结论、范围、证据摘要 |

### 4.2 提案工具

| 工具 | 作用 | 不直接产生的效果 |
|---|---|---|
| `change_open` | 根据作者意图建立 Change Set 草案 | 不改变 Canon/正文 |
| `canon_patch_propose` | 提出 Canon 领域命令或 Patch | 不直接写 Bible |
| `plan_patch_propose` | 提出章节/场景/节拍变化 | 不移动正式结构 |
| `manuscript_patch_propose` | 提出正文 hunk | 不覆盖正文 |
| `author_observation_propose` | 提出作者习惯观察 | 不直接激活偏好 |
| `revision_plan_propose` | 根据 Impact 组织修订步骤 | 不开始批量执行 |

### 4.3 分析与执行工具

| 工具 | 作用 | 守卫重点 |
|---|---|---|
| `change_analyze` | 运行确定性影响分析并可请求语义补充 | base commit、scope、Job capability |
| `audit_run` | 运行指定审计器 | 默认只读、检查范围 |
| `revision_execute` | 执行已批准计划中的指定步骤 | approval、lease、step dependency |
| `change_commit` | 提交已审阅且获批的 Patch | approval、head、hunk selection、domain invariants |

Agent 没有 `approval_grant`、`approval_revoke` 或任意 SQL/文件写工具。授权由作者界面或受信任应用用例创建。

## 5. 守卫

| 守卫 | 检查 | 拒绝后的恢复方式 |
|---|---|---|
| Schema | 字段、类型、稳定 ID、枚举 | 返回最小正确示例 |
| Capability | 当前 Agent/Job 是否有工具和目标权限 | 缩小请求或由编排器创建正确任务 |
| Version | base commit/revision 是否为预期版本 | 重新读取 diff、重算或人工合并 |
| Scope | 修改对象和 hunk 是否在批准范围 | 拆分 Proposal 或申请新范围 |
| Domain | 业务不变量是否满足 | 修改命令或先完成前置变化 |
| Approval | 是否有未撤销、未过期、次数足够的审批 | 向作者展示具体变化重新确认 |
| Lease | 长任务是否仍拥有资源租约 | 停止旧任务并重新调度 |
| Idempotency | 是否已执行同一请求 | 返回原结果，不重复提交 |

守卫执行顺序固定，避免把未经授权的数据送入昂贵模型或让领域错误掩盖版本冲突。

## 6. 错误契约

工具错误统一返回：

```json
{
  "ok": false,
  "category": "violation",
  "code": "COMMAND_SCOPE_VIOLATION",
  "where": "manuscript_patch_propose.targets[2]",
  "why": "目标 ch07-p18 不在本次批准的 ch05 范围内",
  "expected": "只修改 ch05，或为 ch07 建立新的影响项并请求扩展范围",
  "retryable": false,
  "details": {}
}
```

`category` 只有：

- `violation`：修改输入或重新授权；
- `empty`：请求合法但没有变化/结果；
- `environment`：外部依赖故障，可恢复后重试；
- `conflict`：基础版本变化，需要重新读取或合并。

错误文本必须由结构化字段渲染，不能只保留给人看的异常字符串。

## 7. 边界与不变量

- 工具描述不能承载硬约束，硬约束必须有可执行守卫；
- 项目里程碑、对象数量和 prompt 中的“已确认”不能充当 Approval；
- 人工编辑的直接意图只覆盖本次保存 payload；
- Agent 不能签发、扩大或复用 Approval；
- 一个工具调用不能同时“发现问题并静默修复”；
- Commit 成功前不得返回 `completed`；
- no-op 返回合法空结果，不消耗 Approval 使用次数；
- 环境故障不得被包装成模型违规；
- Tool Schema、领域命令 Schema 和错误码都必须有契约测试。

## 8. 测试重点

- Agent 在没有 Approval 时可以提案但不能提交；
- Approval 绑定旧 head 时提交被拒；
- Scope 外 hunk 被拒且 Scope 内 hunk 不被部分提交；
- User Intent 只允许保存当前编辑范围；
- Agent 无法调用授权工具；
- no-op 不产生 Commit、不消耗授权；
- 幂等重试返回原 Commit；
- 环境错误保持 Job 可重试；
- 每种错误都有 where/why/expected。

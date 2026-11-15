# Interfaces & Observability：API、事件、任务与导入导出

> 状态：Phase 16 的 Job/API 已交付（§0）；Phase 17 的旧数据迁移已退役，V2 导出及旧写路径退出保留（§0.1）。七页作者工作台与偏好裁决出口已交付（§0.2）；下文尚未逐项标记的资源仍是目标契约。
> 目标包：`hastings.interfaces`、`hastings.infrastructure.events`、`hastings.infrastructure.jobs`。

## 0. Phase 16 as-built

API 实际前缀为 `/api/v2/projects/{project_id}`，入口 `interfaces/api/agents.py`：
| 资源 | 用途 |
|---|---|
| `GET /story`、`POST /coordinate` | 当前故事对象与明确用例选择；不代签审批 |
| `GET/POST /agent-jobs` | 持久任务列表/提交；显式请求键与可选上下文预算 |
| `GET /agent-jobs/{id}`、`GET /agent-jobs/{id}/events?after=N` | 状态/候选及独立单调事件流 |
| `POST /agent-jobs/{id}/run`、`/cancel`、`/accept` | 运行/取消/作者接受；接受要求 expected_head |
| `POST /changes/{id}/execute-agent` | 使用已批准步骤驱动 Reviser，不给模型执行与审批工具 |

服务生命周期自动执行排队/退避任务，启动扫描未发布 Outbox，恢复检查点，并补交分析元数据。SQLite Job 表与 repository 表在同一数据库文件内，分别维护执行事实与项目事实；Job 事件的 `commit_id/project_sequence` 始终为空，不冒充业务提交事件。接受及分析 Commit 证明放在对应结果里。记录 `after` 游标可跨重启续读。

前端入口为“变更中心→规划与生成任务”，支持空项目、规划/正文审阅、人工保存、任务状态/事件、取消/重试与审计；批准后的修复仍在变更中心审阅。当前进度采用轮询，未新增会话式 WebSocket Agent。V1 界面和工具保持其原项目边界。下文资源清单及导出接口仍是后续架构目标；证据见 [Phase 16](../phase-16-agent-workflows.md)。

## 0.1 旧工作区迁移已退役（2026-10-15）

项目未上线，没有需要承诺兼容的生产工作区。旧迁移面板、客户端、slug 迁移接口、
全工作区导入器和账本映射器均已删除；`settings.py` 不再读写迁移项目/数据库映射。
CLI 对 V2 工作区提示使用 Web 工作台；前端从本浏览器登记的原生项目进入作品。

V2 原生项目、正文导入、业务事件及备份包导出/重导入继续保留。
导出测试与容量测量直接创建 V2 数据，旧迁移夹具和专用测试已删除。
旧写路径退役证明见 `tests/v2/server/test_legacy_write_unreachable.py`，
接口退役与原生写入回归见 `tests/test_migration_retired.py`。
决策及本地数据处理边界见[退役记录](../phase-17-workspace-retirement.md)。

## 0.2 V2 七页作者工作台（as-built）

`frontend/src/pages/` 为项目概览、创作台、故事账本、变更影响、修订审阅、悬疑审计与作者模型。
所有页面共享 `state/v2.ts` 的项目身份；作品写操作只走 V2。`ChangesPage` 用两个页面状态承载同一个服务端变更集。
`components/SettingsDrawer` 收纳新建/编号找回与服务级模型档案。

新增 `interfaces/api/author_decisions.py`：

- `POST /api/v2/projects/{id}/author-model/preferences`：作者显式声明，带 `expected_head` 与幂等键；
- `POST /api/v2/projects/{id}/author-model/preferences/{preference_id}/revoke`：带原因撤销，保留历史。

这两条仅适配已有领域命令与 Commit Service，不新增任意对象写入口。候选激活仍服从原归纳规则。
业务 WS 按 sequence 去重和补齐，事件合并为 REST 回读信号；任务过程单独轮询。
正文草稿在切页时保留，切项目/切章前处理脏稿，版本冲突保留输入；审阅依据不因刷新被静默替换。

证据、截图与已知边界见 [phase-17-frontend-workbench.md](../phase-17-frontend-workbench.md)。

## 0.3 模型接入与切换（Phase 18 as-built）

服务级接口：`GET/PUT /api/model-selection`、`POST /api/providers/connect`、
`GET /api/providers/{name}/model-choices`。来源分普通 API、套餐与本地，服务端预填地址。
当前选择由同一 ProviderBook 驱动 API 与 V2 工作者，保存配置后后续任务生效。
模型列表不支持时才回退内置候选；鉴权/网络错误不能显示为验证成功。
本地免密、绕过系统代理、Ollama 安装与下载流程保留。详见 [Phase 18](../phase-18-model-access.md)。

## 1. 职责

本模块把作者界面、CLI、导入导出和后台任务连接到应用用例，并把 Commit、Job 和 Agent 进度转换成稳定的可观察事件。

接口层只做协议适配、身份上下文和结果映射，不复制领域规则。

## 2. 面向界面的资源

建议 REST 资源围绕稳定领域对象，而不是围绕页面组件：

```text
/projects
/projects/{id}/branches
/projects/{id}/snapshot?at=<commit>
/artifacts/{id}?at=<commit>
/changes/{id}
/changes/{id}/impact
/changes/{id}/revision-plan
/patch-sets/{id}
/approvals
/jobs/{id}
/audits/{id}
/author-profile
/imports
/exports
```

命令端点使用 Idempotency-Key，并要求客户端传基础 Commit/Revision。读端点允许指定 `at` 查看历史；未指定时读取明确的 branch head。

## 3. 编辑器协议

正文保存请求至少包含：

- project/branch/chapter ID；
- `base_revision_id`；
- Edit Operations 或带校验值的完整内容；
- 客户端幂等键；
- 用户可选修改说明。

成功响应返回新 Revision、Commit、规范化 diff 和分析 Job ID。冲突响应返回当前 Revision 与客户端基础版本之间的 diff，前端不得把 409 当作普通失败后自动覆盖重试。

## 4. 事件分类

### 4.1 业务提交事件

来自 Commit Outbox，具有项目 sequence：

- `repository.commit.created`；
- `artifact.revision.created`；
- `canon.changed`；
- `narrative.changed`；
- `manuscript.changed`；
- `author.preference.changed`；
- `change.committed`；
- `audit.finding.changed`。

### 4.2 工作流事件

来自 Job Store，不能冒充业务提交：

- `job.queued/running/waiting/completed/failed`；
- `impact.analysis.progress`；
- `revision.step.progress`；
- `agent.output.proposed`。

### 4.3 对话流事件

模型文本流、工具调用展示和思考摘要保留在会话通道。它们可以关联 Job/Change ID，但不进入项目 Commit sequence。

## 5. 事件信封

```json
{
  "event_id": "...",
  "event_type": "manuscript.changed",
  "project_id": "...",
  "commit_id": "...",
  "project_sequence": 42,
  "job_id": null,
  "occurred_at": "...",
  "actor": {"kind": "user", "id": "..."},
  "correlation_id": "...",
  "payload": {}
}
```

Job 和对话事件没有 `project_sequence` 时使用各自 stream sequence。消费者必须按 `(stream, sequence)` 去重，不能假定 WebSocket 永不重连或不重复。

## 6. 前端投影

第一批投影：

- Project Explorer：分支、head、最近 Commit 和 stale 数量；
- Canon/Plan Editor：当前 Revision 与依赖摘要；
- Manuscript Editor：章节正文、版本、保存冲突；
- Change Center：意图、影响、计划、Patch 和批准；
- Diff Review：按 Artifact/hunk 接受或拒绝；
- Author Model：偏好、证据、作用范围和撤销；
- Audit Center：Finding、证据、状态和对应 Change；
- Activity：Commit 和 Job 分开呈现。

前端缓存是投影。收到业务事件后按 commit/revision ID 更新；无法补齐时重新读取 Snapshot，不在浏览器中推演领域状态。

## 7. 导入与导出

### 导入

不再支持 V1 工作区迁移。V2 章节文本导入与 V2 备份包恢复走既有领域命令和统一提交，
不得恢复旧的通用工作区映射入口。

### 导出

从指定 Commit 生成 Markdown/YAML/JSON 包，包含 manifest、内容哈希和 schema version。导出不修改仓库；重新导入时按显式 import 工作流处理。

## 8. 可观测性

每个请求、Job、Agent 调用和 Commit 使用 correlation ID 串联。至少记录：

- 用例名称、基础 Commit 和目标范围；
- 守卫拒绝码；
- Context 选择数量、裁剪和 token 使用；
- Provider、延迟、重试与 Schema 纠正；
- Patch 数量、接受/拒绝 hunk；
- Commit ID、sequence、outbox 发布延迟；
- Job 恢复次数和最终状态。

日志不记录 API key、完整未发布正文或未脱敏 prompt。调试需要正文时保存受项目权限保护的引用。

## 9. 不变量

- REST/WS/CLI 调用相同 application use case；
- WebSocket 断线不影响 Commit 和 Job；
- 业务完成 UI 必须以 Commit ID 为依据；
- 重复事件不会重复应用前端结果；
- 导入先报告后写入，歧义不得静默猜测；
- 导出可从任意 Commit 重建且带 schema version；
- Job completed 不等于 Change committed；
- 事件消费者失败不回滚已经成功的业务 Commit；
- 项目内 sequence 只有 Repository 分配。

## 10. 测试重点

- REST 和 CLI 对同一命令产生相同 Commit；
- 409 冲突包含可合并信息；
- WebSocket 重连按 sequence 补发并去重；
- Outbox Publisher 崩溃重启后继续；
- Job completed 但 Patch 未批准时 UI 不显示“正文已更新”；
- 旧工作区 dry-run 导入不写任何数据；
- 导出后校验 manifest 和内容 hash；
- 日志脱敏测试覆盖 provider 配置和正文引用。


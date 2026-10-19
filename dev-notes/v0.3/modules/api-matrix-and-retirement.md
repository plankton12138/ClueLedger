# API 矩阵、入口拒启与默认工作区

> 状态：**as-built**（V03-8.6，2026-10-19）。证据：[8.6](../notes/v03-8.6-api-matrix-and-default-desk.md)、[API 矩阵](../artifacts/v03-8.6-api-matrix.md)。
> 实际包：`interfaces/api/_retired.py`、`interfaces/api/{changes,agents,revision}.py`、`server/v2.py`、`frontend/src/App.tsx`。

## 1. 职责

把「0.3 提供什么、不提供什么」写成可执行约束：126 条 V2 路由逐条分类；被排除功能的启动面在 HTTP 层统一拒绝；默认壳是写作台。

## 2. 分类

| 类别 | 含义 | 约束 |
|---|---|---|
| 0.3 消费面 | 写作台/客户端直接使用 | 走统一发布或只读投影 |
| 内部/演进保留 | 有 0.3 真实用例（agent-jobs/task-drafts 等） | 凭据、预算、来源核对 |
| V1 兼容保留 | 旧工作区读写 | 0.3 装配不消费、无自动链 |
| 必须拒启 | 本版排除功能 | 410 + 三要素 + 无副作用 |

## 3. 拒启契约

- 错误码 `FEATURE_RETIRED_IN_03`，HTTP **410**（区分于 400 参数错、403 无权限、404 不存在）。
- 三要素的 `fix` 必须给本版替代路径（拒启本身就是迁移指引）。
- 覆盖 13 个启动面：变更集 analyze/plan/approve/execute/execute-agent/review/merge、`POST /audit`、`rewrites` 四个、`POST /coordinate`。
- 只读保留：`GET /changes`、`GET /changes/{cs}`。
- 证明：`tests/v2/test_legacy_entry_guard.py`（20 项：410、无副作用、只读可用、旧写路径归因、`style_observer` 拒启、作者模型路由不存在、语料静态守卫）。

## 4. 旧写路径收敛

- `POST /chapters/import`（旧单章导入）从直提领域命令改为 `publish_command`，返回 `operation_id`——8.6 前是唯一绕过统一发布器的写路径。
- save/restore/import/adopt/finish/undo/publish 全部可归因（`GET /operations/{id}`）。

## 5. 默认工作区

- `App.tsx` 默认视图与导航首位 = 写作台；进入写作台自动收起旧版对话栏。
- 旧七页保留为兼容入口：变更影响/修订审阅改只读回看、悬疑审计移除启动按钮。
- 删除未挂载的 `ChangeCenter.tsx` 与 `changeAction`/`runAudit`/`coordinate` 客户端函数。

## 6. 未验与留白

- V1 语料写接口（`POST /api/corpus` 等）仍可**手工**调用（数据保留策略）；0.3 无入口、无自动消费，是否拒启待产品裁决。
- `coordinator`/`auditor` 角色仍可创建 Job（有凭据门禁），服务旧任务回看。
- 逐章审批流底层服务保留未删，只保证入口不可达。

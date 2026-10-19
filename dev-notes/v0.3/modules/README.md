# Hastings 0.3 模块 as-built 导航

> 状态：**as-built，2026-10-19**，基线 commit `2aa7138`（V03-8.7）。
> 本目录只登记 **0.3 新增或契约实质变化**的模块；未变化模块的契约仍在
> [`dev-notes/v0.2/modules/`](../../v0.2/modules/README.md)（仍是当前实现，不是历史文档）。
> 模块文档写契约层（职责/公开接口/关键决策/约束/演化），实现细节以代码为准。

## 1. 与 v0.2 模块文档的关系

| v0.2 模块文档 | 0.3 是否有实质变化 | 0.3 处置 |
|---|---|---|
| [repository-kernel](../../v0.2/modules/repository-kernel.md) | 是：新增 `revision_work`/`operations`/`execution_log` 辅助表、`journal_mode` 加固 | 契约沿 v0.2，增量见本文档 §3 |
| [story-domain](../../v0.2/modules/story-domain.md) | 否（Beat 类型保留，见未验项） | 契约不变 |
| [manuscript-author-model](../../v0.2/modules/manuscript-author-model.md) | 是：作者模型资料面退出 0.3；正文侧新增锚点/核对 | 契约以 [desk-surface](desk-surface.md) / [publish-import-recovery](publish-import-recovery.md) 为准 |
| [change-impact](../../v0.2/modules/change-impact.md) | 是：逐章审批流退役（HTTP 410），只读保留 | [api-matrix-and-retirement](api-matrix-and-retirement.md) |
| [guard-authorization](../../v0.2/modules/guard-authorization.md) | 是：意图三分 + 执行凭据 + 预算门 | [revision-and-coverage](revision-and-coverage.md) |
| [orchestration-context](../../v0.2/modules/orchestration-context.md) | 是：自动链切断、旧任务隔离、上下文装配 | [publish-import-recovery](publish-import-recovery.md) §4 |
| [interfaces-observability](../../v0.2/modules/interfaces-observability.md) | 是：执行日志通道、SSE 游标补齐、拒启面 | [execution-log](execution-log.md)、[api-matrix-and-retirement](api-matrix-and-retirement.md) |

## 2. 0.3 模块索引

| 文档 | 覆盖 | 关键包/文件 | 状态 |
|---|---|---|---|
| [desk-surface.md](desk-surface.md) | 写作台三部分工作区、响应式、键盘/输入法、唯一输入框、主区视图 | `frontend/src/components/desk/*`、`state/desk*` | as-built（V03-1.1/1.5/5.x/6.4/8.5） |
| [revision-and-coverage.md](revision-and-coverage.md) | 意图三分、覆盖台账、预算/修正上限、修订任务状态机、统一发布与撤销 | `application/services/{intent_service,revision_task_service,revision_work_service,revision_publish_service}.py`、`domain/revision/*` | as-built（V03-7.x） |
| [publish-import-recovery.md](publish-import-recovery.md) | 统一发布管线、导入预览、备份/恢复、启动恢复与旧队列隔离 | `application/services/{publish_manuscript_change,import_service,backup_service}.py`、`infrastructure/runtime.py` | as-built（V03-2.x/8.1-8.3） |
| [execution-log.md](execution-log.md) | 执行日志（append-once/分页/SSE/断线补齐/压力目标） | `infrastructure/execution_log.py`、`interfaces/api/execution_log.py` | as-built（V03-1.6/8.4） |
| [api-matrix-and-retirement.md](api-matrix-and-retirement.md) | API 分类、410 拒启、默认工作区、旧写路径收敛 | `interfaces/api/_retired.py`、`server/v2.py` | as-built（V03-8.6） |

## 3. 0.3 新增的存储与端口（增量）

| 面 | 位置 | 说明 |
|---|---|---|
| 操作记录 | `application/services/operation_service.py` | 每个写入返回 `operation_id`；`GET /operations/{id}(/impact|/undo)` |
| 覆盖台账 | `revision_work` aux 表 + `revision_work_service` | 逐章读取登记与五状态结论；`complete` 只在全书下过结论后为真 |
| 修订任务 | `revision_task_service` + `domain/revision/task.py` | 预算 `call_limit/calls_used/remaining`、修正轮次上限 2、等待/停止/继续 |
| 执行日志 | `infrastructure/execution_log.py` | append-once + 作品级游标分页 + SSE 续传；事件类型见 `domain/execution_log` |
| 入口拒启 | `interfaces/api/_retired.py` | 410 `FEATURE_RETIRED_IN_03` + 三要素；被排除功能不可从 HTTP 启动 |
| 能力目标 | `application/capabilities.py` | §9.6 性能目标（执行日志 page_size、p95 等）被测试读取 |

## 4. 未验与留白（模块级）

- 节拍类型仍在 `domain/story/narrative`（计划 §9.1 留白「节拍类型退出随 V03-8」未做）；起草入口已不要求 Beat，但类型与旧引用未移除（`v03-0-beat-reference-map.md` 记录了 9 个引用点）。
- 前端执行日志面板消费端未接（[execution-log](execution-log.md) §未验）。
- V1 语料写接口仍挂载（0.3 无入口、无自动消费）；是否拒启待裁决。
- 逐章审批流的底层服务（review/audit/rewrite）保留未删，只保证入口不可达。

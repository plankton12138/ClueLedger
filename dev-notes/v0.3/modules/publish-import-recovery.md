# 统一发布、导入预览与备份恢复

> 状态：**as-built**（V03-2.1–2.5、8.1–8.3、8.6，2026-10-19）。证据：[2.2](../notes/v03-2.2-publish-manuscript-change.md)、[2.3](../notes/v03-2.3-import-preview.md)、[8.1](../notes/v03-8.1-recovery-and-legacy-isolation.md)、[8.2](../notes/v03-8.2-backup-and-restore.md)、[8.3](../notes/v03-8.3-production-startup-loop.md)。
> 实际包：`application/services/{publish_manuscript_change,import_service,backup_service,operation_service}.py`、`interfaces/api/*`、`server/{__main__,v2}.py`。

## 1. 统一发布管线（唯一正式写路径）

- 六步：领域命令纯函数 → 证据/摘要核对 → 操作记录 → 原子提交 → 来源/反向链接 → 事件投影。
- 所有正式写入都返回 `operation_id`：`chapters/{id}/save`、`chapters/{id}/restore`、`chapters/import`（8.6 接入）、`imports/previews/confirm`、`drafts/{id}/adopt`、`edit-sessions/{id}/finish`、`operations/{id}/undo`、`revision-tasks/{id}/publish`。
- 证明：`GET /operations/{id}` 能回读本次变化的章（`tests/v2/test_legacy_entry_guard.py`）。

## 2. 导入预览（2.3）

- `POST /imports/previews` 只解析不写入；预览展示**会被写入的实际文本**。
- 同名章默认新增；内容相同必须作者选择（新增副本 / 替换 / 取消），不静默覆盖。
- 整批确认一次提交，失败无半份正文；`preview_hash` 防过期采用。

## 3. 备份与恢复（8.1/8.2）

- `GET /projects/{id}/backup`：预览按 head 构建并回报 `BACKUP_COVERAGE` 覆盖声明；凭据结构性排除。
- `POST /projects/{id}/restore`：差异未确认 409（列章与 `changed_fields`），确认后 `save_full_text` + 账本进同一 Commit；包内对象缺失 `EXPORT_RESTORE_NEW_OBJECT`。
- 恢复是覆盖语义（不做三方合并）；保留后来修改请用撤销。

## 4. 启动恢复与旧任务隔离（8.1）

- `V2Runtime.recover_on_startup()` 是 lifespan 唯一入口：执行中的对话/任务标 `interrupted`/waiting，旧自动队列 `manuscript.analysis`/`style_observer` 启动即分类暂停为 `historical_paused`；**只改状态，不自动重发模型调用**。
- `GET /api/v2/recovery` 提供上次恢复摘要、旧队列快照、任务状态分布与等待清单。

## 5. 生产装配（8.3）

- `assemble_server()`：config 解析 → 静态目录探测 → `.env` → `load_server_config` → 引导档案/未配置判定 → 凭据存储 → `ProviderBook` → `WorkspaceRegistry` → `V2Runtime.sqlite` → `create_app`；`main()` 只剩参数解析与 `uvicorn.run`。
- 冒烟测试用同一段装配走完整回路（创建→导入→保存→跨章修改→撤销→采用→备份恢复）。

## 6. 未验与留白

- **真 uvicorn 进程启动无运维日志证据**（8.3 留白）——冒烟走的是同进程 TestClient。
- 恢复到新作品需要 artifact ID 重映射（0.3 不做）。
- 备份核对以「正文/账本/副本/操作/建议/来源/日志」为准；凭据排除是结构性保证。

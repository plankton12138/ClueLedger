# 正文发布、导入与恢复

正式正文的导入、保存、恢复、候选采用、副本完成、撤销与整体修订发布共用发布管线。这样正文、来源证据、摘要状态与操作记录能够在同一次提交内保持一致，失败时不留部分更新。

## 1. 正式发布

发布器准备领域变化，核对证据与摘要状态，生成操作记录，再原子提交并投影事件。正文写入返回 `operation_id`，可通过 `GET /operations/{id}` 回读相关变化。该约定针对正式正文发布，不表示单独保存账本也一定生成操作记录。

保存会确定性地核对正文依据并标记摘要失效，不自动调用模型生成摘要或账本建议。副本、候选和覆盖台账的工作状态不直接替代正式内容。

实现：`application/services/{publish_manuscript_change,operation_service}.py`、`application/workflows/operation_projection.py`。

## 2. 导入预览

`POST /imports/previews` 只解析并返回实际将写入的文字，不保存正式正文。默认新增章节；重复内容须由作者选择新增副本、替换或取消。确认时检查预览指纹、目标版本与活动副本，整批一次提交，失败不留下部分章节。

旧单章导入 `POST /chapters/import` 同样经发布器返回操作编号。实现：`application/services/import_service.py`、`interfaces/importers/manuscript_text.py`。

## 3. 备份与恢复

`GET /projects/{id}/backup` 以当前正式版本构建包并回报覆盖声明：

| 纳入 | 不纳入 |
|---|---|
| 正文章节与段落清单、故事账本、本书约定、叙事结构、依赖边 | 手工修改副本与快照、覆盖台账、整体任务与暂存、关联链接、操作记录、待确认建议与候选、执行日志、模型连接与凭据 |

这份导出包用于恢复作品内容，不能替代包含过程状态的数据库完整备份。

`POST /projects/{id}/restore` 仅支持同作品恢复。存在差异但未确认时返回 409 并列出差异；确认后正文与账本经统一发布一次提交。包内对象在当前作品不存在时返回 `EXPORT_RESTORE_NEW_OBJECT`，不自动创建另一部作品或重映射 ID。

恢复按包内容覆盖，不做三方合并；需要保护后续独立修改时使用[撤销](editing-and-undo.md)。内容相同可返回无变化结果。

实现与覆盖声明：`application/services/backup_service.py` 的 `BACKUP_COVERAGE`。

## 4. 启动恢复与装配

`server/v2.py` 的 `V2Runtime.recover_on_startup()` 在服务生命周期中统一处理执行中的对话与任务，将其标为中断或等待；旧自动分析和风格观察队列标为 `historical_paused`。恢复只改状态，不自动重新调用模型。

`GET /api/v2/recovery` 返回恢复摘要、旧队列快照、状态分布与等待清单。

`server/__main__.py` 的 `assemble_server()` 负责配置、凭据、模型连接、工作区、数据库与应用装配。未配置模型可正常启动并使用非模型功能。

## 5. 验证边界

现有装配闭环证据使用同进程测试客户端；独立服务进程的运行日志尚未取证。同作品恢复不等于跨作品迁移，也不证明完整数据库回退已经验证。运行命令见[运行说明](../release.md)。

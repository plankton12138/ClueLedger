# 手工修改、候选采用与撤销

作者可随时开启手工修改，在工作副本中跨章保存，完成后统一形成正式版本。候选先预览后采用；撤销生成新的操作记录，并保护操作后的独立修改。

## 工作副本与合并

副本保存不移动正式版本。开始时可声明本轮章节清单，首次保存也会把章节纳入；完成前要求清单内修改已保存，否则返回 `EDIT_SESSION_UNFLUSHED`。

副本保存使用递增保存序号核对并发。正式稿已前进时，按基础稿、副本稿与当前稿进行段落级三方合并：不重叠的修改自动保留，重叠返回 `EDIT_SESSION_MERGE_CONFLICT` 并要求逐处选择。完成通过统一发布器一次发布；提交成功后的重试回读原操作并补齐关闭状态，不重复发布。

`POST/GET /edit-sessions/{id}/snapshots` 冻结文本、选区、保存序号与段落来源；候选“用进副本”通过 `POST /drafts/{id}/apply-to-edit-session` 只修改副本，写入段落标为模型来源。完成后来源区间随操作记录保存。

## 试写与采用

`POST /drafts` 按作者要求生成候选，首章不要求已有正文、大纲或节拍。改写现有章时提示词包含该章原文；候选不是正式稿。

预览只读，采用依据实际预览指纹并核对基础版本。正式采用经统一发布；存在活动副本时按对应保护规则处理，不能静默覆盖副本。

## 撤销与影响

`GET /operations/{id}/undo` 预览撤销，`POST /operations/{id}/undo` 携带预览令牌与冲突选择执行。比较原操作前、操作后和当前内容：

- 当前仍等于操作后内容时可恢复原值。
- 后续非重叠编辑自动保留，重叠由作者逐处选择 `restore` 或 `keep`。
- 预览后版本变化或选择不完整时拒绝写入，正文与关联账本不留下部分恢复。

关联账本与正文可放进同一次撤销提交；后续建议采用通过 `source_operation_id` 记录因果关系。关联采用中新建的条目只报告 `skipped_created`，不自动删除。原操作新建的章节或条目也受删除能力缺失的限制，不能承诺所有操作均可完整撤销。

`GET /projects/{id}/operations/{operation_id}/impact` 从真实版本差异计算变化与后续修改标记，不输出质量分数或逻辑正确性评价。

## 代码位置

`domain/manuscript/{edit_session,merge,drafting}.py`；`application/services/{edit_session_service,drafting_service,undo_service,revision_links}.py`；`application/queries/{chapter_history,impact}.py`。路径除显式列出的完整作品段外，均省略公共 API 与作品前缀。

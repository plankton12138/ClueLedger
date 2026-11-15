# P2 对话入口、回执与撤销

日期：2026-11-02。对应 M2 的前端部分与 M3。

## 1. 交付

- 新增 `frontend/src/state/discussionContext.ts`：按草稿槽位保存本轮焦点；点击「讨论调整」只登记引用并打开对话，不建会话、不发请求。
- `ConversationPanel` 显示「本轮焦点」标签；发送时把焦点冻结进 `context.discussion_focus`，发送成功后清空，失败保留输入与焦点。
- 选段入口改为「讨论此段」；正文选段不再弹出名称／用途表单，也不再直接写入线索。
- 新增 `components/chat/StoryCommitCard.tsx`：在对话回执里展示已保存的改动对象，展开时读取 `/story/changes/{id}` 的操作列表，并提供「撤销这次调整」；撤销走同一逆操作接口。
- `state/story.ts` 增加 `invalidation` 失效序号；详情、本章相关与全书投影在结构提交后一起重读。`state/conversations.ts` 的提交事件不再要求页面停在构思页才刷新。
- 初始化会话时，如果作者已经在新对话槽位输入文字，不再自动切到最近会话，避免加载竞态吞掉未发送文字。

## 2. 操作覆盖

`update_story` 的现有操作集已覆盖创建、改名、修改、删除节点与线索、正文绑定／解绑、用途、关系、提醒暂缓与确认、阶段、故事线、连接、时间与约定；本版没有新增聊天专用写存储。撤销是提交的逆操作，不进入 `update_story`。

探索轮没有写权限：`writable` 仍要求本轮意图允许结构写入；明确指令才提交。提醒暂缓与确认继续使用作者原话和指纹校验。

## 3. 验证

- `frontend/src/state/discussionContext.test.ts`：槽位隔离、准备不发送。
- `frontend/src/state/story.test.ts`：失效序号与撤销。
- `frontend/src/api/story.test.ts`：随章首屏聚合请求与游标冲突。
- `frontend/src/components/story/storyOverview.test.ts`：情节分组与未安排兜底。
- `frontend/e2e/clueledger-workspace.spec.ts`：选段只准备焦点；详情只读；线索关系按需打开。
- `frontend/e2e/clue-reference.spec.ts`：线索详情的讨论调整不写草稿引用。
- `frontend/e2e/chapter-context.spec.ts`：分组、详情、讨论本章与返回。

## 4. 留白

- 焦点不写入服务端草稿上下文；刷新后未发送的焦点不保留，已发送轮次仍由冻结快照恢复。
- 撤销按改动标识执行，界面对「依赖变化导致撤销失败」只展示错误三要素，不提供自动修复。
- 在线模型下的「采用建议」仍依赖既有候选机制，本阶段只用确定性用例验证授权与回执路径。

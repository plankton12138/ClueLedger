# P4 正文随章查看与跨布局导航

日期：2026-11-02。对应 M5。

## 1. 交付

- 新增 `components/workspace/ChapterContext.tsx`：首屏一次 `GET /story/chapter-context`，按 observed / planned / unresolved 分组；切章中止旧请求，游标过期可重读。
- `WritingLayout.tsx` 增加「本章」入口与 `mode=chapter`；辅助区一次只放详情、本章相关或对话中的一种，打开对话显示「返回详情」。
- 正文页允许 `?node=` 与 `?clue=` 单一对象选择；从本章相关、正文标记或构思出处进入同一只读详情。
- 关闭详情回到本章面板；本章面板关闭回正文。URL 保留 `mode` 与对象 ID，前进后退与刷新回到同一位置。
- 正文标记点击进入对象详情；构思出处标记同理，不再只做临时高亮。
- 选中对象时自动收起对话，避免窄屏同时出现正文、详情与对话三列；对话仍可从「讨论调整」重新打开。

## 2. 读取与并发

- 请求键包含作品、方案（当前主方案）、章节路径与结构失效序号；组件用 `AbortController` 丢弃旧响应。
- 结构提交后 `invalidation` 递增，本章面板、详情与全书投影一起重读。
- 章节面板每次只加载一页；`next_cursor` 与 `truncated` 原样展示，不静默遗漏。

## 3. 验证

- `frontend/e2e/chapter-context.spec.ts`：本章出现、计划与失效分组；节点详情；讨论本章；返回本章；切章后失效引用保留。
- `frontend/e2e/clue-source-navigation.spec.ts`：从线索详情定位原文、返回构思面、窄屏定位。
- `frontend/e2e/text-selection.spec.ts`：选段进入讨论焦点；模块选段仍按草稿引用保存；跨阅读区不显示入口。
- `frontend/e2e/aqua-layout.spec.ts`：单栏模式对话占满剩余高度，发送按钮可达。
- `frontend/src/state/navigation.test.ts`：`mode=chapter` 往返与节点／线索互斥。

## 4. 留白

- 顶部主导航仍是正文、构思、线索与模块库四个入口；「本章」属于正文页内的辅助模式，不新增第五个入口。
- 章节面板不显示已采用建议或候选正文；它们只出现在对话回执里。
- 快速切章的并发用例由组件层的 `AbortController` 与失效序号保证，没有单独的浏览器压力用例。

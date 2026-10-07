# modules/ — tau 之外新增功能与模块设计

本目录收录 **Hastings 应用层**（相对上游 tau_agent / tau_ai 内核之外新增部分）的
功能与模块设计文档。内核零改动是本项目的架构红线，因此所有新功能设计都沉淀在这里，
与总体设计 [DESIGN.md](../DESIGN.md)、阶段笔记 `phase-XX-*.md` 互补：

- `DESIGN.md` 回答"整体为什么这么做"；
- `phase-XX-*.md` 回答"这个阶段做了什么、怎么验证的"；
- `modules/*.md` 回答"这个模块的契约是什么"——公开 API、关键决策、约束与演化方向，
  随实现同步更新，是读代码前的模块级地图。

## 收录规则

- 一个模块一篇，命名 `<模块名>.md`；
- 内容只写**契约层**信息：职责、公开接口、关键决策与理由、约束/红线、演化方向；
  实现细节以代码为准，不在此重复；
- 模块行为变更时同步更新对应文档（与"测试先行、原子提交"同属开发流程）。

## 索引

| 文档 | 模块 | 状态 |
|---|---|---|
| [bible-store.md](bible-store.md) | `hastings.bible` — Story Bible 文件制存储层 | P1 已实现 |
| [writing-tools.md](writing-tools.md) | `hastings.tools` — 写作工具集（bible_upsert / bible_query / write_chapter） | P1 已实现 |
| `cli.md` | `hastings.cli` — 命令行 REPL 与 PlainRenderer | P1 计划内（T5） |
| `prompts.md` | `hastings.prompts` — system prompt 分节组装 | P1 计划内（T4） |
| `rag.md` | `hastings.rag` — 语料入库与文风检索 | P4 |
| `server.md` | `hastings.server` — FastAPI WebSocket 桥 | P3 |
| `eval.md` | `hastings.eval` — 评测 harness | P6 |

# dev-notes

设计文档与开发过程笔记（沿用上游 Tau 的 ADR/构建日志习惯）。

## 目录约定

- `DESIGN.md` — 总体设计说明（架构、技术选型、Agent 设计、评测方案、路线图）
- `phase-XX-*.md` — 每个开发阶段（见 DESIGN.md §8 路线图）完成后追加一篇构建笔记，记录：做了什么、为什么、踩了什么坑、如何验证
- `modules/` — tau 内核之外新增功能与模块的设计文档（契约层：职责、公开接口、关键决策、演化方向），索引见 [modules/README.md](modules/README.md)

## 命名规则

`phase-<两位序号>-<短横线主题>.md`，例如 `phase-01-minimal-loop.md`。

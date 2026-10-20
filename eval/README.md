# Hastings 评测归档

> 状态：历史归档。0.3 的评测驱动器与 spec 已随 0.3 代码删除，本目录只保留运行产物与基准原文。
> 0.4 的真实模型取证尚未建立新驱动器；需要时按 0.4 链路重新开始，不要按这里的旧口径执行。

## 目录

```
eval/
├── README.md      # 本文件
├── runs/<日期>/   # 运行产物归档（报告、转录、验收记录）
└── assets/        # 基准源原文（txt 入库；*.epub 版权文本不入库）
```

已删除的内容及原因：

- `driver.py`、`v2/`（六角色/对话驱动器）、`specs/`、`intake/` 依赖 0.3 的
  `hastings.bible`、`hastings.cli`、`hastings.prompts` 等模块，这些模块在 V04-5 的
  旧代码清理中删除，驱动器无法再运行。
- `scripts/bench_v03_baseline.py` 等 0.3 基准脚本同理删除；`scripts/` 只保留与版本
  无关的 `package_submission.py`。

`runs/` 与 `assets/` 保留为历史证据：结论对应的是 0.3 的账本与候选链路，不代表 0.4
的行为。0.4 的真实模型取证口径见 [0.4 开发入口](../dev-notes/v0.4/README.md)。

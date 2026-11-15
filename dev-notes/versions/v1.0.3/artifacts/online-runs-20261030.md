# 1.0.3 在线场景记录

- 日期：2026-10-30
- 模型：`.env` 配置的在线 `deepseek-flash`，非本机模型
- 代码：`1b7fcd7` 加本轮 P6 测试补丁；运行版本 `1.0.1`
- 端口与目录：每个场景使用独立端口和 `data/test-tmp/` 隔离目录
- 原则：每个场景只发起必要请求，不通过反复改提示词追求通过

| 场景 | 命令入口 | 结果 | 关键证据 |
|---|---|---|---|
| O01 空白多线创建 | `story-workflow-online.spec.ts -g 创建表单实际保存` | 通过，约 16 秒 | `online-create-20261030/` |
| O02 已有正文归纳 | `story-workflow-online.spec.ts -g 已有正文归纳` | 通过，约 52 秒 | `online-existing-prose-20261030/` |
| O03 局部修改 | 同上，第二轮真实请求 | 通过；其他节点、阶段与故事线未改 | 同上 |
| O04 真实写作与撤销 | `story-scene-online.spec.ts` | 通过，约 2.1 分钟 | `online-scene-20261030/` |
| O05 暂停、继续、重载恢复 | `story-workflow-online.spec.ts -g 暂停、继续` | 通过，约 1.4 分钟 | `online-pause-20261030/` |
| O06 大结构与预算拆分 | `story-workflow-online.spec.ts -g 大结构超过预算` | 通过，约 16 秒；只装第一组并记录拆分 | `online-budget-20261030/` |
| O07 无效输出拒绝 | `story-workflow-online.spec.ts -g 悬空线索` | 通过，约 9 秒；模型提交悬空引用，服务端返回 `STORY_CLUE_REFERENCE_INVALID`，未落结构 | `online-invalid-20261030/` |

证据目录保存该场景的构思状态、线索账本和对话事件；对话事件包含模型身份、请求分区、工具结果与回执。未保存 API 密钥或作者真实作品。

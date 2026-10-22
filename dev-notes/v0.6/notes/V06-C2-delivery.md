# V06-C2 整体交付

> 实施日期：2026-10-22。计划见[上下文与文件操作开发计划](../context-and-file-operations-plan.md)。
> 本阶段收口：版本升级、模块与运行说明、全量验证与未完成项清单。

## 目标

把 A1–C1 的增量整理成一次可交付的运行版本：版本声明同步、导航指向当前实现、
验证按证据范围记录，未完成与未验证单列。

## 实际改动

| 位置 | 改动 |
|---|---|
| `pyproject.toml`、`uv.lock`、`frontend/package.json`、`desktop/package.json`（及 lock） | 版本 `0.5.0` → `0.6.0`；接口前缀与数据格式标识不变 |
| `dev-notes/v0.6/modules/README.md`（新增） | 0.6 模块导航：新能力指向本版说明，未变化契约指向 0.5/0.4 具体链接 |
| `dev-notes/v0.6/modules/context-and-materials.md`（新增） | 当前实现说明：材料与参考、修改范围、章节索引、写后检查、错误码与不做清单 |
| `dev-notes/v0.6/release.md`（新增） | 运行说明：启动方式、相对 0.5 的行为变化、本次验证范围与未验证项 |
| `dev-notes/README.md`、`dev-notes/v0.6/README.md`、`src/hastings/README.md`、`frontend/README.md`、`AGENTS.md` | 当前实现指向 0.6；0.5 转为历史入口 |

## 版本与兼容

- 包版本 `0.6.0`（后端、前端、桌面同步）。`/api/file-workspaces` 前缀不变。
- `conversation-ui.json` 升到版本 2（草稿参考）；版本 1 文件按无参考读取，不需要迁移。
- 会话日志新增字段（`context`、`purpose`、`index_paths`、`review_paths`）向后可读，
  旧记录按空材料与 chat 处理，不反推旧材料。

## 验证证据（2026-10-22，本次工作区）

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | 432 通过 |
| `uv run ruff check src/hastings tests/workspace` | 通过 |
| `uv run mypy src/hastings` | 42 个源文件通过 |
| `npm test -- --no-file-parallelism`（frontend） | 11 个文件、102 项通过 |
| `npm run build`（frontend，含 e2e 前置构建） | 通过 |
| `npm run test:e2e`（端口 8931，真实后端） | 15 项通过 |

分阶段用例清单见各[阶段记录](README.md)；前后端异常与恢复（断线重连、缺口回读、
停止与中断、跨会话隔离）沿用 0.4/0.5 的既有测试并在本次全量运行中通过。

## 未完成与未验证

- **真实模型语义验收未执行**（计划 §11.2 七类任务）：需要真实模型与人工判读，按约定由
  开发者明确启动。受控 provider 只验证了规则与接线，不能证明模型正确理解「只改」「同步」
  「继续」这类表达。
- **检查阶段回合预算未精确扣减**：与修改阶段共用 `max_turns`。
- **检查质量未验收**：误报、漏报与「回忆当事实」的判断需要真实作品判读。
- 范围详情只读，无逐条撤回追加的界面操作；索引面板不支持逐条编辑（修改即重新生成）。

# Hastings — 基于 Tau 的悬疑小说写作 Agent

> 命名取自阿加莎·克里斯蒂笔下波洛的叙事者黑斯廷斯上尉——"替侦探写下故事的人"。包名 `hastings`。

此项目为基于 tau 的二次开发 agent，详细设计见 [dev-notes/DESIGN.md](dev-notes/DESIGN.md)。
参考原项目目录：`C:\Projects\Agent\tau`。

## 技术选型（版本固定）

| 层 | 技术 | 版本约束 | 备注 |
|---|---|---|---|
| 语言 / 运行时 | Python + uv | Python ≥ 3.12 | uv 管理依赖与 lockfile |
| Agent 内核 | `tau_agent`（原样） | 与上游 tau 同步 | 预计 <5% 改动，每次改动须写 dev-notes |
| Provider 层 | `tau_ai`（原样） | 与上游 tau 同步 | 含 FakeProvider（测试基建） |
| Web 框架 | FastAPI + uvicorn | 最新稳定版 | 原生 async，WebSocket 一等公民 |
| 前端 | React + Vite + TypeScript | React 18, Vite 5, TS 5 | + Zustand（状态）+ Tailwind（样式） |
| 向量库 | Chroma（嵌入式模式） | 最新稳定版 | 零运维、数据即文件 |
| Embedding | BGE-M3（sentence-transformers） | 最新稳定版 | 中英双语，本地运行 |
| Rerank（可选） | bge-reranker-base | 最新稳定版 | Phase 4 后期引入 |
| 测试 | pytest | ≥ 8.0 | + FakeProvider 确定性回放 |
| 静态检查 | ruff + mypy | ruff ≥ 0.5, mypy ≥ 1.10 | ruff line-length=100, quote-style="double" |
| 数据格式 | YAML + Markdown + JSON | — | Bible=YAML, 正文=Markdown, 审计/事件=JSON |

## 开发环境

- **包管理**：一律使用 `uv`（`uv sync`、`uv add`、`uv run`），不用 pip/pipx 直接装。
- **Python 版本**：`pyproject.toml` 中 `requires-python = ">=3.12"`，本地用 uv 管理的解释器。
- **运行命令**：所有 Python 命令经 `uv run` 执行（如 `uv run pytest`、`uv run python -m hastings ...`）。
- **前端**：Node ≥ 18，npm/pnpm 均可，`frontend/` 目录下 `npm run dev` / `npm run build`。
- **环境变量**：API key 等敏感信息放 `.env`（不提交），代码中通过 `pydantic-settings` 或 `os.environ` 读取。

## 开发规范

### Python 代码风格

- 目标 Python 3.12；使用 typed dataclass / Pydantic schema 描述核心消息、事件、工具。
- async 边界显式标注（`async def` / `AsyncIterator`），不在 sync 函数里隐藏异步调用。
- 遵循 tau 上游的 ruff 配置（`E, F, I, UP, B, SIM`），提交前运行 `uv run ruff check .` 和 `uv run ruff format --check .`。
- mypy strict 模式；核心包（`tau_ai`, `tau_agent`, `hastings`）全覆盖。
- 测试用 FakeProvider / FakeTool 做确定性 agent-loop 测试，不依赖真实 LLM。

### 目录结构约定

```
src/
├── tau_ai/          # 上游 provider 层（0 改动）
├── tau_agent/       # 上游 agent 内核（<5% 改动，改动须记录）
├── tau_coding/      # 上游 coding 应用层（保留参考，不参与构建）
└── hastings/        # 本项目应用层（新建）
    ├── __init__.py
    ├── bible/       # Story Bible 数据模型与状态机
    ├── tools/       # 小说写作工具集（bible_query, write_chapter, ...）
    ├── rag/         # RAG pipeline（ingest, search, style_profile）
    ├── server/      # FastAPI WebSocket 服务
    └── eval/        # 评测 harness
frontend/            # React SPA（Vite + TS）
dev-notes/           # 设计文档 + 阶段构建笔记
tests/               # pytest 测试
```

### 开发流程

1. 每完成路线图（DESIGN.md §8）一个阶段，在 `dev-notes/` 追加一篇 `phase-XX-*.md` 构建笔记。
2. 核心行为变更必须先写测试再写实现（参照 tau 上游做法）。
3. 提交信息原子化：一次提交只做一件事（feature / fix / docs / refactor / cleanup）。
4. 对 `tau_agent` / `tau_ai` 的任何改动，必须在 dev-notes 中记录动机，避免无记录漂移。

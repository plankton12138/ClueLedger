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
└── hastings/        # 本项目应用层（新建；模块导航见 src/hastings/README.md）
    ├── bible/       # 账本域：数据模型/状态机/六阶段生命周期/派生统计/审计
    ├── tools/       # 小说写作工具集（硬约束所在层）
    ├── manuscript/  # 正文 SSOT 与保存不变量（段落 diff/revision 归档/write_guard/写作守门 guards）
    ├── rag/         # 语料与文风（style_profile/corpus_store）
    ├── server/      # FastAPI REST + WebSocket 服务与会话装配
    └── eval/        # 评测 harness（占位）
frontend/            # React SPA（Vite + TS）
dev-notes/           # 设计文档 + 阶段构建笔记（导航见 dev-notes/README.md）
tests/               # pytest 测试
```

### 开发流程

1. 每完成路线图（DESIGN.md §8）一个阶段，在 `dev-notes/` 追加一篇 `phase-XX-*.md` 构建笔记。
2. 核心行为变更必须先写测试再写实现（参照 tau 上游做法）。
3. 提交信息原子化：一次提交只做一件事（feature / fix / docs / refactor / cleanup）。
4. 对 `tau_agent` / `tau_ai` 的任何改动，必须在 dev-notes 中记录动机，避免无记录漂移。
5. 构建笔记须单独设「Agent 设计经验」专节（置于交付内容与关键决策之后、评审记录之前），以四透镜提炼本阶段的 agent 设计手法，代码级 debug 细节作辅助记录。四透镜：① 错误消息是喂给模型的 API（字段级定位原样透传；三要素=哪里错、为什么错、怎样算对）；② 硬约束放工具层，prompt 只负责解释世界；③ 泄压阀——区分「违规 / 空结果 / 环境故障」三情形；④ 可观测性是事件契约的一部分。样板：[phase-01-minimal-loop.md §3](dev-notes/phase-01-minimal-loop.md)。
6. 真模型手册走查用例统一放仓库根 `eval/` 目录（如 `eval/intake/`），一个测试点一个文件，README 收判定口径与索引；目录导航与跑法见 [eval/README.md](eval/README.md)——`driver.py` 是统一驱动入口，`specs/` 收对照实验 spec，`runs/<日期>/` 只归档 driver/spec/报告（`*.log` 不入库），runs 内历史 driver 勿当作可执行入口。C 组（经典原著基准，对照实验：harness 臂 vs bare 臂量增益）输入须防训练数据污染：不用原书书名/人名/地名/标志性原句，委托书按真实用户口吻只给核心前提与偏好（人物、手法、线索留给系统发展）；金标准保留原书人名仅供评判者对照；spec 委托书与 intake 用例文件严格同文。
7. 文档查找统一走导航层，不逐个翻文件：dev-notes 侧见 [dev-notes/README.md](dev-notes/README.md)「查什么→去哪」路由表（含阶段一行索引）；模块级见 [src/hastings/README.md](src/hastings/README.md)。读长期文档须区分三类：**设计态**（DESIGN.md 未标 as-built 的章节，保留演进对比价值，勿当现状）、**as-built**（显式标注块，反映实际交付）、**历史记录**（`*-plan.md` 为立项时冻结的只读计划、`dev-notes/artifacts/` 为验收转录）。

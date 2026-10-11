# Phase 0 开发计划：项目骨架（Skeleton）

> 对应 [DESIGN.md](DESIGN.md) §8 路线图 P0 行。
> 状态：**✅ 已收口**，构建笔记见 [phase-00-skeleton.md](phase-00-skeleton.md)。

## 0. 已核实的依赖事实（计划的输入）

开工前对 `src/tau_ai`、`src/tau_agent` 全量 import 扫描的结论：

| 结论 | 证据 | 对 P0 的影响 |
|---|---|---|
| `tau_agent` 第三方依赖仅 **pydantic** | 全包 import 扫描 | pyproject 不需要 anyio/pillow/rich/textual/typer/pygments |
| `tau_ai` 第三方依赖仅 **httpx**（+传递 pydantic） | 全包 import 扫描 | 保留 `httpx[socks]` 与上游一致 |
| 上游测试用 `@pytest.mark.anyio` 跑异步 | [test_agent_harness.py](../../tau/tests/test_agent_harness.py) | dev 依赖加 `anyio`（其 pytest 插件随包提供，默认 asyncio backend） |
| 上游 CI 四道门：pytest → ruff check → ruff format --check → mypy | [tau/.github/workflows/ci.yml](../../tau/.github/workflows/ci.yml) | 照抄结构，去掉 docs job |
| mypy strict 用 `packages=[...]` 白名单 | 上游 pyproject.toml | 白名单写 `tau_ai, tau_agent, hastings`，天然排除 tau_coding |

## 1. 目标与验收标准

**目标**：搭起可构建、可测试、可 CI 的仓库骨架，并用一个 FakeProvider 驱动的 hello-world 证明"tau 内核在新仓库里能跑通完整 agent loop"。

**验收标准**（来自 DESIGN.md，逐字）：

1. `uv run pytest` 绿；
2. hello-world harness 用 FakeProvider 跑通。

**非目标（本阶段明确不做）**：

- 不写任何 Bible/小说工具（P1 的事）；
- 不接入真实 LLM provider；
- 不动 `src/tau_coding`（保留参考、不参与构建/lint/typecheck）；
- 不做前端、不做 RAG。

## 2. 任务分解

### T1 仓库初始化

- `git init`（当前 myagent 无 .git）+ 首次提交作为基线；
- `.gitignore`：`.venv/`、`__pycache__/`、`*.pyc`、`.env`、`uv.lock` 不忽略（要提交）、`stories/`（用户作品）、`.chroma/`（P4 用，先写上）；
- 删除从上游复制来的全部 `__pycache__/` 目录（卫生）；
- `.python-version` 写 `3.12`（CI 的 setup-python 读它）。

**验证**：`git status` 干净；`git log` 有基线提交。

### T2 pyproject.toml

新建项目根 `pyproject.toml`，关键决策：

| 项 | 值 | 理由 |
|---|---|---|
| build-backend | hatchling（同上游） | 保持与上游可比 |
| 发行名 | `hastings-ai`；import 包名 `hastings` | 发行名防撞，包名短 |
| wheel packages | `["src/tau_ai", "src/tau_agent", "src/hastings"]` | tau_coding **移出构建**（DESIGN.md 决策） |
| dependencies | `pydantic>=2.11`、`httpx[socks]>=0.27` | §0 扫描结论，最小化 |
| dev 组 | `pytest>=8.0`、`ruff>=0.5`、`mypy>=1.10`、`anyio>=4.0` | anyio 提供 pytest 异步插件 |
| ruff | 照抄上游：line-length=100、`E,F,I,UP,B,SIM`、双引号；**加 `extend-exclude = ["src/tau_coding"]`** | tau_coding 依赖未装的 textual 等，lint 排除 |
| mypy | strict=true，`packages = ["tau_ai", "tau_agent", "hastings"]` | 白名单制，tau_coding 不进类型检查 |
| pytest | `testpaths = ["tests"]` | 新建空 tests/ |

**验证**：`uv sync --dev` 成功生成 `uv.lock`；`uv pip list` 里**没有** textual/rich/pillow/typer（依赖最小化的直接证据）。

### T3 `src/hastings/` 包骨架

- `src/hastings/__init__.py`：`__version__ = "0.1.0"`；
- 按 AGENTS.md 目录约定建**空子包占位**（各含一个 `__init__.py`）：`bible/`、`tools/`、`rag/`、`server/`、`eval/`。占位不写实现，仅声明结构。

**验证**：`uv run python -c "import hastings; print(hastings.__version__)"`。

### T4 hello-world harness（本阶段的核心交付）

两个文件：

1. `tests/test_hello_harness.py` — 确定性测试，学 tau 的 `test_agent_harness.py` 写法：
   - 定义一个最小 `AgentTool`（如 `story_ping`：收到 arguments 原样返回，`AgentToolResult` 带文本）；
   - `FakeProvider` 预录一段事件流：assistant 发起一次 `story_ping` 工具调用 → 收到工具结果 → 输出最终文本；
   - 断言：**事件序列**（`AgentStart → TurnStart → MessageStart/Update/End → ToolExecutionStart/End → TurnEnd → AgentEnd`）、消息历史含 user/assistant/tool_result 三类、FakeProvider 收到的 tools 列表含 `story_ping`；
   - 标 `@pytest.mark.anyio`。
2. `examples/hello.py` — 可独立运行的同款演示（`uv run python examples/hello.py` 打印事件流），供演示和面试展示。

**验收口径**：不是"跑起来不报错"，而是**断言事件契约**——这是 P0 唯一有学习含量的部分，必须按 tau 测试范式写。

### T5 CI

`.github/workflows/ci.yml`，照上游 ci.yml 精简：

- 触发：push/PR to main；
- 单 job：checkout → setup-python（读 `.python-version`）→ setup-uv（enable-cache）→ `uv sync --dev --locked` → `uv run pytest` → `uv run ruff check .` → `uv run ruff format --check .` → `uv run mypy`；
- 不要 docs job（无 Hugo 站）。

**说明**：仓库尚未推送 GitHub 时 CI 文件只是"就绪态"，推送后自动生效；本地以同样的四道命令为准。

### T6 本地质量门全量跑通

依次执行并全部通过：`uv run pytest`、`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`。

### T7 上游零改动验证（架构红线）

P0 不允许改动 `tau_ai`/`tau_agent` 的任何一行。用目录级 diff 证明：

```powershell
git diff --no-index --stat C:\Projects\Agent\tau\src\tau_agent src\tau_agent   # 预期：仅 __pycache__ 差异（已删则为空）
git diff --no-index --stat C:\Projects\Agent\tau\src\tau_ai src\tau_ai
```

结果截图/输出记入构建笔记。此检查在**每个阶段验收时都要重跑**（后续阶段允许 <5% 改动但须记账）。

### T8 构建笔记

写 `dev-notes/phase-00-skeleton.md`：做了什么、关键决策（依赖最小化证据、tau_coding 排除方式）、坑、验证输出、评审结论（见 §3 R5）。

## 3. 评审方案

分四层，P0 期间全部执行一遍——评审机制本身也是简历素材（"给项目建立了质量门禁"）。

### R1 自动化门禁（每次提交前）

四道本地命令即 CI 四道门，任一不过不提交：

```powershell
uv run ruff check .; uv run ruff format --check .; uv run mypy; uv run pytest
```

### R2 架构红线评审（T7，阶段验收时）

- `tau_ai`/`tau_agent` 与上游 `C:\Projects\Agent\tau` 目录 diff 为空；
- `src/tau_coding` 未出现在 wheel packages / ruff / mypy / pytest 任何一条链路中（检查 pyproject 三处配置）。

### R3 代码评审（P0 全量 diff 完成后）

用 TRAE-code-review skill 对 P0 的全部变更（pyproject、hastings 骨架、测试、CI）做一次评审，重点关注清单：

1. hello-world 测试是否**断言了事件序列**而非仅断言"无异常"；
2. pyproject 依赖是否与 §0 扫描结论一致（不多不少）；
3. `examples/hello.py` 与测试是否重复到需要抽公共 fixture（允许重复——示例要可独立阅读）；
4. CI 是否用了 `--locked`（保证 lockfile 权威）。

评审发现的问题分级：blocker 必须修，建议记入构建笔记"已知留白"。

### R4 检查点评审（两个 checkpoint，执行中）

- **CP1（T2 完成后）**：对照 §2-T2 表格逐项核对 pyproject；跑 `uv pip list` 确认依赖最小化。不通过则不开始 T3。
- **CP2（T4 完成后）**：人工读一遍测试断言，确认覆盖"事件序列 + 消息历史 + 工具透传"三点（对照 tau `test_agent_harness.py` 的断言密度）。

### R5 阶段验收评审（全部任务完成后）

对照 DoD 清单（§4）逐条打勾，逐条留证据（命令输出）；评审结论（通过/带留白通过）写入 T8 构建笔记。**R5 通过 = P0 正式关闭**，才允许进入 P1。

## 4. DoD（Definition of Done）清单

- [ ] `git log` 有基线提交，工作树干净
- [ ] `uv sync --dev` 成功，`uv.lock` 已提交
- [ ] `uv pip list` 无 textual/rich/pillow/typer（依赖最小化）
- [ ] `import hastings` 成功，五个子包占位存在
- [ ] `tests/test_hello_harness.py` 断言事件序列，FakeProvider 回放通过
- [ ] `uv run python examples/hello.py` 打印完整事件流
- [ ] 四道质量门全绿（ruff check / format / mypy strict / pytest）
- [ ] `.github/workflows/ci.yml` 存在且语法有效
- [ ] T7 上游 diff 为空（输出存档）
- [ ] R3 代码评审完成，blocker 清零
- [ ] `dev-notes/phase-00-skeleton.md` 构建笔记发布

## 5. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| mypy strict 对 hastings 骨架报错（空包无类型标注一般无碍，但 `__init__.py` 需 `__all__` 或显式标注） | 低 | 骨架代码全部带类型标注；报错即修，不降级 strict |
| `uv sync` 在 Windows 上 httpx[socks] 拉 socksio 失败 | 低 | 失败则去掉 `[socks]` extra 并在构建笔记记录（不影响 FakeProvider 路径） |
| 上游复制文件带 `__pycache__` 干扰 diff | 中 | T1 先删干净再做 T7 |
| hello-world 事件序列断言写错（对 tau 事件理解偏差） | 中 | 先读 [events.py](../src/tau_agent/events.py) 与 [loop.py](../src/tau_agent/loop.py) 的事件发出顺序，再照 `test_agent_harness.py` 的断言样式写 |

## 6. 执行顺序

```text
T1 仓库初始化 → T2 pyproject（CP1 评审）→ T3 包骨架 → T4 hello-world（CP2 评审）
→ T5 CI → T6 质量门 → T7 红线验证 → R3 代码评审 → T8 构建笔记 + R5 验收
```

# Phase 1 开发计划：最小闭环（CLI 版写作 Agent）

> 对应 [DESIGN.md](DESIGN.md) §8 路线图 **P1 最小闭环** 行。
> 状态：**✅ 已收口**，构建笔记见 [phase-01-minimal-loop.md](phase-01-minimal-loop.md)。

## 0. 已核实的依赖事实（计划的输入）

开工前对 `src/tau_ai`、`src/tau_agent`、`src/tau_coding`（只读参考）的核查结论：

| 结论 | 证据 | 对 P1 的影响 |
|---|---|---|
| `OpenAICompatibleProvider(config)` 构造即可用；`openai_compatible_config_from_env()` 从 `OPENAI_API_KEY` / `OPENAI_BASE_URL` 读配置 | [env.py](../src/tau_ai/env.py#L91-L119)、[openai_compatible.py](../src/tau_ai/openai_compatible.py#L72-L92) | provider 接入**零新依赖**；hastings 只做"读 env → 构造 config → 构造 provider"三行胶水 |
| 工具 `execute_fn` 抛出的任何异常都会被 loop 捕获、转为 `is_error=True` 的工具结果喂回模型 | [loop.py](../src/tau_agent/loop.py#L355-L361) | hastings 定义自己的 `ToolInputError(ValueError)`，**不需要** import tau_coding 的同名类 |
| `AgentTool` 是 frozen dataclass：name/label/description/parameters(JSON Schema)/execute_fn/prompt_guidelines | [tools.py](../src/tau_agent/tools.py#L76-L105) | hastings 工具直接构造此 dataclass（照 `create_read_tool_definition` 的工厂闭包模式，但**不 import** tau_coding） |
| `harness.prompt(str) -> AsyncIterator[AgentEvent]`；`subscribe()` 挂监听；`cancel()` 中断 | [harness.py](../src/tau_agent/harness.py#L147-L205) | CLI REPL = `async for event in harness.prompt(user_input)`，事件契约与 P0 测试一致 |
| tau_coding 的 plain 渲染器依赖 typer（**不在**本项目依赖中） | [plain.py](../src/tau_coding/rendering/plain.py) | 只复用其思路（订阅事件 → 流式打印），用内置 `print()` 自写 ~60 行渲染器 |
| Bible 数据格式 = YAML（DESIGN.md §2.2 已决策），但 `pyproject.toml` 尚无 YAML 库 | pyproject.toml | **依赖决策 D1**（见下） |
| pydantic 已是运行时依赖 | pyproject.toml | Bible 实体用 Pydantic model 做 schema 校验，YAML 仅作序列化层 |
| `.env` 已在 `.gitignore`；`stories/` 已在 `.gitignore` | .gitignore（P0 T1） | 用户作品与密钥天然不入库；需补一份 `.env.example` 模板 |

### 依赖决策（本阶段新增，需评审确认）

| 编号 | 包 | 组 | 理由 | 备选 |
|---|---|---|---|---|
| D1 | `pyyaml>=6.0` | 运行时 | Bible YAML 读写的事实标准；纯 Python fallback 可无 C 扩展运行 | ruamel.yaml（保留注释/格式，P1 不需要） |
| D2 | `types-PyYAML` | dev | mypy strict 下 PyYAML 的完整类型桩 | 无（不加则 yaml 调用全是 Any） |
| D3 | `python-dotenv>=1.0` | 运行时 | CLI 入口 `load_dotenv()` 一行补齐 `.env` → `os.environ`，与 `openai_compatible_config_from_env()` 无缝衔接 | 手写 15 行 .env 解析（能省一个依赖，但重复造轮子且要自测边界） |

除 D1–D3 外不引入任何新依赖；CLI 不用 typer/rich（刻意保持最小，渲染器自写）。

## 1. 目标与验收标准

**目标**：做出第一个**真实可用的闭环**——命令行里与一个接真实 LLM 的写作 Agent 对话，它能通过工具把角色卡写进 Story Bible、把章节正文写进工作区。

**验收标准**（DESIGN.md §8 原文）：

> 终端里完成"设定 2 个角色 → 写 1 章"

展开为可检查的四条：

1. `uv run python -m hastings <story-slug>` 启动 REPL，接真实 OpenAI 兼容端点（读 `.env`）；
2. 对话中 Agent 调用 `bible_upsert` 落盘 2 张角色卡（`stories/<slug>/bible/characters/*.yaml`）；
3. 对话中 Agent 调用 `write_chapter` 落盘 1 章正文（`stories/<slug>/chapters/ch01.md`）；
4. 工具调用过程在终端可见（流式文本 + 工具调用摘要行）。

**非目标（本阶段明确不做）**：

- 不做五账本全量与状态机校验（线索/伏笔/手法/时间线是 **P2**；P1 的 Bible 仅支持 character 一种实体）；
- 不做 `timeline_check` / `method_lock` / `foreshadow_audit`（P2/P5）；
- 不做会话 JSONL 持久化与恢复（P3 服务端再接线）；
- 不做 Web 服务、前端、RAG；
- CLI 不做花哨 TUI（无颜色框架、无快捷键），流式纯文本即可。

## 2. 任务分解

### T1 依赖与环境模板

- `uv add pyyaml python-dotenv`；`uv add --dev types-pyyaml`；
- 新建 `.env.example`：`OPENAI_API_KEY=`、`OPENAI_BASE_URL=`（注释举例 DeepSeek/GLM 兼容端点）、`HASTINGS_MODEL=`；
- `uv.lock` 更新随本任务提交。

**验证**：`uv sync --dev --locked` 通过；`uv pip list` 仍无 textual/rich/typer/pillow。

### T2 Bible 存储层（`hastings/bible/`）

两个模块，纯逻辑、可单测：

1. `hastings/bible/models.py` — Pydantic schema：
   - `Character`：`name: str`（必填）+ `identity / motivation / secret / notes: str = ""` + `relationships: list[str] = []`。
   - 字段集合是 DESIGN.md §2.2 角色行的**最小子集**；弧光阶段等状态机字段 P2 再加（模型留扩展空间但不提前实现）。
2. `hastings/bible/store.py` — 文件制工作区读写：
   - `StoryWorkspace(root: Path, slug: str)`：解析 `stories/<slug>/` 下 `bible/characters/`、`chapters/` 路径，`ensure_dirs()` 惰性建目录；
   - `save_character(ws, char) -> Path` / `load_character(ws, name)` / `list_characters(ws)`：YAML 序列化/反序列化（`yaml.safe_load` / `safe_dump`，`allow_unicode=True`，`sort_keys=False`）；
   - **安全红线**：实体名经 slug 校验（`^[A-Za-z0-9\u4e00-\u9fff][\w\u4e00-\u9fff -]{0,63}$`），拒绝路径穿越（`../`、绝对路径）。

**验证（测试先行）**：`tests/test_bible_store.py` —— 往返一致（save→load 相等）、非法名拒绝、空库 list 返回 `[]`、YAML 中文不转义。

### T3 三个写作工具（`hastings/tools/`）

`hastings/tools/errors.py` 定义 `ToolInputError(ValueError)`；两个工厂模块（闭包绑定 workspace，照 `create_read_tool_definition` 模式）。

三个工具各司其职，共同落实"工具是账本的守门人"设计（DESIGN.md §3.3）：

- `bible_upsert` —— **唯一写入口**：角色设定只能经它校验后落盘，模型无法绕过工具直接写文件；校验失败时把字段级错误喂回模型，让它自我修正而非盲目重试；
- `bible_query` —— **只读检索口**：动笔前核对既有设定的唯一途径，防止正文与 Bible 矛盾；查无结果返回引导性提示而非报错，避免模型陷入重试死循环；
- `write_chapter` —— **正文出口**：章节号规范（chNN 零填充）、标题生成、覆盖保护都在这里强制执行——chapters/ 目录的格式一致性靠工具保证，不靠 prompt 自觉。

| 工具 | 参数（JSON Schema） | 行为 | 关键约束 |
|---|---|---|---|
| `bible_upsert` | `entity_type: "character"`、`data: object` | Pydantic 校验 → 落盘 YAML → 返回文件路径 | `entity_type` 非 character 返回"P2 才支持"的明确错误；data 校验失败抛 `ToolInputError`（错误消息带字段级原因，方便模型自我修正） |
| `bible_query` | `entity_type: "character"`、`name?: string` | 带 name 返回该角色 YAML 全文；不带返回全部角色摘要列表 | 查无此角色时返回提示而非异常（避免模型死循环重试） |
| `write_chapter` | `chapter: int ≥1`、`title?: string`、`content: string`、`force?: bool` | 写 `chapters/chNN.md`（两位数零填充），含 `# 第N章 标题` 头 | 文件已存在且 `force!=true` 拒绝；`content` 为空拒绝 |

每个工具带 `description`、`prompt_guidelines`（写进 system prompt 的使用规范，如"角色信息必须先经 bible_upsert 落盘再开始写正文"）、`render_call`（终端单行摘要）。`AgentToolResult.details` 放结构化数据（路径、实体计数），供未来审计/前端用。

**验证（测试先行）**：`tests/test_tools_bible.py` / `tests/test_tools_chapter.py` —— 每个工具：正常路径 + 每条约束的非法输入断言错误消息；`tmp_path` 隔离文件系统。

### T4 System prompt 组装（`hastings/prompts.py`）

DESIGN.md §3.2 的 P1 最小版：分节常量 + `build_system_prompt(tools) -> str`：

1. **角色节**：资深悬疑小说主编 + fair-play 守则一句话版；
2. **工作流程节**：先 Bible 后正文；角色未定不开写；工具是触碰工作区的唯一方式；
3. **工具规范节**：由各 `AgentTool.prompt_guidelines` 自动拼接。

**验证**：`tests/test_prompts.py` —— 输出含三节标题；每个工具的 guidelines 均出现；无工具时仅前两节。

### T5 CLI 与渲染器（`hastings/cli.py` + `hastings/__main__.py`）

- `hastings/cli.py`：
  - `PlainRenderer`：订阅 `AgentEvent`——`TextDeltaEvent` 流式 `print(end="", flush=True)`；`ToolExecutionStart` 打印 `[tool] name 摘要`；`ToolExecutionEnd` 且 `is_error` 时打印 `[tool error] 消息`；assistant 消息结束换行；
  - `async run_repl(story, model)`：构造 workspace → tools → provider → harness；循环 `input("> ")` → `async for event in harness.prompt(line)` 喂渲染器；`/quit` 退出、`/characters` 快捷查看 Bible（调 store 直读，不走 agent）；
  - 启动检查：缺 `OPENAI_API_KEY` / `HASTINGS_MODEL` 时打印 `.env.example` 对照的中文指引并非零退出。
- `hastings/__main__.py`：`argparse`（`slug` 位置参数 + `--model` 覆盖 env）→ `load_dotenv()` → `asyncio.run(run_repl(...))`；Ctrl-C/Ctrl-D 优雅退出。

**验证**：`tests/test_renderer.py` —— 用 P0 的 FakeProvider 事件流喂 `PlainRenderer`，捕获 stdout 断言：文本增量按序出现、工具行格式正确、错误工具调用有 error 行。CLI 交互本身不自动化测（留 T7 人工验收）。

### T6 Loop 集成测试（本阶段的核心交付，对标 P0 hello-harness）

`tests/test_writing_loop.py` —— FakeProvider 预录完整剧本：

```text
user: "创建两个角色并写第一章"
  → assistant 调 bible_upsert(侦探)        → tool result
  → assistant 调 bible_upsert(嫌疑人)      → tool result
  → assistant 调 bible_query() 确认        → tool result
  → assistant 调 write_chapter(ch01)       → tool result
  → assistant 输出总结文本
```

断言三层（沿用 tau 测试范式）：① 事件序列契约；② `tmp_path` 下**真实落盘** 2 张 YAML + 1 个 md 且内容可解析；③ 消息历史含 user/assistant/tool_result 三类。

### T7 质量门 + 红线 + 真实 LLM 人工验收

1. 四道门全绿：`uv run ruff check .`、`uv run ruff format --check .`、`uv run mypy`、`uv run pytest`；
2. T7 红线重跑：`tau_ai`/`tau_agent` 与上游 diff 为空（输出存档进构建笔记）；
3. **人工验收**（验收标准 1–4）：用真实端点跑 `uv run python -m hastings demo-story`，完成"设定 2 角色 → 写 1 章"，完整 transcript 存 `dev-notes/artifacts/p1-acceptance-transcript.md`；
4. 若执行时暂无可用 API key：以 T6 集成测试 + FakeProvider 演示作为替代证据，真实端点验收记"已知留白"并明确补验条件（需用户本人 key，无法代跑）。

### T8 构建笔记

写 `dev-notes/phase-01-minimal-loop.md`：交付物、D1–D3 依赖决策落锤记录、事件流渲染的取舍、坑（预计：mypy 对 `yaml.safe_load` 返回 Any 的处理、Windows 终端中文流式输出）、四道门与红线输出、R3/R5 结论、验收 transcript 链接。

## 3. 评审方案

沿用 P0 的四层机制，检查点与关注清单按本阶段内容重设。

### R1 自动化门禁（每次提交前）

```powershell
uv run ruff check .; uv run ruff format --check .; uv run mypy; uv run pytest
```

### R2 架构红线评审（阶段验收时）

- `tau_ai`/`tau_agent` 与上游 `C:\Projects\Agent\tau` 目录 diff 为空；
- hastings 代码 **零 import** `tau_coding`（`grep -r "tau_coding" src/hastings tests` 为空）；
- 新增依赖仅限 D1–D3（diff pyproject 确认）。

### R3 代码评审（P1 全量 diff 完成后）

TRAE-code-review skill 双验证者评审，重点关注清单：

1. T6 集成测试是否**断言事件序列 + 落盘文件**双契约，而非仅"无异常"；
2. 工具 JSON Schema 与 Pydantic 模型是否**单一事实源**（允许两处声明但字段必须一一对应，评审逐字段核对）；
3. 路径穿越防护是否覆盖全部写盘入口（store 层 + write_chapter）；
4. `PlainRenderer` 是否吞事件（未知事件类型静默忽略 vs 显式 passthrough——应显式）；
5. 依赖是否与 D1–D3 决策一致（不多不少）。

问题分级沿用：blocker 必修，建议记入构建笔记"已知留白"。

### R4 检查点评审（执行中，两个 checkpoint）

- **CP1（T3 完成后）**：对照 §2-T3 表格逐工具核对参数 Schema、错误路径、`prompt_guidelines` 文案；跑一遍工具单测确认全覆盖约束行。不过则不进 T4。
- **CP2（T6 完成后）**：人工读集成测试断言密度（对标上游 `test_agent_harness.py` 与本仓库 `test_hello_harness.py`），确认"事件 + 文件 + 消息历史"三层都有；用 FakeProvider 干跑一遍 CLI 渲染路径（不起真实服务）。

### R5 阶段验收评审（全部任务完成后）

对照 DoD（§4）逐条打勾留证据；结论写入 T8 构建笔记。**R5 通过 = P1 正式关闭**，才允许进入 P2。

## 4. DoD（Definition of Done）清单

- [ ] `uv sync --dev --locked` 通过；新依赖仅 pyyaml / python-dotenv / types-PyYAML
- [ ] `uv pip list` 仍无 textual/rich/typer/pillow（依赖最小化保持）
- [ ] `.env.example` 存在且不含任何真实 key
- [ ] `Character` 模型 + store 层单测全绿（往返、非法名、空库）
- [ ] 三个工具单测覆盖每条约束；非法输入错误消息可读（模型能据此自我修正）
- [ ] system prompt 组装测试通过（三节 + 工具 guidelines 拼接）
- [ ] T6 FakeProvider 集成测试断言事件序列 + 落盘文件 + 消息历史三层契约
- [ ] 渲染器测试通过；未知事件显式忽略
- [ ] 四道质量门全绿；mypy strict 无新增豁免
- [ ] T7 红线：`tau_ai`/`tau_agent` diff 为空；hastings 零 import tau_coding
- [ ] 人工验收：真实端点完成"2 角色 → 1 章"，transcript 存档（或按 T7-4 记留白）
- [ ] R3 评审完成，blocker 清零
- [ ] `dev-notes/phase-01-minimal-loop.md` 构建笔记发布

## 5. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| 真实 LLM 不按预期调工具（不 upsert 直接开写） | 中 | system prompt 工作流程节 + 工具 guidelines 双保险；T6 剧本即"标准答案"；人工验收如跑偏，调 prompt 不调架构 |
| 验收依赖用户本人的 API key，agent 无法代跑真实端点 | 高 | 计划内明示：真实验收需用户在场提供 `.env`；否则按 T7-4 留白处理，FakeProvider 证据先行 |
| `yaml.safe_load` 返回 `Any`，mypy strict 报错 | 中 | store 层收敛全部 yaml 调用，返回类型经 Pydantic `model_validate` 收窄；禁止业务代码直接碰 yaml |
| Windows 终端中文流式输出乱码/缓冲 | 低 | `print(..., flush=True)`；若乱码记构建笔记并指引 `PYTHONIOENCODING=utf-8`，不为此引依赖 |
| `write_chapter` 的 content 很大时 JSON Schema 参数过长被模型截断 | 低 | P1 接受（单章量级可控）；P3 再评估"大纲先行 + 分段写"工具演化，记构建笔记 |
| 范围失控（顺手做了状态机/第四工具） | 中 | 非目标清单即红线；R3 清单第 5 条专查范围爬行 |

## 6. 执行顺序

```text
T1 依赖与 .env.example
→ T2 存储层（测试先行）
→ T3 三个工具（测试先行，CP1 评审）
→ T4 system prompt
→ T5 CLI + 渲染器
→ T6 loop 集成测试（CP2 评审）
→ T7 质量门 + 红线 + 人工验收（需用户 API key 在场）
→ R3 代码评审
→ T8 构建笔记 + R5 验收
```

提交策略沿用 P0：原子提交（chore(deps) / feat(bible) / feat(tools) / feat(cli) / test / docs 分开），**每笔提交前先经用户批准**。

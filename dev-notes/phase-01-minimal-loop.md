# Phase 1 构建笔记：最小闭环（CLI 写作 Agent）

> 对应 [phase-01-minimal-loop-plan.md](phase-01-minimal-loop-plan.md)，2026-10-07 完成并关闭。
> 结论：**R3 评审 blocker 清零，R5 验收通过，P1 正式关闭，可进入 P2。**

## 1. 交付内容

| 交付 | 提交 | 说明 |
|---|---|---|
| P1 计划 | `7fee1a0` | 任务分解 T1–T8 + 评审方案 R1–R5 |
| T1 依赖 | `c385022` | pyyaml / python-dotenv / types-PyYAML + `.env.example` |
| T2 存储层 | `8fcc79a` | `Character` 模型 + `StoryWorkspace` YAML 读写（实体名 slug 校验防穿越） |
| T3 工具 | `b7384a2` | `bible_upsert` / `bible_query` / `write_chapter`（测试先行，字段级错误回喂） |
| T4 prompt | `c400388` | 角色/工作流程/工具规范三节，工具 guidelines 单一事实源 |
| T5 CLI | `a0070ca` | REPL + `PlainRenderer`（流式文本 + 工具摘要行，未知事件显式上报） |
| T6 集成测试 | `339d53c` | FakeProvider 全剧本：事件序列 + 落盘文件 + 消息历史三层断言 |
| R3 修复 | `d08aeb8` `48215cf` `9e41ef8` | 空端点归一化 / slug+保留名防护 / REPL 直读兜底 |

## 2. 关键决策

### 2.1 依赖决策 D1–D3 落锤

按计划新增且仅新增三个：`pyyaml`（Bible 序列化，D1）、`types-PyYAML`（mypy strict 类型桩，D2）、`python-dotenv`（`.env` → environ 一行衔接 tau 的 env 读取，D3）。`uv pip list` 共 30 包，**仍无 textual/rich/typer/pillow**；CLI 渲染用内置 `print` 自写 ~60 行，未引入任何 TUI 框架。

### 2.2 工具是账本的守门人（DESIGN.md §3.3 首次落地）

- `bible_upsert` 是角色设定的**唯一写入口**：Pydantic 校验失败时把 `loc: msg` 拼成错误消息抛 `ToolInputError`，loop 转为 `is_error` 工具结果喂回模型自我修正；
- `write_chapter` 的章节号（int ≥1）、标题生成、覆盖保护（force）全部在工具层强制，不靠 prompt 自觉；
- 真实端点验收印证了这条设计：模型首次把 `relationships` 写成非列表，被工具拒绝后凭字段级错误消息一次修正成功（见 [验收 transcript](artifacts/p1-acceptance-transcript.md)）。

### 2.3 yaml 调用收敛 + 事件契约显式性

- 全部 `yaml.safe_load/dump` 收敛在 `store.py`，返回值以 `raw: object` 收窄后经 `model_validate` 进 Pydantic，mypy strict 零豁免（计划 §5 预判的坑未发生）；
- `PlainRenderer` 对未知事件**不静默吞**：记入 `unhandled_event_types`，REPL 退出时打印计数——事件契约演进时的调试锚点。

## 3. R3 代码评审记录（TRAE-code-review 双验证者）

计划 §3-R3 五项重点清单全部通过：① 集成测试三层契约 ② Schema↔Pydantic 字段一一对应 ③ 写盘入口穿越防护全覆盖 ④ 未知事件显式上报 ⑤ 依赖与 D1–D3 一致。

双验证者并行复核确认 4 个问题（2/2 一致，无新增发现）：

| # | 级别 | 问题 | 修复 |
|---|---|---|---|
| 1 | blocker | `.env.example` 留空的 `OPENAI_BASE_URL=` 经 load_dotenv 写入空串，tau 侧 `environ.get(var, DEFAULT)` 空串覆盖默认端点，运行报模糊错误；`env_error` 漏检 | `normalize_base_url()`：空值从 environ 移除、有值去首尾空白（`d08aeb8`） |
| 2 | 建议 | `StoryWorkspace.slug` 未校验，CLI argv 直拼路径 | `__post_init__` 调 `validate_slug`，与实体名同等规则（`48215cf`） |
| 3 | 建议 | `_NAME_PATTERN` 放行 CON/NUL 等 Windows 保留设备名，写盘 OSError 致模型重试循环 | 校验拒绝保留名（大小写不敏感），错误消息说明原因（`48215cf`） |
| 4 | 建议 | `/characters` 直读路径对损坏 YAML 无兜底，ValidationError 直接甩 traceback | 捕获 `ValidationError/yaml.YAMLError/OSError` 打印中文提示（`9e41ef8`） |

#4 做了损坏 YAML 手动复现验证：REPL 打印「读取 Story Bible 失败 + 检查路径」后继续可用。

## 4. 踩过的坑

1. **`__pycache__` 噪音再现**：`git diff --no-index` 对比上游时 36 个 .pyc 二进制差异全是编译时间戳。沿用 P0 结论：红线取证用**逐 .py 文件 SHA-256 对比**，不比目录。
2. **python-dotenv 空值语义**：`KEY=` 留空会写入空串而非跳过，叠加 tau `environ.get(var, DEFAULT)` 的「存在即取值」语义，模板里留空的"可选"变量反而破坏默认值——R3 #1 的根因，已用测试钉死（`test_entry.py`）。
3. **pytest.raises 的 match 是大小写敏感正则**：错误消息 "Reserved…" 配 `match="reserved"` 五连挂，改 `(?i)reserved`。
4. **Windows 终端管道编码**：REPL 自动化喂入时需 `PYTHONIOENCODING=utf-8`，否则中文乱码；REPL 内 `print(..., flush=True)` 流式输出本身无缓冲问题（计划 §5 风险未触发）。

## 5. 验证输出存档

四道质量门（R3 修复后复跑，全绿）：

```text
uv run ruff check .        → All checks passed!
uv run ruff format --check → 78 files already formatted
uv run mypy                → Success: no issues found in 50 source files（hastings 零豁免）
uv run pytest              → 71 passed（P0 基线 1 + 本阶段新增 70）
```

T7 红线（逐 .py SHA-256 对比上游 `C:\Projects\Agent\tau`）：

```text
tau_ai / tau_agent : 全部 .py 逐字节一致，差异 0 个
hastings           : tau_coding 零 import（grep 仅 2 处 docstring 提及设计出处）
```

真实端点人工验收（DeepSeek `deepseek-v4-pro`，2026-10-07）：

- 命令：`uv run python -m hastings demo-story`，REPL 内完成「密室案 2 角色 → 1 章」；
- 验收标准 4/4 通过，产物 `stories/demo-story/`（2 张角色 YAML + `ch01.md`《门从里面锁着》1430 字）；
- 完整 transcript 与标准对照见 [artifacts/p1-acceptance-transcript.md](artifacts/p1-acceptance-transcript.md)。

## 6. DoD 对照（计划 §4）

- [x] `uv sync --dev --locked` 通过；新依赖仅 pyyaml / python-dotenv / types-PyYAML
- [x] `uv pip list` 30 包，无 textual/rich/typer/pillow
- [x] `.env.example` 存在且不含真实 key（真实 `.env` 已被 gitignore 覆盖）
- [x] Character 模型 + store 层单测全绿（往返、非法名、空库、排序）
- [x] 三个工具单测覆盖每条约束；错误消息带字段级原因
- [x] system prompt 三节组装测试通过
- [x] T6 集成测试断言事件序列 + 落盘文件 + 消息历史三层契约
- [x] 渲染器测试通过；未知事件显式上报
- [x] 四道质量门全绿；mypy strict 无新增豁免
- [x] T7 红线：tau_ai/tau_agent 与上游 .py 逐字节一致；hastings 零 import tau_coding
- [x] 人工验收：真实端点 2 角色 → 1 章，transcript 存档
- [x] R3 评审完成，blocker 清零（4 问题全修，3 笔 fix 提交）
- [x] 本构建笔记发布

## 7. 已知留白（转入后续阶段）

- `write_chapter` 的 content 大时可能被模型参数截断——P1 接受（单章量级可控），P3 评估「大纲先行 + 分段写」工具演化；
- `entity_type` 仅 character，clue/foreshadow/method/timeline 四账本与状态机属 P2；
- 会话 JSONL 持久化与恢复未接（P3 服务端接线）；
- 角色弧光阶段等状态机字段未进 `Character` 模型（P2 扩展）。

# 阶段 2：对话改稿评测集与工作台第一版 · 实施计划

日期：2026-11-15。状态：**T0–T6 完成；待作者在线冒烟**。依据：[评测体系重建设计](design.md) 第 2—4、6—8 节与第 7.0—7.2 节（方案 C）。

> **For agentic workers:** 按任务顺序 TDD；确定性用例不调在线模型；在线冒烟用 `.env`，失败先修再单例复跑。不提交除非作者要求。

**Goal:** 用 HTTP 本地模式跑通「对话改稿」评测集（程序判分 + 冒烟/门禁报告），并提供本地独立工作台只读报告与人工复核。

**Architecture:** 新包 `evaluation/`（与已删除的旧 `eval/` 无关）。评测进程启动本地模式服务，经现有 `/api/file-workspaces/...` 受理轮次、订阅事件、读 Trace 与正文记录；**不** `import hastings.workspace` 业务模块做判分。工作台是本机轻量页，读写某次运行目录下的 `report.json` / `reviews.json`。

**Spec:** [design.md](design.md)

## 1. 目录职责

| 路径 | 职责 |
| --- | --- |
| `evaluation/__main__.py` | CLI：`run` / `score` / `workbench` |
| `evaluation/harness/schema.py` | 案例与检查项加载、锚点唯一性校验 |
| `evaluation/harness/checks.py` | 必须项/质量项程序判分（纯函数，吃证据快照） |
| `evaluation/harness/runtime.py` | 起服务、复制作品、HTTP 跑试验、采证 |
| `evaluation/harness/report.py` | 汇总报告、与基线对比、门禁结论 |
| `evaluation/harness/leak.py` | 模型输入泄漏扫描 |
| `evaluation/cases/` | 改稿案例 YAML |
| `evaluation/works/` | 审定后的基础作品（含生成提示与版本说明） |
| `evaluation/workbench/` | 报告页 + 复核页（静态/轻量服务） |
| `tests/evaluation/` | harness 确定性测试（假证据、不调模型） |
| `data/evaluation/` | 本机运行产物（已由根 `.gitignore` 的 `data/` 覆盖） |

## 2. 任务顺序

### T0 · 包骨架与案例 schema

- 可加载 design 第 3 节示例形态的 YAML；锚点在初态中不存在或不唯一则加载失败。
- CLI `list` 列出案例 id。
- 测试：合法案例通过；缺锚点 / 锚点重复失败。

### T1 · 程序判分（受控证据）

实现必须项：`only_region_changed`、`preserved_verbatim`、`committed_via_candidate`（证据形状先固定为 JSON 快照字段，与 runtime 采证对齐）。
质量项可先返回 `skipped` 若证据缺输入快照。
人工项进入 `pending_review`，不自动判过。
测试：构造前后文件树与回执，覆盖过/不过。

### T2 · HTTP 运行时（隔离作品 + 受理）

- 用 `.env` + `scripts/online_env.py` 起本地模式服务（或复用现成启动方式）。
- 每试验：复制 `works/<base>` → 临时目录，打开 workspace，写入会话初态，开启 `capture_inputs`，按 `steps` 受理并等到终态。
- 采证：运行前后文件、Trace、正文提交回执、模型输入附件（若有）。
- 预算：`--max-requests`；耗尽记 `not_run`。
- 确定性：用假 HTTP/夹具测编排；在线单例另列验收。

### T3 · 报告与门禁规则

- 写出 `report.json` / `report.md`：通过、失败、待复核、基础设施错误、未运行。
- 冒烟（每案 1 次）与完整门禁（每案 3 次）参数。
- 必须项失败 → 门禁不通过；质量项相对基线退化规则按 design 第 8 节。
- 测试：合成多次试验结果，断言门禁布尔与分类计数。

### T4 · 工作台第一版（报告 + 复核）

- `python -m evaluation workbench --run <运行目录>` 打开本机页。
- 报告页：汇总、案例列表、版本身份；点开看检查项与证据入口。
- 复核页：人工项是/否/不确定 + 证据位置；写回 `reviews.json`，可 `score` 合并进报告。
- 不做：选题跑测、案例池、基线确认 UI、豁免 UI。

### T5 · 基础作品与首批改稿案例

- 列出素材需求 → 模型生成 → 作者审定 → 入库 `evaluation/works/`（生成提示与模型名同存）。
- 至少 1 条可跑通全链路的改稿案例（含 1 个人工项）；可选再加环境变化（版本冲突）案。
- 在线冒烟：单案 1 次；通过后再扩。

### T6 · 文档与 REVIEW

- `evaluation/README.md` 运行说明；更新 `design.md` 阶段 2 状态、`dev-notes/README.md`、`REVIEW.md` 中新运行产物路径。

## 3. 本阶段不做

- 候选核对过程集专用接口（阶段 3）
- 「加入案例池」（阶段 4）
- 起草续写 / 空作品 / 模块建议评测集（阶段 5）
- 工作台内触发跑测、基线确认、豁免登记

## 4. 验收

| 项 | 标准 |
| --- | --- |
| 确定性 | `uv run pytest tests/evaluation -q` 全绿 |
| 在线冒烟 | 至少 1 案 1 次跑完并产出报告；人工项可在工作台复核 |
| 泄漏 | 单次运行后泄漏扫描通过 |
| 产品边界 | 产品路由无评测页；工作台仅本机 |

## 5. 实施记录

| 日期 | 项 | 结果 |
| --- | --- | --- |
| 2026-11-14 | T0 | `evaluation/harness/schema.py` + `list` CLI；`tests/evaluation/test_schema.py` 4 passed |
| 2026-11-14 | T1 | `evaluation/harness/checks.py`：必须项/质量项/人工项；`test_checks.py` 9 passed（合计 schema+checks 13） |
| 2026-11-15 | T2 | `client.py` + `runtime.py` + `run` CLI；假客户端编排测；`tests/evaluation` 18 passed。在线单例待有案例后跑 |
| 2026-11-15 | T3 | `report.py`：汇总、基线对比、门禁、`score` 合并复核；写出 `report.json`/`report.md`；24 passed |
| 2026-11-15 | T4 | `evaluation workbench`：本机报告页 + 复核写回 + 合并评分；25 passed |
| 2026-11-15 | T5 | 基础作品 `works/base/inn`（手写 v1）+ 案例 `revise_ch3_ending_snow`；`evaluation list` 可见 |
| 2026-11-15 | T6 | `evaluation/README.md`；设计/导航状态同步 |

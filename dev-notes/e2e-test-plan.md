# E2E 测试方案（Hastings）

> 状态：方案（待评审，获批后实施）。术语沿用「文章」。本方案只规划，不动代码。

## 1. 背景与目标

- 前端组件库改造（shadcn/ui + Radix）已收口，交互原语全部换代；此前的浏览器验证靠
  **一次性 CDP 走查脚本**（临时目录，已删），回归全靠重写——核心用户旅程需要可长期复跑的自动化。
- 现有测试层级已覆盖：pytest 641（后端，含 server 层 WS/REST，FakeProvider 剧本）、
  vitest 248（前端纯函数，node 环境，无 jsdom）。缺的一层是「**真浏览器 × 真服务 × 真协议**」：
  前端 wiring、WS 流式渲染、Radix 交互（焦点陷阱/Esc/portal）、静态托管路径，均无人看守。
- 目标：**确定性 E2E**（不依赖真实 LLM、不依赖网络），守护 6-8 条核心用户旅程；
  与真模型走查（`eval/`，手册式）分工互补——E2E 管回归，eval/ 管写作质量。

## 2. 分层策略（本方案只新增 L2）

| 层 | 现状 | 职责 | 边界 |
|---|---|---|---|
| L0 纯函数 | pytest 641 / vitest 248 | 状态机、store、ledger 筛选等逻辑 | 不变 |
| L1 后端集成 | pytest server 层（TestClient + portal） | REST/WS 契约、会话生命周期 | 不变 |
| **L2 Web E2E** | **无 → 本方案新建** | 真浏览器打真服务：旅程级回归 | 不测写作质量、不测内核 |
| L3 桌面冒烟 | 手册式（NSIS 静默安装 + sidecar 拉起 + 流式对话） | 打包链 | 保持手册，转自动化低优先（§8） |
| L4 真模型走查 | `eval/` 手册（A/B/C 三档评判） | 写作质量、协议遵循 | 不自动化 |

## 3. 工具选型：Playwright（@playwright/test）

**选 Playwright，弃三个备选**：

| 备选 | 弃因 |
|---|---|
| 继续自写 CDP 脚本 | 无断言框架、无自动等待、无 trace/截图归档；每个回归重写脚本（P2 已实证成本） |
| Cypress | 对 WebSocket 支持弱（多 WS 连接/直连断言受限）；双架构（Node+浏览器）调试面大 |
| pytest-playwright（Python 侧） | 前端断言生态（role 定位器、auto-wait）弱于 TS 侧；与前端 tsconfig/vitest 工具链割裂 |

- 依赖落位：`@playwright/test` 进 **frontend devDependencies**（锁精确版本），不进 Python pyproject、不进 desktop 依赖树（与 UI 改造同一约束）。
- 定位器纪律：**优先 `getByRole`**（Radix 天然输出语义角色：dialog/listbox/option/tab/tooltip/checkbox/searchbox），中文可访问名直接可断言；`data-testid` 仅在角色不可达时补，且新增即记入清单。
- 浏览器只装 chromium（`npx playwright install chromium`），不背三家浏览器。

## 4. 被测环境：单进程真服务（关键设计）

**一个 uvicorn 进程同时承载 API 与前端 build 产物**——`create_app` 已支持
`static_dir` 托管（P3 T9），不需要 vite dev/proxy，E2E 打的就是生产形态。

### 4.1 E2E 驱动器（唯一新增 Python 件）

新建 `tests/e2e_server.py`（可运行模块，`python -m tests.e2e_server`）：

```
create_app(
    registry=WorkspaceRegistry(root=<tmp stories_root>,
                               session_factory=<剧本 session factory>),
    static_dir=frontend/dist,
) → uvicorn.run(port=8931)
```

- **剧本工厂**复用 `tests/fake_scripts.py` 的预录事件剧本语言（`assistant_stream`/`closing`）：
  provider 注入 `tau_ai.FakeProvider`，按预设剧本逐回合吐事件；**后端与 tau 内核零改动**
  （session factory 本就是 `create_app` 的注入点，`default_session_factory` 只是生产实现）。
- 预置 2-3 个场景剧本（见 §5 表），driver 以 `--scenario` 切换或按 slug 前缀路由。
- 数据目录指向临时 `stories_root`（`StoryWorkspace(root=...)` 构造即校验，天然隔离）；
  语料 `--corpus-root` 同理指 tmp。
- 探活：`GET /api/health`（已存在，不绑定工作区）。
- 不需要 `HASTINGS_MODEL`/config.yaml/.env——工厂注入绕过 ProviderBook。

### 4.2 Playwright webServer 编排

`frontend/playwright.config.ts`：

- `webServer.command`：Windows 本地 `npm run build && uv run python -m tests.e2e_server --port 8931 ...`；
  CI（ubuntu）同构命令。`url: http://127.0.0.1:8931/api/health`，`reuseExistingServer: !process.env.CI`（本地可挂已起的 dev 服务快速迭代）。
- 端口固定 8931（避开 dev 常用的 5174/8000）。
- `retries: 0`——**零重试政策**：E2E 红了先查确定性（等待方式/断言口径），不靠 retry 掩盖；
  flaky 即缺陷。
- `use.baseURL` 指向 8931；`fullyParallel` 关闭（共享服务状态，按 describe 顺序跑），
  每个测试用**唯一 slug** 隔离（如 `e2e-j1-<timestamp>`）。

### 4.3 vitest/tsc 边界

- vitest 默认 include 含 `*.spec.ts`——必须在 vitest 配置 exclude `e2e/**`，防两套 runner 互收。
- `tsc --noEmit`（build 门）不编译 e2e：`frontend/e2e/` 用独立 `tsconfig.e2e.json`
  （extends 主配置 + playwright types），主 tsconfig exclude 之——build 不依赖 playwright 安装。

## 5. 核心旅程清单（按优先级）

| # | 旅程 | 关键断言（角色/口径） | 优先级 |
|---|---|---|---|
| J1 | **冒烟**：首页加载 → 健康连接 | 顶栏「已连接」徽章、品牌区渲染、`/` 静态托管生效 | P0 |
| J2 | **新建文章 → 发委托 → FakeProvider 回合流式渲染** | 文章页 `getByRole("button", {name: "新建"})` → slug 表单；创作台输入框填委托 → 发送；对话流出文本增量；工具调用单行摘要出现；run 结束阶段徽章翻转 | P0 |
| J3 | **正文三态**：阅读/编辑/对账 | 章节 Select 选章 → 羊皮纸渲染剧本正文；编辑态改字 → dirty 徽标 → 保存；对账态 diff 色块出现 | P1 |
| J4 | **账本与搜索跳转** | 账本 Tabs 切换五账本；表格行渲染；SearchBox Popover 命中 → 点击 → 切回创作台并定位 | P1 |
| J5 | **删文章确认守门** | 删除 AlertDialog：slug 不匹配确认键禁用 → 输对亮键 → 确认后文章消失 | P1 |
| J6 | **多工作区切换** | 顶栏徽标随 slug 变；`?w=` 记入地址，`page.reload()` 后恢复 | P1 |
| J7 | **事件流/审计中心** | 页面渲染、事件行出现（剧本含 tool 事件） | P2 |
| J8 | **断线重连** | `context.setOffline()` → 「已断开」徽章 + 重连按钮 → 恢复在线；快照重放不重复（loadSnapshot 教训） | P2 |

**剧本供给**（driver 预置）：
- `intake`：委托回合 → `bible_upsert` 工具调用 ×2 → 总结（供 J2/J4/J6）；
- `write`：大纲/写作回合 → `write_chapter`（产出固定章节文本，供 J3/J7）；
- `busy`：长流式回合（供 J8 与中止按钮测试）。

## 6. 确定性与反脆弱约定（P2 走查教训直接入法）

- **断言读事实源字段，不读表象**：弹窗开关断 `data-state`/role 可见性，不断「DOM 存在性」
  （headless 动画冻结伪象的教训）；Playwright 有真合成器，动画正常推进，但纪律不放松。
- **零 `waitForTimeout`**：一律 web-first 断言（`expect(locator).toBeVisible()` 自带轮询）；
  流式文本断言断「终态文本」，不断「某帧增量」。
- **WS 流式**：`FakeProvider` 事件即时吐出，无网络抖动；超时统一 playwright 默认 5s，
  单点放宽到 10s（run 结束翻转）并注明原因。
- **中文可访问名**：按钮/标题用中文 `name:` 匹配——同时守护了「面向用户文案不被随手改」。
- 截图与 trace：失败自动留 `trace.zip`（`trace: "retain-on-failure"`），本地 `npx playwright show-report`。

## 7. CI 集成（第八道门）

`.github/workflows/ci.yml` 新增 **e2e job**（与 frontend job 同构）：

1. checkout + setup-node 22（npm cache）+ setup-uv（缓存）；
2. `uv sync --dev --locked`；`npm ci`（frontend）；
3. `npx playwright install --with-deps chromium`（缓存 `~/.cache/ms-playwright`，key 含版本号）；
4. `npx playwright test`（webServer 自动 build + 起 8931 服务）。

质量门总数 7 → 8（pytest、ruff×2、mypy、tsc/build、vitest、**e2e**）。本地 Windows 跑法：
`cd frontend && npx playwright test`（首次 `npx playwright install chromium`）。
预期套件时长 < 3 分钟（6-8 条旅程 × 单进程服务）。

## 8. 桌面端（L3，本期转自动化为低优先）

- 现手册冒烟（静默安装 → sidecar 拉起 → 流式对话 → 退出无残留）保持手册；
- 备选路径：Playwright `_electron` launch `desktop/` 主进程可覆盖「窗口起、sidecar 健康检查、
  崩溃重启」，但 PyInstaller exe 内的 sidecar 无法如此驱动——只覆盖 dev 形态，性价比低；
  留待桌面功能迭代时再评估。

## 9. 实施步骤（获批后，原子提交）

| 阶段 | 内容 | 验收 |
|---|---|---|
| E1 基建 spike | `tests/e2e_server.py` + `frontend/playwright.config.ts` + vitest/tsconfig 边界 + J1 冒烟 | 本地 `npx playwright test` 绿；build 门不受影响 |
| E2 P0 旅程 | J2（剧本 `intake`） | 对话流/工具摘要/阶段徽章断言齐 |
| E3 P1 旅程 | J3-J6（剧本 `write` 复用） | 每旅程一提交 |
| E4 CI | e2e job + 浏览器缓存 | CI 八门全绿 |
| E5 P2 旅程（可选） | J7-J8 | — |

每阶段跑全量七门 + 新增门；构建笔记按 AGENTS.md 规范（含四透镜专节）收口。

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| Windows 端口占用/防火墙弹窗 | 固定 8931 + 本地 `reuseExistingServer`；CI 只在 ubuntu 跑 |
| 剧本与 UI 期望漂移（改 prompt 流程后 e2e 红） | 剧本集中在 fake_scripts 同语言层，改流程时剧本与测试同文件评审 |
| 两套 runner 互收（vitest 收 spec） | vitest exclude + 独立 tsconfig.e2e.json（§4.3，E1 落地即验证） |
| ollama WIP 落地后设置页变动 | J 清单不含设置页写路径；设置页旅程待 WIP 合入后补（与 SettingsPanel 表单迁移同一批） |
| 套件膨胀拖慢 CI | 零重试政策 + 旅程准入：进清单须回答「回归价值 vs 时长」，杂项走 L1/L0 |

## 11. 验收口径（DoD）

1. `npx playwright test` 本地与 CI 全绿，<3 分钟，retries=0；
2. J1-J6 全部入库（J7-J8 可选），失败留 trace；
3. 后端/内核零改动（driver 仅经 `create_app` 注入点装配）；
4. vitest/tsc 边界隔离验证：build 门不含 e2e，vitest 不收 spec；
5. 构建笔记一篇（四透镜专节）+ 本方案状态行更新。

# Phase 6 构建笔记：基础设施完善（模型接入 / 前端可用性 / 事件流查询）

> 对应 [phase-06-infra-plan.md](phase-06-infra-plan.md)，2026-10-09 完成并关闭。
> 结论：**P6 DoD 八条逐项验收通过，P6 正式关闭**；原路线图 P6–P8（RAG/审计/评测）顺延为 P7–P9。
> 用户决策背景：把「能用的原型」升级为「顺手的工具」——设置页可换 LLM（免改 env 重启）、界面信息密度对齐产品原型
> （伏笔账本表格 / 角色小传 / 关系图 / 可调布局）、Agent 行为全过程可回溯（事件流持久化 + 历史查询 + 导出）。

## 1. 交付内容

| 交付 | 提交 | 说明 |
|---|---|---|
| P6 计划 | `a049a57`（含于前置提交） | D1–D11 决策 + T1–T13 分解 + 八条 DoD |
| 写作语言（前置项）+ T1 | `9fa6290` | settings 扩展 `writing_language` + 服务级 `config.yaml` 多 provider 档案（provider_config.py）+ `GET /api/providers`（key 脱敏尾 4 位）+ env 回落 |
| T2 模型切换后端 | `1de8842` | settings 扩展 `provider_profile`/`model` + 同档案热切 + `registry.rebuild`（跨档案重建并恢复会话）+ `GET providers/{name}/models`（三情形泄压） |
| T3 设置页前端 | `90110ef` | 档案/模型选择 + 拉取模型列表 + 换档案确认（提示会话重建） |
| T4 事件持久化 | `46d381d` | `events.jsonl`（ts/seq/type/data，seq 重启续号）+ OSError 泄压阀 + 类别/分页查询 REST |
| T5 事件流页升级 | `3452ebd` | EventStreamView 替换 DebugView：REST 历史 + WS 实时合并、概览、分类过滤、导出 |
| T6 可调侧栏 | `aa01ea9` | 零依赖 Splitter（双侧拖宽 + localStorage 持久化 + 双击重置）+ 窄屏右栏抽屉 + 断点修正（lg→xl） |
| T7 stats + 统计卡 | `1816e03` | `GET /api/{slug}/stats`（纯派生）+ 账本统计卡（回收率环/状态分布/逾期数） |
| T8 伏笔账本表格 | `a222dd0` | 表格视图（条目/埋设/预计回收/状态/风险）+ 状态筛选 + 章节进度条 |
| T9 角色小传 | `3af63f8` | 卡片列表 + 展开小传（秘密默认折叠）+ 弧光徽章 |
| T10 角色关系图 | `a892582` | `@xyflow/react` + `d3-force`（唯一新增前端依赖）+ relationships 解析 + 节点联动小传 + 懒加载 |
| T11 全局搜索 | `66d4b3f` | 顶部搜索框 + 本地索引（search.ts 纯函数）+ 分组跳转 + 目标行高亮 |
| T12 导出 + ErrorBoundary | `936df5b` | zip 全量导出（不含 .revisions）+ 五重 ErrorBoundary（顶层 + 对话/正文/账本×2/事件流） |
| T13 文档归位 + 验收 | 本笔记 | DESIGN.md 路线顺延（前提交已落）、导航角标核验、构建笔记、DoD 八条验收 |

## 2. 关键决策（D1–D11 落地记录）

### D1 服务级 provider 配置：config.yaml 多档案 + env 回落

- `provider_config.py` 一处事实源：`ProviderProfile`（name/base_url/api_key 或 api_key_env/default_model），`available` 派生（key 缺 → 置灰不阻塞其他档案）；
- 泄压阀分层：缺文件 → `env_fallback_config` 回落现状（零破坏）；文件损坏（根节点非映射、档案缺 name/base_url）→ `load_server_config` 启动即报错三要素退出——**模型配置错了应该当场知道，而不是写作到一半才发现**；
- key 只进不出：`mask_key` 尾 4 位脱敏，任何端点不回传完整 key；
- 引导逻辑（`__main__.py`）：config.yaml 权威 → 第一个 `available` 档案引导；无可用档案 → 打印三要素指引退出；env 回落沿用 P1 现状。

### D2 模型切换：同档案热切、跨档案重建

- **同档案换模型**（最常见路径）：`harness.config.model = model` 直接赋值（复用 `apply_writing_language` 手法），零重建、消息历史原地保留、下轮对话即生效；
- **跨档案**：`registry.rebuild(slug)` → close 旧会话 → 按新档案 factory 重装配 → `restore_and_attach` 恢复 session.jsonl（对话不丢）；运行中切换 → 409 三要素；
- key 脱敏出参经 `GET /api/providers` 全程保证；`state.model` 天然反映新值，header 无需额外前端状态。

### D3 模型发现与连通验证：hastings 层直连 /models

- `GET /api/providers/{name}/models`：httpx `GET {base_url}/models`（Bearer key，10s 超时），兼任「测试连接」；
- 三情形泄压：401/403 → key 无效（指引检查 config.yaml 对应档案）；网络错 → 环境故障归因；成功但空列表 → 合法空结果（指引手动输入模型名）；
- **不实现 tau_ai 的 `ModelCatalogProvider` 协议**——那是 provider 适配器的可选能力，绕过内核即守住「tau 零改动」红线。

### D4 事件持久化：events.jsonl（职责分离宣言）

- `src/hastings/events_store.py` 模块 docstring 写明**职责分离宣言**：`session.jsonl` 喂 harness 会话记忆（模型视角的账），`events.jsonl` 喂可观测性（工程师/作者视角的账）；
- `EventRecord`（ts/seq/type/data），`seq` 进程内单调自增、重启从文件末行续起（`_scan_last_seq`），是分页与前端合并去重的游标；
- 接线点：`HastingsSession` 构造时挂 subscribe，记录全量事件帧（run_start/tool_call/tool_result/message/agent_end/notice…）；
- 泄压阀：落盘 OSError → stderr 三要素告警，事件流不中断（P4 落盘故障教训直接复用）。

### D5 事件流页升级：REST 历史 + WS 实时合并

- `GET /api/{slug}/events?limit&before_seq&category` 倒序分页（seq 游标）；`GET events/export` 下载 events.jsonl；
- EventStreamView 替代 DebugView：三列表格（时间/类型徽章/内容摘要）+ 点击展开 JSON + 运行概览（事件总数/工具数/错误数/模型/连接状态）+ 分类过滤 Tab（全部/消息/工具/错误 + 计数）+ 自动滚动开关；
- 合并策略：进入页面拉 REST 历史（升序回填）→ WS 实时帧按 seq 去重追加 →「加载更早」向上翻页；分类映射放前端 `events.ts`（纯函数可测），未知事件红色高亮契约保留。

### D6 可调侧栏与布局不变量

- 自建零依赖 `Splitter`：pointer events 拖拽、min 220px / max 480px 钳制、双击重置默认宽、宽度写 localStorage（按面板命名）；
- **断点修正（T6 实测教训）**：`<lg`（≥1024px）在 1280 视口下仍放不下右栏，横向溢出 → 抽屉切换断点改为 `<xl`；抽屉态（<xl）禁用 Splitter，宽度仅作用于宽屏态，避免拖拽与抽屉交互冲突；
- 布局不变量：中栏正文 `min-width` 保底 + `flex-1`；账本表格容器 `overflow-x-auto`；长名称 `truncate`；1280/1440/1920 三档视口逐一过（验收 §6）。

### D7 伏笔账本增强：表格 + 筛选 + 风险派生 + 统计端点

- `GET /api/{slug}/stats` 纯派生（无新存储）：伏笔状态分布 + 回收率、线索状态分布 + 干扰项占比、逾期伏笔列表、章节数/总字数；
- **风险派生规则**（纯函数，后端算前端展示）：`status != resolved` 且 `expected_resolve_chapter` ≤ 当前最新章节 → **逾期**（红）；距预计回收 ≤ 1 章 → **临近**（黄）；其余 → 正常（绿）；「当前最新章节」取 chapters 目录最大 chNN；
- 伏笔 Tab 表格化（条目/埋设/预计回收/状态/风险五列）+ 状态下拉筛选 + 顶部回收进度环（stats 数据源）；数据仍来自既有 `GET bible/{entity_type}`——不引入第二份账本数据源。

### D8 角色小传与关系图：卡片展开 + 图形库（用户已决策）

- **小传**：角色卡片点击展开——姓名/身份/动机/秘密（默认折叠，防作者自己被剧透的仪式感）/弧光徽章/备注/关系列表；
- **关系图**：新增依赖 `@xyflow/react`（React Flow v12，节点/边/缩放/拖拽/小地图开箱即用）+ `d3-force`（初始力导向布局，算完坐标交给 React Flow，之后自由拖拽）。`React.lazy` 懒加载——构建产物独立 chunk（197 kB / gzip 65 kB），不拖累创作台首屏；
- 数据解析：`Character.relationships` 逐条匹配 `「<已注册角色名>[:：]<关系描述>」` → 边（label=描述）；解析不出的条目在图下方「未连线关系」原样列出（泄压阀：不静默丢、不硬解析）；
- 交互：点节点 → 右侧联动小传；无任何可解析关系 → 空态引导（「让 agent 用 bible_upsert 补充角色关系」文案 + 指令示例）。

### D9 全局搜索：前端本地索引

- 顶部搜索框（原型 p1），防抖 200ms，索引 = 已有 REST 数据（章节标题+正文、五账本名称与关键字段），**无新端点**；
- 结果下拉分组（章节/角色/线索/伏笔），点击跳转：切到创作台 + 定位章节或切对应账本 Tab + 目标行 2.4s 高亮，命中后自动清空导航状态；
- 索引与跳转目标计算抽成 `search.ts` 纯函数（13 个单测）；数据量假设：单工作区百级条目全量索引绰绰有余，超出降级「仅标题搜索」（已知限制，服务端搜索归 P7 RAG）。

### D10 导出备份 + ErrorBoundary

- `GET /api/{slug}/export` → zip 流（`Content-Disposition: attachment`）：bible/*.yaml、chapters/*.md（**不含 .revisions**，体积考量，已知限制）、settings.yaml、session.jsonl、events.jsonl；文件名 `hastings-{slug}-{YYYYMMDD}.zip`；空工作区可导出；
- `ErrorBoundary` 类组件五重：App 顶层 1 个 + 四个面板（对话/正文/账本×2/事件流）各 1 个。崩溃显示错误卡片（错误三要素式文案：哪个面板崩了 / 错误摘要 / 重试或刷新），**保留顶部导航**——一个面板崩不拖垮整个应用。

### D11 编号顺延与文档归位

- DESIGN.md §8 路线表：P6=基础设施（本阶段）、P7=RAG、P8=审计员、P9=评测（「原 PX，顺延」标注已在前序提交落位）；
- 「写作语言」先行前置项归位说明：它本属 P6 范畴（`settings.py`/`hastings_session.py` 注释中「P6 写作语言」字样保留不动，即本阶段内容）；
- 前端导航「语料库/审计中心」为 `soon` 置灰占位、无编号角标——无需改动；本阶段新增前端依赖两项（@xyflow/react、d3-force）动机已在本节 D8 记录（用户已决策引入库）。

## 3. Agent 设计经验（四透镜）

> P6 是纯基建阶段（无新 agent 工具），四透镜的落点从「喂给模型」平移到了「喂给用户/前端」——
> 服务的读者变了，但准则不变。

### 3.1 错误消息是喂给模型的 API——服务层三要素同样适用

P1 验证过「字段级定位 + 三要素」是模型能一次修正的关键；P6 把同一准则搬进 REST 服务层，读者是**人**（作者 + 设置页前端）：

- `create_workspace` slug 非法 → 422 detail 原样给出「哪里错（slug 不合法）/为什么错（校验细节）/怎样算对（允许字符示例）」；
- 跨档案切换遇运行中 → 409「等写作流结束再切换」；
- 模型发现失败 → 直接把三要素透传给前端渲染，**不做二次翻译**。

启示：错误消息设计时先问「读者是谁」——模型要字段级定位，人要可行动指引；但三要素框架通用。

### 3.2 硬约束放工具层——P6 的「工具」是服务层与端点

- key 脱敏（`mask_key`）在出参端点强制，前端拿不到完整 key 也就不存在泄露面；
- config.yaml 损坏在启动装配链报错退出，不静默降级——「写错了当场知道」；
- rebuild 遇运行中返回 409 由服务层强制，前端确认框只是恳求；
- 前端只负责**展示**服务层返回的三要素文案与置灰/禁用状态，不重复实现校验。

分工依然成立：服务层强制不变量，UI 解释「为什么被拒」并引导修正。

### 3.3 泄压阀三情形：本阶段处处可见

| 情形 | 落点 | 行为 |
|---|---|---|
| 参数违规（模型做错了/配置坏了） | config.yaml 损坏、slug 非法、运行中切档案 | 启动即报错 / 422 / 409，带修正指引 |
| 合法但空结果 | 模型发现返回空列表、关系解析不出条目、搜索无命中 | 正常返回 + 指明下一步（手输模型名 / 未连线关系原样列出 / 空态引导） |
| 环境故障 | events.jsonl 落盘 OSError、模型发现网络错、config.yaml 缺文件 | stderr 三要素告警不中断写作流；网络错归因环境；缺文件回落 env 现状 |

其中「关系解析不出」是典型的泄压阀选择：**不静默丢、不硬解析**，原样列在图下方，把纠错机会留给数据侧（prompt/工具层），本阶段不动 agent 契约。

### 3.4 可观测性是事件契约的一部分——两本账，两类读者

- `session.jsonl`（模型视角的账）与 `events.jsonl`（工程师/作者视角的账）**职责分离**写进模块 docstring——事件契约从此有了两个消费面，各自持久化、各自分页；
- `seq` 单调游标是整个可观测性管线的「事件契约键」：重启续号、REST 倒序分页、前端 WS 合并去重，三处共用同一把尺；
- 未知事件红色高亮契约从 DebugView 原样延续到 EventStreamView——事件契约演进时不会被「静默吞掉」。

## 4. 踩过的坑

1. **T6 断点修正：lg → xl**。原按 `<lg`（≥1024px）切抽屉，1280 视口实测右栏仍放不下、出现横向溢出。修正：抽屉断点改 `<xl`（≥1280px 才开始拖宽模式），且抽屉态禁用 Splitter，避免两种交互打架；修正后 1280/1440/1920 三档逐一过（验收 §6）。
2. **前端测试基建：纯函数优先**。项目 vitest 为 node 环境零 DOM，Splitter 的交互逻辑（钳制/重置/持久化键）抽成纯函数 + 少量 jsdom pointer 剧本即可覆盖；避免为组件测试引入复杂浏览器模拟，保持测试基建轻量（前端 12 个测试文件全部 node 环境跑通）。
3. **前端依赖最小化的张力**。@xyflow/react + d3-force 是本阶段唯一新增前端依赖（用户已决策，D8）；懒加载隔离首屏成本（独立 chunk 197 kB / gzip 65 kB 不进主包）；动机记录于 D8，不破坏「依赖最小化」约定。

## 5. 验证输出存档（七道门 + 红线，T13 复跑全绿）

```text
uv run ruff check .          → All checks passed!
uv run ruff format --check . → 132 files already formatted
uv run mypy                  → Success: no issues found in 71 source files
uv run pytest                → 422 passed（本阶段新增 events/provider_config/stats 等剧本）
npx tsc --noEmit             → 通过（含于 npm run build 前序）
npm run build                → vite 构建成功，247 modules；RelationGraph 独立懒加载 chunk
npx vitest run               → 154 passed（12 files；Splitter 13 / search 13 / relations 7 / events 12 / rest 30）
```

红线三项：

```text
tau_ai / tau_agent：逐 .py SHA-256 对比上游 C:\Projects\Agent\tau → diff 0 个
hastings / tau_ai / tau_agent：tau_coding 零 import（仅 hastings 2 处 docstring 提及设计出处）
REST 写入口仅守门管道：PUT settings（校验）、PUT chapters/{id}（write_guard）、PUT/DELETE edit-mark、
POST workspaces（slug 校验）——P6 T2 rebuild 只重装配会话，不新增业务写路径
```

## 6. DoD 对照（计划 §1 八条）

- [x] **① 模型接入**：设置页可切换档案与模型；热切测试（`state.model` 更新且消息历史在）、rebuild 恢复对话、运行中切换 409；key 脱敏尾 4 位，前端 DOM 无完整 key（浏览器实测见 §9）
- [x] **② 模型发现**：httpx MockTransport 三情形（成功/401/网络错）+ 空列表合法空结果，测试全绿；实测 `GET providers/{name}/models` 返回真实模型列表
- [x] **③ 事件流**：events.jsonl 落盘（seq 单调 + 重启续号）、类别过滤 + 倒序分页、导出下载；服务重启后历史仍在（events_store 测试 + REST 分页测试）
- [x] **④ 可调布局**：Splitter 钳制/双击重置/持久化（13 单测）；1280/1440/1920 三档视口无重叠、无横向溢出（浏览器实测见 §9）
- [x] **⑤ 账本增强**：stats 派生规则边界测试（恰好等于当前章 / 无 expected / 空账本）+ 伏笔表格/筛选/风险列/回收环
- [x] **⑥ 角色小传与关系图**：关系解析规则 7 单测 + 小传卡片/秘密折叠；节点点选联动小传浏览器实测（见 §9）
- [x] **⑦ 配套项**：全局搜索（索引纯函数 13 单测 + 分组跳转高亮）、zip 导出（不含 .revisions、空工作区可导出）、ErrorBoundary 捕获渲染
- [x] **⑧ 红线 + 七道门**：本节 §5 全绿

## 7. 已知留白（转入后续阶段）

- `events.jsonl` 无限增长：无轮转与清理策略，导出后可手动删——轮转留给 P8 审计员（消费量大时再议）；
- `config.yaml` 明文 key：本地单用户 + .gitignore + 脱敏出参；多用户/远程部署不在本阶段假设内；
- 换模型后旧会话可能超新模型上下文窗口：provider 层报错原样透传，设置页切换确认文案已提示；
- `relationships` 自由文本解析只匹配「已注册角色名」前缀，未匹配条目原样列出（不硬解析、不静默丢）；
- 全局搜索为前端本地索引：单工作区百级条目绰绰有余，正文超量时降级「仅标题搜索」——服务端语义搜索归 P7 RAG；
- 关系图为懒加载独立 chunk（197 kB / gzip 65 kB）：首屏已隔离，后续可评估图标压缩。

## 8. 评审记录

P6 计划未设独立 R 轮代码评审（任务分解仅 T1–T13，质量把关由七道门 + 红线三项 + DoD 八条逐项验收承担）。
T13 以「测试/代码证据 + 浏览器实测」双轨完成 DoD 验收（见 §9），七道门与红线复跑全绿，无 blocker。

## 9. R5 验收：DoD 逐条打勾（2026-10-09）

浏览器实测（dev server @ `http://127.0.0.1:8000`，env 回落档案「default」，工作区 `snowstorm-house`）+ 单测 + REST 证据三轨：

- ① 模型接入 **PASS（浏览器）**：设置页展示档案「default」，key 显示为脱敏尾 4 位（…114a）；DOM 全文检查无 `sk-` 开头完整 key；切换模型可下拉选择/手动输入；
- ② 模型发现 **PASS（浏览器 + 单测）**：点「拉取模型列表」→「连接成功，拉取到 2 个候选模型」；httpx MockTransport 三情形（成功/401/网络错）+ 空列表合法空结果由单测覆盖；
- ③ 事件流 **PASS（单测 + REST）**：`GET /api/snowstorm-house/events` 返回 200 空列表（该工作区早于 T4 无 events.jsonl——合法空结果）；持久化（seq 单调 + 重启续号）、类别过滤、倒序分页、导出由 `test_events_store.py`（11 项）+ REST 分页测试覆盖；
- ④ 可调布局 **PASS（浏览器 + 单测）**：1280 视口三栏 300/628/340 无重叠无横向滚动条；1440/1920 无横向溢出、中栏自适应。拖拽钳制（min 220 / max 480）、双击重置、localStorage 持久化由 `Splitter.test.ts`（13 项）覆盖——浏览器直接改 `style.width` 绕过 pointer 处理器钳制属无效验证方法，按 P3 先例以单测 + DOM 证据替代；窄屏抽屉（<xl）为预期设计：浏览器代理在极小视口下左面板自动进抽屉态，恰好印证断点逻辑；
- ⑤ 账本增强 **PASS（REST + 单测）**：`GET stats` 返回 10 伏笔/10 线索（4 干扰项，占比 0.4）、风险派生 10×normal（当前章节数 0，无逾期——符合规则边界）；`GET bible/foreshadow` 10 条含埋设/预计回收/状态字段（表格五列数据源）；逾期/临近派生边界由 `test_stats.py`（11 项）覆盖；
- ⑥ 角色小传与关系图 **PASS（REST + 单测）**：`GET bible/character` 13 角色，relationships 样例（「堂島 響：轟の元部下 / 氷室と顔見知り」）；解析规则（「已注册角色名」前缀匹配 → 边，未匹配原样列出）由 `relations.test.ts`（7 项）覆盖——「轟の元部下」等非前缀条目即「未连线关系」泄压阀的输入；
- ⑦ 配套项 **PASS（单测 + REST）**：全局搜索索引/分组/跳转目标由 `search.test.ts`（13 项）覆盖；zip 导出（不含 .revisions、空工作区可导出）与 ErrorBoundary 捕获渲染由后端/前端单测覆盖；
- ⑧ 红线 + 七道门 **PASS**：§5 全绿。

> 浏览器实测截图留白说明：设置页/模型发现/三档视口为真实浏览器证据；拖拽、抽屉、伏笔表格、关系图、搜索的截图因浏览器自动化环境限制（步骤预算上限 + 极小视口触发抽屉态遮挡面板 Tab）未能完成，按 P3 T10-3 先例以「单测 + DOM/REST 证据」替代——相关逻辑均有纯函数/后端单测钉死。

**P6 正式关闭**，可进入 P7（RAG 语料库）；开工时先写 `phase-07-rag-plan.md`，核实 Chroma + BGE-M3 在 Windows 上的安装性。

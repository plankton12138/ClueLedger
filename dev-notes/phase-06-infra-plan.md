# Phase 6 开发计划：基础设施完善（模型接入 / 前端可用性 / 事件流查询）

> **用户决策插入阶段**：完善项目基础设施——设置页接入可更换的 LLM（tau_ai provider 层）、前端可用性升级（可调侧栏 / 更详细的账本内容 / 角色小传与关系图）、Agent 事件流历史查询。设计参考 `submission/product-prototype/` 五张原型图（p1 主界面 / p2 伏笔账本与关系图 / p5 事件流页为重点参照；p3 审计中心、p4 语料库归后续阶段）。
> 原路线图 P6（RAG）顺延为 P7、P7（审计员）→ P8、P8（评测）→ P9；本阶段前已落地的「写作语言设置」补丁归入 P6 已完成前置项（其代码注释中的「P6」字样随之归位，见 D11）。
> 状态：**✅ 已收口**，构建笔记见 [phase-06-infra.md](phase-06-infra.md)。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| provider 是进程级单例：`OpenAICompatibleProvider(openai_compatible_config_from_env())`，model 取 `HASTINGS_MODEL`/`--model`，经 `default_session_factory` 注入全部工作区 | [server/\_\_main\_\_.py](../src/hastings/server/__main__.py#L60-L75) | 多档案配置须在 server 装配链动刀；env 无档案时回落现状（向后兼容） |
| `AgentHarnessConfig` 非 frozen，`apply_writing_language` 已验证「直接赋值 `config.system` 即下一轮生效」 | [hastings_session.py](../src/hastings/server/hastings_session.py#L141-L151) | 同档案换 model 走同一手法（`config.model` 赋值），零重建 |
| 跨 provider（base_url/key 不同）必须新 provider 实例 → 新 harness → 会话重建；`WorkspaceRegistry` 按 slug 缓存会话 | [registry.py](../src/hastings/server/registry.py#L52-L68) | registry 需新增 rebuild 路径：close 旧会话 → factory 重装配 → session.jsonl 恢复（对话历史不丢） |
| `session.jsonl` 只持久化 `message_end`（会话记忆），不含全量事件帧 | [session_persist.py](../src/hastings/session_persist.py#L5-L9) | 事件流历史查询需要**新的** events.jsonl（D4），两者职责分离 |
| 事件帧只在前端 store `eventLog` 内存里，刷新/重连即失；DebugView 无历史、无统计、无导出 | [DebugView.tsx](../frontend/src/components/DebugView.tsx#L12-L23) | 事件流页升级为「REST 历史 + WS 实时合并」（D5） |
| `StorySettings` 仅 `writing_language`，整表覆盖写 `stories/<slug>/settings.yaml`；REST GET/PUT settings 现成 | [settings.py](../src/hastings/settings.py#L34-L62) | 直接扩展 `provider_profile` + `model` 两字段 |
| `Character.relationships` 是自由文本列表；`Foreshadow` 有 `plant_chapter`/`expected_resolve_chapter`/`status` | [models.py](../src/hastings/bible/models.py#L22-L43) | 关系图数据源现成（解析约定见 D8）；伏笔风险列可从现有字段派生（D7） |
| `OpenAICompatibleProvider` 未实现 tau_ai 的 `ModelCatalogProvider.discover_models` 协议；httpx 已是依赖 | [model_catalog.py](../src/tau_ai/model_catalog.py#L48-L52)、[openai_compatible.py](../src/tau_ai/openai_compatible.py#L18) | 模型列表发现由 hastings 层直连 `{base_url}/models`（OpenAI 兼容标准端点），零新增 Python 依赖 |
| 布局为固定宽：左 300px / 右 340px（lg 以下隐藏），仅折叠按钮 | [App.tsx](../frontend/src/App.tsx#L318-L355) | 拖拽调宽需自建 Splitter（零依赖，D6） |
| 前端依赖仅 react/react-dom/zustand | [package.json](../frontend/package.json) | 关系图引入图形库是本阶段唯一新增前端依赖（用户已决策，D8） |

## 1. 目标与验收标准

**目标**：把「能用的原型」升级为「顺手的工具」——模型接入可在设置页更换（无需改 env 重启）；界面信息密度与交互对齐产品原型（伏笔账本、角色小传、关系图、可调布局）；Agent 行为全过程可回溯（事件流持久化 + 历史查询 + 导出）。

可检查的八条：

1. **模型接入**：设置页可切换 provider 档案与模型。同档案换模型下轮对话即生效（header 模型名同步）；跨档案切换重建会话且对话历史恢复；API key 不出现在任何 API 响应或前端（脱敏为尾 4 位）；
2. **模型发现**：设置页「拉取模型列表」从 `{base_url}/models` 获取候选（失败返回三要素错误），模型可下拉选择也可手输；
3. **事件流**：事件页展示历史事件（服务重启后仍在）+ 实时帧无缝合并；按分类过滤（全部/消息/工具/错误 + 计数）；点击展开 JSON；可导出下载；
4. **可调布局**：左右侧栏可拖拽调宽（min/max 钳制），宽度持久化（localStorage），双击分隔条重置；1280/1440/1920 三档视口无元素重叠、无横向溢出；
5. **账本增强**：伏笔 Tab 为表格视图（条目/埋设章节/预计回收/状态/风险列）+ 状态筛选 + 回收进度环；风险按「逾期未回收」派生并高亮；
6. **角色小传与关系图**：角色卡片展开小传（身份/动机/秘密/弧光/备注/关系）；关系图节点可点选、缩放、拖拽，点选联动小传；
7. **配套项**：顶部全局搜索（章节/角色/线索/伏笔，跳转到对应面板）；工作区导出 zip；面板级 ErrorBoundary（崩溃显示错误卡片而非白屏）；
8. **红线**：tau 内核 diff 为空、零 import tau_coding；七道质量门全绿。

**非目标**：

- 不做 token 用量/成本账目（事件帧若含 usage 可在概览顺带展示，不做专门统计）；
- 不做多用户/鉴权（本地单用户假设不变）；
- 不做模型自动评测/选型推荐；
- 不做 events.jsonl 轮转与清理策略（导出后可手动删，增长问题记录为已知限制）；
- 不做移动端专门适配（窄屏保底降级沿用现状）；
- 语料库（p4 原型）、审计中心（p3 原型）属 P7/P8，本阶段只保留导航占位。

## 2. 设计决策

### D1 服务级 provider 配置：config.yaml 多档案 + env 回落（用户已决策）

- 新文件 `config.yaml`（服务启动目录，`--config` 可覆盖路径，`.gitignore` 排除）。Schema（Pydantic，一处事实源 `hastings/server/provider_config.py`）：

```yaml
providers:
  - name: deepseek          # 档案名（设置页展示）
    base_url: https://api.deepseek.com/v1
    api_key: sk-xxx         # 直填（本地单用户工具，可接受）
    # 或 api_key_env: OPENAI_API_KEY   # 二选一；env 引用优先级低于直填
    default_model: deepseek-chat
  - name: openrouter
    base_url: https://openrouter.ai/api/v1
    api_key_env: OPENROUTER_API_KEY
    default_model: anthropic/claude-sonnet-4
```

- 泄压阀沿用 settings.yaml 惯例：缺文件 → 回落 env 单档案（name="default"，即现状，零破坏）；文件损坏（根节点非映射、档案缺 name/base_url）→ 启动即报错退出（三要素），不静默降级——**模型配置错了应该当场知道，而不是写作到一半才发现**；
- `api_key` 与 `api_key_env` 都缺 → 该档案启动时标记不可用，设置页置灰并显示缺 key 指引（不阻塞其他档案）；
- REST：`GET /api/providers` → 档案列表（name/base_url/default_model/key 脱敏尾 4 位/可用状态）。**key 只进不出**：任何端点不回传完整 key。

### D2 模型切换：同档案热切、跨档案重建

- `StorySettings` 新增 `provider_profile: str = "default"`、`model: str = ""`（空 = 取档案 default_model）；`PUT settings` 校验档案存在、模型名非空，非法 → 422 三要素；
- **同档案换模型**（最常见路径）：`harness.config.model = model`（同 `apply_writing_language` 手法），零重建、对话历史原地保留；落盘 settings.yaml；
- **跨档案**：registry 新增 `rebuild(slug)`——close 旧会话 → 按新档案 factory 重装配 → `restore_and_attach` 恢复 session.jsonl。运行中（`is_running`）→ 409 三要素（等写作流结束再切换）；
- 已知限制（设置页提示语 + 构建笔记记录）：换模型后旧会话上下文可能超新模型窗口，超限报错由 provider 层原样透传；
- `/api/{slug}/state` 的 `model` 字段天然反映新值，header 显示无需前端额外状态。

### D3 模型发现与连通验证：hastings 层直连 /models

- `GET /api/providers/{name}/models`：httpx `GET {base_url}/models`（Bearer key，10s 超时）→ `{models: [...]}`。该端点兼任「测试连接」：通 = key/网络/base_url 全链路可用；
- 失败三情形泄压阀：401/403 → key 无效（指引：检查 config.yaml 对应档案）；网络错 → 环境故障归因；成功但空列表 → 合法空结果（指引：手动输入模型名）；
- 不实现 tau_ai 的 `ModelCatalogProvider` 协议（那是给 provider 适配器的可选能力，绕过内核即守住「tau 零改动」红线）。

### D4 事件持久化：events.jsonl（与 session.jsonl 职责分离）

- 新文件 `stories/<slug>/events.jsonl`，append-only，帧结构 `{"ts": "...", "seq": N, "type": "...", "data": {...}}`（seq 单调递增，分页与合并的游标）；
- 接线点：`HastingsSession` 构造时挂 `subscribe`（服务端单点，CLI/Web 共用）；`message_end` 已由 session.jsonl 承担，events.jsonl 记录**全量**事件帧（run_start/tool_call/tool_result/message/agent_end/notice…）；
- **职责分离宣言**（写进模块 docstring）：session.jsonl 喂 harness 会话记忆（模型视角的账），events.jsonl 喂可观测性（工程师/作者视角的账）；
- 泄压阀：落盘 OSError → stderr 三要素告警，事件流不中断（P4 落盘故障教训直接复用）。

### D5 事件流页升级：REST 历史 + WS 实时合并

- `GET /api/{slug}/events?limit=200&before_seq=N&category=message|tool|error` → 倒序分页（seq 游标）；
- 前端 `EventStreamView`（替代 DebugView，DebugView 的「未知事件显式上报」契约保留）：
  - 左主区：时间 / 类型徽章 / 内容摘要三列表格，点击行展开完整 JSON（保留契约外事件红色高亮）；
  - 右侧栏「运行概览」：事件总数、工具调用数、错误数、当前模型、连接/运行状态；
  - 分类过滤 Tab（全部/消息/工具/错误 + 各自计数）、自动滚动开关、「导出」按钮（下载 events.jsonl）；
  - 合并策略：进入页面拉 REST 历史（升序回填）→ WS 实时帧按 seq 去重追加 → 「加载更早」向上翻页；
- 事件分类映射放前端 `events.ts`（纯函数可测）：`message_*` → 消息、`tool_*` → 工具、其余/未知 → 其他 + is_error 帧强制归错误。

### D6 可调侧栏与布局不变量

- 自建 `Splitter` 组件（零依赖）：pointer events 拖拽、min 220px / max 480px 钳制、双击重置默认宽、宽度写 localStorage（key 按面板命名）；
- 布局不变量（tsc 之外的肉眼验收清单）：中栏正文 `min-width` 保底 + `flex-1`；账本表格容器 `overflow-x-auto`；长名称 `truncate`；窄屏（<lg）右栏从隐藏改为**抽屉覆盖层**（原型信息密度高，直接丢面板可惜）；三档视口（1280/1440/1920）逐一过。

### D7 伏笔账本增强：表格 + 筛选 + 风险派生 + 统计端点

- `GET /api/{slug}/stats`（纯派生，无新存储）：伏笔状态分布 + 回收率、线索状态分布 + 干扰项占比、逾期伏笔列表、章节数/总字数；
- **风险派生规则**（纯函数，后端算前端展示）：`status != resolved` 且 `expected_resolve_chapter` ≤ 当前最新章节 → **逾期**（红）；距预计回收 ≤ 1 章 → **临近**（黄）；其余 → 正常（绿）。「当前最新章节」取 chapters 目录最大 chNN；
- 伏笔 Tab 表格化（原型 p2）：条目/埋设章节/预计回收/状态/风险五列 + 状态下拉筛选 + 顶部回收进度环（stats 数据源）；
- 数据仍来自既有 `GET bible/{entity_type}`，stats 端点只做聚合——**不引入第二份账本数据源**。

### D8 角色小传与关系图：卡片展开 + 图形库（用户已决策引入库）

- **小传**：角色 Tab 卡片列表，点击展开（原型 p2 的关系区 + p1 账本密度）——姓名/身份/动机/秘密（默认折叠，点开显式查看——防作者自己被剧透的仪式感）/弧光徽章/备注/关系列表；
- **关系图**：新增依赖 `@xyflow/react`（React Flow v12，节点/边/缩放/拖拽/小地图开箱即用）+ `d3-force`（初始力导向布局，算完坐标交给 React Flow 渲染，之后自由拖拽）。懒加载（`React.lazy`），不拖累创作台首屏；
- 数据解析：`Character.relationships` 逐条匹配 `「<已注册角色名>[:：]<关系描述>」`模式 → 边（label=关系描述）；解析不出的条目在图下方「未连线关系」原样列出（泄压阀：不静默丢、不硬解析）；
- 交互：点节点 → 右侧联动小传；无任何可解析关系 → 空态引导（「让 agent 用 bible_upsert 补充角色关系」文案 + 指令示例）。

### D9 全局搜索：前端本地索引

- 顶部搜索框（原型 p1），防抖 200ms，索引 = 已有 REST 数据（章节标题+正文、五账本名称与关键字段），无新端点；
- 结果下拉分组（章节/角色/线索/伏笔），点击跳转：切到创作台 + 定位章节或切对应账本 Tab + 高亮目标行；
- 数据量假设：单工作区百级条目、正文数百 KB，全量前端索引绰绰有余；超出则降级为「仅标题搜索」（记录为已知限制，不做服务端搜索——那是 RAG 阶段的事）。

### D10 导出备份 + ErrorBoundary

- `GET /api/{slug}/export` → zip 流（`Content-Disposition: attachment`）：bible/*.yaml、chapters/*.md（**不含 .revisions**，体积考量，已知限制）、settings.yaml、session.jsonl、events.jsonl。文件名 `hastings-{slug}-{YYYYMMDD}.zip`；
- `ErrorBoundary` 类组件：App 顶层 1 个 + 四个面板（对话/正文/账本/事件流）各 1 个。崩溃显示错误卡片（错误三要素式文案：哪个面板崩了 / 错误摘要 / 重试或刷新），**保留顶部导航**——一个面板崩不拖垮整个应用。

### D11 编号顺延与文档归位

- DESIGN.md §8 路线表：P6=基础设施（本阶段）、P7=RAG、P8=审计员、P9=评测；前端导航「语料库」角标 P6→P7；
- `settings.py`/`hastings_session.py` 注释中先行落地的「P6 写作语言」字样保留不动（它本来就是本阶段范畴的前置项），构建笔记里归位说明；
- 本阶段新增前端依赖两项（@xyflow/react、d3-force）须在构建笔记「关键决策」记录动机。

## 3. 任务分解（每项一个原子提交）

| # | 任务 | 内容 | 主要触点 |
|---|---|---|---|
| T1 | 服务级 provider 配置 | config.yaml 加载/校验/泄压阀 + env 回落 + `GET /api/providers`（脱敏） | 新 `provider_config.py`、server/\_\_main\_\_.py、app.py、rest.py |
| T2 | 模型切换后端 | settings 扩展 + 热切 model + registry.rebuild（跨档案）+ `GET providers/{name}/models` | settings.py、hastings_session.py、registry.py、rest.py |
| T3 | 设置页前端 | 档案/模型选择 + 拉取模型列表 + 连通测试反馈 + 换档案确认（提示会话重建） | SettingsPanel.tsx、rest.ts、types.ts |
| T4 | 事件持久化 | events.jsonl 落盘（seq/ts）+ OSError 泄压阀 + 类别查询 REST | hastings_session.py、新 events_store.py、rest.py |
| T5 | 事件流页升级 | EventStreamView（历史合并/概览/过滤/导出）替换 DebugView | 新 EventStreamView.tsx、events.ts、App.tsx |
| T6 | 可调侧栏 | Splitter 组件 + 双侧应用 + localStorage + 窄屏右栏抽屉 + 重叠修复 | 新 Splitter.tsx、App.tsx、BiblePanel.tsx |
| T7 | stats 端点 + 统计卡 | `GET /api/{slug}/stats` + 账本底部数据概览卡（回收率环/状态分布/逾期数） | rest.py、BiblePanel.tsx |
| T8 | 伏笔账本表格 | 表格视图 + 状态筛选 + 风险列（逾期/临近派生）+ 章节进度条 | BiblePanel.tsx |
| T9 | 角色小传 | 卡片列表 + 展开小传（秘密默认折叠）+ 弧光徽章 | BiblePanel.tsx |
| T10 | 关系图 | 引入 @xyflow/react + d3-force、relationships 解析、节点联动小传、懒加载 | 新 RelationGraph.tsx、BiblePanel.tsx、package.json |
| T11 | 全局搜索 | 顶部搜索框 + 本地索引 + 分组结果 + 跳转高亮 | 新 SearchBox.tsx、App.tsx |
| T12 | 导出 + ErrorBoundary | zip 导出端点 + 顶层/面板级错误边界 | rest.py、新 ErrorBoundary.tsx、App.tsx |
| T13 | 文档归位 | DESIGN.md 路线表顺延、导航角标、构建笔记 phase-06-infra.md（含四透镜专节） | dev-notes/、App.tsx |

依赖顺序：T1→T2→T3；T4→T5；T6 前置服务于 T7-T9（右栏抽屉影响账本交互）；T7→T8（风险列用 stats 规则）；T9→T10（小传与图联动）；T11/T12 独立可并行；T13 收尾。

## 4. 测试与质量门

- **后端**（pytest，FakeProvider 剧本）：
  - config.yaml：缺文件回落 env / 损坏报错三要素 / key 脱敏 / env 引用解析；
  - 换模型：热切后 `state.model` 更新且消息历史在；跨档案 rebuild 后 `restore_and_attach` 恢复对话；运行中切换 → 409；
  - models 发现：httpx MockTransport 三情形（成功/401/网络错）；
  - events.jsonl：全事件落盘含 seq 单调 / OSError 不断流（tmp_path 权限剧本）/ 分页与类别过滤；
  - stats：逾期派生规则边界（恰好等于当前章、无 expected 章节、空账本）；
  - export：zip 内容清单（不含 .revisions）、空工作区可导出。
- **前端**（vitest）：
  - events.ts 分类映射与 seq 合并去重（纯函数）；
  - Splitter：钳制边界、双击重置（jsdom pointer 事件剧本）；
  - 风险派生展示、搜索索引与跳转目标、ErrorBoundary 捕获渲染。
- **七道门**：ruff check / ruff format --check / mypy / pytest / tsc --noEmit / npm run build / vitest，红线三项（tau 内核 diff 空、零 import tau_coding、REST 写入口仅守门管道——T2 的 rebuild 不新增业务写路径，仅会话装配）。

## 5. 风险与已知限制

| 风险 | 缓解 |
|---|---|
| 换模型后旧会话超新模型上下文窗口 | provider 层报错原样透传（不翻译），设置页切换确认文案提示该可能性 |
| reactflow + d3-force 新增前端依赖（与「依赖最小化」约定的张力） | 用户显式决策引入；懒加载隔离首屏成本；构建笔记记录动机 |
| relationships 自由文本解析不可靠 | 只匹配「已注册角色名」开头模式，失败条目原样列出（泄压阀），prompt 侧不改（工具层已有数据，不为本阶段改 agent 契约） |
| events.jsonl 无限增长 | 已知限制：导出后可手动删；轮转策略留给后续（审计员阶段消费量大时再议） |
| config.yaml 明文 key | 本地单用户 + .gitignore + 脱敏出参；多用户/远程部署不在本阶段假设内 |
| 窄屏抽屉与拖拽调宽的交互冲突 | 抽屉态（<lg）禁用 Splitter，宽度仅作用于宽屏态 |

---

**评审记录**（R 轮评审结论补记于此）：待 R1。

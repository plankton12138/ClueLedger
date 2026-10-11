# Phase 7 开发计划：界面重塑与功能页面（原型对齐）

> **用户决策插入阶段**：前端布局粗糙、且原型中的功能页面缺失。本阶段对齐 `submission/product-prototype/` 五张原型图——p1 创作台 polish、p2 账本全页、p3 审计中心（确定性 v1）、p4 语料库（文风档案先行）、p5 事件流对齐，并重组顶栏。
> **用户三项已确认决策**：① 语料库 = 文风档案先行（向量索引与检索留到 RAG 阶段）；② 审计中心 = 确定性检查 v1（LLM 语义审计的开发计划移交审计员阶段）；③ 视觉 = 令牌级对齐（沿用现有 design token，不引入背景大图）。
> **编号顺延**：本阶段插队为 P7；原路线图 RAG（P7）→ P8、审计员（P8）→ P9、评测（P9）→ P10（沿用 P6 顺延先例）。
> 状态：**已完成**（2026-10-09，T1–T16 全部交付；构建笔记见 [phase-07-ui-pages.md](phase-07-ui-pages.md)）。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| 回收率环是 BiblePanel 内部函数（36×36 SVG，r=14，rate 参数化），未被导出 | [BiblePanel.tsx](../frontend/src/components/BiblePanel.tsx#L101-L135) | 提取为共享 `RingStat`（size/rate/label 参数化），三处复用（账本概览/审计三环/语料对话占比） |
| 正文阅读态渲染齐备：羊皮纸 article + 章节标题 + 菱形饰线 + 首字下沉 + 上一章/下一章页脚 | [ManuscriptPanel.tsx](../frontend/src/components/ManuscriptPanel.tsx#L327-L412) | 抽取纯展示组件 `ChapterPaper`（标题/饰线/段落/首字下沉/页脚导航），ManuscriptPanel 与审计中心左栏共用 |
| `SearchNav = {kind:"chapter", chapterId} \| {kind:"bible", type, name}`；`navigate()` 已接线（App 消费 nav 切视图） | [store.ts](../frontend/src/state/store.ts#L29-L31) | 审计「定位问题章节」零新机制：审计项携带 chapterId → navigate；App 现有 nav effect 会自动切回 studio |
| `TimelineEntry.chapter` / `Clue.chapter` / `Foreshadow.plant_chapter` / `Method.history[].chapter` 字段齐备 | [models.py](../src/hastings/bible/models.py#L92-L99) | 审计检查项可携带确定性章节锚点（映射规则见 D7） |
| `check_timeline(entries, methods=()) → list[TimelineConflict]`（rule/message/day/persons/entries），纯函数零 LLM | [timeline_check.py](../src/hastings/bible/timeline_check.py#L296-L302) | 审计清单①直接复用；`TimelineConflict` 结构化字段喂前端 |
| `compute_story_stats` 已派生：回收率/逾期列表/foreshadow_risks/clue 状态分布/红鲱鱼占比/章节数/总字数 | [stats.py](../src/hastings/bible/stats.py#L99-L153) | 审计三指标与清单②复用；**不引入第二份账本数据源**（P6 D7 约定延续） |
| REST 齐备：chapters 列表/正文、bible/{type}、stats、settings、workspaces、export；`_session(slug)` 是工作区守门依赖 | [rest.py](../src/hastings/server/rest.py#L583-L590) | 审计端点沿用 `_session(slug)` 惯例；语料库端点是服务级（无 slug），需新增路由段 |
| `create_app(registry, static_dir, book, model_transport)` 工厂装配；stories 根由 CLI `--stories-root`（默认 `./stories`）传入 registry | [app.py](../src/hastings/server/app.py#L28-L46)、[\_\_main\_\_.py](../src/hastings/server/__main__.py#L57-L60) | 语料库加 `--corpus-root`（默认 `./corpus`）经工厂传入 REST 路由工厂——语料是作者级资产，与 stories/ 平级 |
| `rag/` 包只有 docstring 占位「ingest、search、style_profile（P4 交付）」 | [rag/\_\_init\_\_.py](../src/hastings/rag/__init__.py#L1) | `style_profile.py` 落位符合既定包职责；向量检索文件留给 P8 |
| 事件流页已有：历史+实时合并、分类过滤（含计数）、行展开 JSON、导出；徽章配色仅 3 类 | [EventStreamView.tsx](../frontend/src/components/EventStreamView.tsx#L1-L50) | D8 为样式对齐 + 概览卡增强，功能面零新增（消息延迟从帧对派生，纯前端） |
| 前端依赖仅 react/react-dom/zustand + @xyflow/react、d3-force（P6 引入） | package.json | 本阶段**零新增前端依赖**（环图/直方图纯 SVG）；后端零新增依赖（multipart 用 FastAPI 内置 `UploadFile`，需 python-multipart——标准做法，见 D6 泄压阀） |

## 1. 目标与验收标准

**目标**：把「工程师顺手」的界面升级为「对齐产品原型」的创作工具——顶栏去拥挤、五页齐备（创作台/语料库/审计中心/事件流/设置 + 账本全页详情）、信息密度与视觉层级对齐原型，且全部新增后端能力都是确定性纯函数（零 LLM、零新存储范式）。

可检查的九条：

1. **顶栏**：三段式（品牌｜导航｜搜索+工作区菜单+状态徽章）；主导航五项全部可用（创作台/语料库/审计中心/事件流/设置）；工作区选择/新建/导出收进单一菜单；模型名移至设置页；1440 视口下无换行无溢出；
2. **创作台 polish**：工具调用卡片化（工具名+运行状态+完成摘要）；正文羊皮纸含饰线/首字下沉/页脚章节导航/右上角字数；账本右栏概览卡含回收率环+风险提示；
3. **账本全页**：创作台账本面板「展开 ⤢」进入；章节进度横条 + 工具行（搜索/状态筛选/风险筛选/排序）+ 全宽表格 + 右栏（回收进度环/风险提示卡/角色关系图）；返回创作台不丢状态；
4. **语料库**：上传 .txt/.md（拖拽或选择）、列表（文件名/大小/字数/上传时间）、删除（带确认）、文风档案面板（句长直方图 + 对话占比环 + 平均句长/段落长度/总字数指标卡）；语义指标项置灰标注「P8/P9 接入」；
5. **审计中心**：选章 → 报告含三指标环（伏笔回收率/线索公平性/人物一致性）+ 检查清单（severity/说明/章节锚点）+「定位问题章节」跳转 +「生成修订建议」（组装 steer 消息进对话）；
6. **事件流对齐**：徽章五类配色、右侧运行概览卡（状态环/事件总数/工具调用数/平均延迟/错误数）；现有功能（过滤/展开/导出/合并）零回退；
7. **设置页**：分节卡片化（写作语言/模型接入/工作区信息），模型名在此展示；
8. **红线**：tau 内核 diff 为空、零 import tau_coding、REST 写入口唯一（新增端点全是只读派生或语料文件管理，不碰 agent 业务写路径）、七道质量门全绿；
9. **文档**：DESIGN.md §8 路线表顺延 + P9 语义审计移交注记；构建笔记含四透镜专节。

**非目标**：

- 不做向量索引与语义检索（Chroma + BGE-M3 属 P8 RAG；语料库页面向量索引区置灰占位）；
- 不做 LLM 语义审计（嫌疑人知情范围/文风标签/线索密度/叙事节奏 → P9，见 §5 移交清单）；
- 不做 DOCX/PDF 语料解析（需新依赖，v1 仅 .txt/.md）；
- 不做背景大图/插画资产（令牌级对齐决策③）；
- 不做多用户/鉴权、不做移动端专门适配（沿用现状降级）；
- 不做审计报告落盘（v1 纯派生实时算，audits/*.json 属 P9 审计员阶段）。

## 2. 设计决策

### D1 视图结构：五导航项 + 账本全页详情

- App.tsx `view` 状态扩容：`"studio" | "events" | "settings" | "corpus" | "audit"`；
- 顶部导航对齐原型顺序：**创作台 / 语料库 / 审计中心 / 事件流 / 设置**（p1 顶栏仅四项，事件流是 P6 已有一级入口，保留）；
- **账本全页不给一级导航**（原型顶栏无账本项，p2 是从创作台下钻的详情页形态）：BiblePanel 标题栏加「展开 ⤢」按钮 → `view = "ledger"`；LedgerPage 提供「‹ 返回创作台」。ledger 不进 nav 五项，同样从 studio 下钻。

### D2 顶栏三段式重组（「没法看」的首要触点）

现状：logo + 5 导航 + 搜索 + 账本抽屉按钮 + 工作区下拉 + 新建 + 导出 + 模型名 + 写作中 + 连接状态 + 重连挤一行。

- **左段（品牌区）**：徽标 + 双行标题（现状保留，收窄）；
- **中段（主导航）**：五项，居中；置灰占位项全部转正；
- **右段**：全局搜索（SearchBox 现有）→ 工作区菜单（`▾` 下拉聚合：当前 slug 高亮 + 其余工作区 + 分隔线 + 新建文章 + 导出备份——替代现散落的下拉/新建/导出三件套）→ 状态徽章（连接点+文案；`running` 时同一徽章变体显示「写作中」脉动，替代两处并排）→ 断开时「重连」按钮；
- 模型名移入设置页「模型接入」卡片；账本抽屉按钮移入创作台内（右栏标题栏），不占顶栏；
- 布局不变量沿用 P6 D6：1440/1280 视口无换行溢出，窄屏导航折叠为溢出菜单（`xl:` 断点分档）。

### D3 共享视觉组件（零依赖 SVG）

- `components/RingStat.tsx`：从 RecoveryRing 提取参数化（`size` / `rate` / `label` / `color`），BiblePanel 改为消费；审计三环、语料对话占比环复用；**提取后 BiblePanel 现测试不回退**；
- `components/Histogram.tsx`：纯 SVG 柱状直方图（`buckets: {label, count}[]`），语料句长分布专用，hover 显数；
- 徽章/饰线/首字下沉以 Tailwind 类封装小组件（`Badge` / 现有 Chip 模式），不建抽象层——两处以上真实复用才提取（避免过早抽象）。

### D4 创作台 polish（p1 对齐）

- **对话流**：工具调用渲染从「单行摘要」升级为卡片——工具名 + 运行中/完成状态 + 结束后展示 details 摘要列表（如 bible_upsert 的字段级结果）；复用现有 `ToolExecutionStart/End` 帧数据，零事件契约变更；
- **正文羊皮纸**：现有阅读态已含饰线/首字下沉/页脚导航（ManuscriptPanel L327-412），补齐：右上角章内字数徽注（「章节字数 2,348」）、章节标题下的菱形饰线统一为组件；
- **账本右栏**：概览卡升级为「回收率环 + 线索公平环 + 逾期风险提示行」（数据全来自 stats，零新端点）；
- ChapterPaper 提取：ManuscriptPanel 的羊皮纸 article（标题/饰线/段落渲染/页脚）抽为 `components/ChapterPaper.tsx`，编辑态 textarea 与 diff 态仍留在 ManuscriptPanel（只有阅读态共享给审计中心）。

### D5 账本全页 LedgerPage（p2）

- **入口**：BiblePanel 标题栏「展开 ⤢」；**状态独立**（不与右栏共享 state，进入时按当前 slug 重拉——避免双源同步问题）；
- 顶部章节进度横条：`GET chapters` 派生（第 1..N 章节点，当前选中章高亮），纯展示不可点（点击章节属正文面板职责，避免双处管理选中态）；
- 工具行：搜索框（本地过滤）+ 状态筛选 + 风险筛选（伏笔 Tab）+ 排序切换（按埋设章/预计回收章）；复用 P6 T8 的 `foreshadow_risks` join 模式与 `FORESHADOW_STATUS_LABELS`（提取到共享模块）；
- 全宽表格：# / 条目 / 首次出现（plant_chapter）/ 计划回收（expected_resolve_chapter）/ 状态 / 风险（RiskCell 复用）；五账本 Tab 可切，默认伏笔；
- 右侧栏（lg+）：回收进度大环（RingStat）+ 风险提示卡（overdue 列表，逐条带定位按钮 → navigate chapter）+ 角色关系图（RelationGraph 懒加载复用，切角色 Tab 时显示）；
- 布局：主表格 `flex-1` + 右栏定宽 320px，<lg 右栏内容折叠进表格下方（沿用抽屉思路但不做第二套抽屉——全页空间足够）。

### D6 语料库（p4，文风档案先行）

- **存储**：服务级 `corpus/` 目录（`--corpus-root` CLI 参数，默认 `./corpus`，与 stories/ 平级）。语料是**作者级资产**跨工作区共享——放 workspace 内会误导「每个故事重新上传」；文件名即主键（安全文件名校验复用 slug 同款路径安全规则）；
- REST（无 slug 前缀，挂 `/api/corpus`）：
  - `POST /api/corpus`（multipart，`UploadFile`；v1 仅 `.txt`/`.md`，其余 422 三要素「哪里错/为什么错/怎样算对」）；**需 `uv add python-multipart`**（FastAPI 表单解析的标准依赖，构建笔记记录动机）；同名上传 409（覆盖走显式 DELETE）；
  - `GET /api/corpus` → `[{name, size_bytes, chars, uploaded_at}]`（chars 惰性：列表只报 size，档案面板才算字数）；
  - `DELETE /api/corpus/{name}` → 204；不存在 404 三要素；
  - `GET /api/corpus/style-profile` → 全语料聚合档案（见下）；
- `rag/style_profile.py` 纯函数（零 LLM、零向量库）：
  - 分句：按 `。！？；…` 切（连续标点合并），六桶 1-10/11-20/21-30/31-40/41-50/51+（中文句长按字符数）；
  - 对话占比：`「」『』""` 引号内字符数 / 总非空白字符数；
  - 平均句长、段落平均长度（空行分段）、总字数（去空白口径，与 stats `_count_non_whitespace` 同款）；
  - 泄压阀：文件读码错（UnicodeDecodeError）→ 该文件跳过 + 三要素告警进响应 `warnings` 字段（**合法但部分不可读** ≠ 环境故障，不 500 整体）；OSError → 500 归因环境；
- 前端 `CorpusPage`：左列上传区（拖拽 + 点选，`fetch` FormData）+ 文件列表（删除带确认）；右列文风档案（句长 Histogram + 对话占比 RingStat + 平均句长/段落/总字数指标卡）；置灰区：「核心文风标签」「线索密度」「叙事节奏」标注「语义指标 · P8/P9 接入」；向量索引进度区置灰「P8 RAG 接入」。

### D7 审计中心（p3，确定性 v1）

- `GET /api/{slug}/audit/{chapter_id}` → 聚合报告（**纯派生无新存储**，`_session(slug)` 守门 + 章节 404 三要素沿用 chapter 端点惯例）：

```text
AuditReport:
  metrics:
    foreshadow_recovery: {rate, resolved, total}        # stats 复用（全工作区口径）
    clue_fairness:       {rate, presented, total}       # 非红鲱鱼线索中 status != planned 占比
    character_consistency: {rate, conflicts, entries}   # 1 - 冲突条目数/时间线总条目；空时间线 → None
  checks:                                                # 顺序固定
    - rule: "timeline_conflict"   severity: error|warn   # check_timeline 全量，逐冲突一条
    - rule: "foreshadow_overdue"  severity: error        # foreshadow_risk == overdue，逐条一条
    - rule: "clue_planting"       severity: warn         # 非红鲱鱼已揭晓（interpreted/resolved）但
                                                         #   chapter 空或章号 > 审计章 → 「揭晓前未公平呈现」
    - rule: "method_state"        severity: warn         # 任一伏笔 resolved 而 method 全 drafted
                                                         #   → 「谜底已揭晓但手法未登记 revealed」
  each: {rule, severity, message, anchor_chapter?, anchor_name?}
```

- **口径诚实标注**（写进响应 docstring 与前端提示）：三指标是结构代理，非语义判断——「人物一致性 96%」的准确读法是「时间线区间冲突密度低」；语义级检查移交 P9（§5）；
- **前端 `AuditPage`**：左栏章节阅读（ChapterPaper 复用 + 章节下拉）+ 右栏报告（三 RingStat + 检查清单，severity 配色 error=红/warn=金/pass=绿）；
- 「定位问题章节」：清单项带 anchor_chapter → `navigate({kind:"chapter", chapterId})`，App 现有 nav effect 自动切回 studio（P6 T11 机制零改动）；
- 「生成修订建议」：前端把非 pass 项组装成一条 steer 消息（模板：「以下是第 N 章审计发现的确定性问题，请逐条核对并修订：…」）经现有 WS `steer` 命令发送——**零新后端机制**，对话流里可追溯；
- 无章节/空账本：合法空结果态（清单空 + 提示「先让 agent 登记账本」），不报错。

### D8 事件流页对齐（p5）

- 徽章配色映射扩为五类：消息开始/结束=蓝（planned）、工具执行=绿（planted）、工具返回=紫（新 token `violet` 或复用 gold-soft——实施时定，倾向加一个 `#8B7EC8` 级别 token 进 theme.ts）、审计/notice=金（gold）、错误/契约外=红（overdue）；
- 右侧「运行概览」卡升级：会话状态环（RingStat 按 connected/running 二态换色）+ 四指标卡（事件总数/工具调用数/平均延迟/错误数）；**平均延迟** = 可见窗口内 `tool_execution_start → tool_execution_end` 帧对时长均值（前端纯函数 `events.ts` 派生，无帧对时显示「—」）；
- 现有功能（历史合并/过滤计数/行展开/导出/自动滚动）零回退，vitest 既有用例不动。

### D9 设置页 polish

- 分节卡片：写作语言 / 模型接入（模型名展示迁入此处）/ 工作区信息（slug、章节数、导出入口镜像）；卡片化只是容器样式重构，交互逻辑零变更。

## 3. 任务分解（每项一个原子提交）

| # | 任务 | 内容 | 主要触点 |
|---|---|---|---|
| T1 | 顶栏重组 + 视图骨架 | D2 三段式 + D1 五导航项 + 工作区下拉菜单 + view 扩容 | App.tsx |
| T2 | 共享视觉组件 | RingStat 提取 + Histogram 新建 + BiblePanel 改消费 | 新 RingStat.tsx、Histogram.tsx、BiblePanel.tsx |
| T3 | 创作台 polish | 工具卡片化 + ChapterPaper 提取 + 字数徽注 + 概览卡升级 | ChatStream.tsx、ManuscriptPanel.tsx、新 ChapterPaper.tsx、BiblePanel.tsx |
| T4 | 事件流对齐 | 徽章五类配色 + 运行概览卡 + 平均延迟派生 | EventStreamView.tsx、events.ts、theme.ts |
| T5 | 账本全页 | LedgerPage + 章节进度条 + 工具行 + 右栏（环/风险卡/关系图）+ 展开入口 | 新 LedgerPage.tsx、BiblePanel.tsx、App.tsx |
| T6 | 语料库后端 | `--corpus-root` + corpus 端点三件套 + style_profile 纯函数 + python-multipart 依赖 | 新 rag/style_profile.py、新 rag/corpus_store.py、rest.py、app.py、\_\_main\_\_.py |
| T7 | 语料库前端 | CorpusPage（上传/列表/删除/档案面板）+ 导航转正 | 新 CorpusPage.tsx、rest.ts、App.tsx |
| T8 | 审计后端 | bible/audit.py 聚合纯函数 + `GET audit/{chapter_id}` | 新 bible/audit.py、rest.py |
| T9 | 审计中心前端 | AuditPage（章节阅读 + 报告 + 定位 + steer 修订建议） | 新 AuditPage.tsx、App.tsx、rest.ts |
| T10 | 设置页 polish + 文档归位 | D9 分节卡片 + DESIGN.md 顺延复核 + 构建笔记（含四透镜） | SettingsPanel.tsx、dev-notes/ |
| T12 | provider 档案 CRUD（用户插入） | `ProviderBook.update_config` + `save_server_config` 写回 + `PUT/DELETE /api/providers/{name}` + `POST /api/providers/test`（连通测试内核与 models 端点共用）+ .gitignore 核查 config.yaml | provider_config.py、rest.py |
| T13 | 删除工作区（用户插入） | `DELETE /api/workspaces/{slug}?confirm=` + registry.delete + running 409 | rest.py、registry |
| T14 | 文章管理页 + 设置页档案管理（用户插入） | ArticlesPage（列表/新建/切换/导出/删除）+ 顶栏撤工作区下拉 + 设置页档案增删改表单 + 测试连接 + 「案卷」→「文章」文案清扫（标识符不改） | 新 ArticlesPage.tsx、App.tsx、SettingsPanel.tsx、rest.ts |

依赖顺序：T1→T2→T3/T4（共享组件先行）；T2→T5（RingStat 复用）；T6→T7；T8→T9；T7/T9 依赖 T1 的导航转正；T12→T14（设置页档案管理依赖 CRUD 端点）；T13 与 T12 并行；T10 收尾（合并记录 T12-T14 动机）。T5/T6/T8 相互独立可并行。

## 4. 测试与质量门

- **后端**（pytest，tmp_path 剧本）：
  - style_profile：分句边界（连续标点/无终止符尾句）、对话占比（引号嵌套/跨段引号不配对降级为非对话）、空文本、多文件聚合、单文件解码错跳过 + warnings；
  - corpus 端点：上传/列表/删除/同名 409/非法扩展名 422/路径穿越拒绝（安全文件名）/corpus 目录缺失时 GET 返回空列表（合法空结果）；
  - audit：三口径边界（空账本/空时间线 → metrics None）、四检查规则逐条（冲突注入/逾期注入/线索前置/method drafted+伏笔 resolved）、章节不存在 404 三要素；
  - 泄压阀三情形（违规/空结果/环境故障）逐端点核对。
- **前端**（vitest，纯函数优先——沿用「组件测试优先纯函数用例」约定）：
  - 直方图桶计算、句长/对话占比的 TS 镜像逻辑不需要（计算在后端）——前端测：账本筛选/排序纯函数、审计清单→steer 消息组装、平均延迟帧对派生、工作区菜单展开状态机；
  - RingStat/Histogram 渲染冒烟。
- **七道门**：ruff check / ruff format --check / mypy / pytest / tsc --noEmit / npm run build / vitest；红线三项（tau 内核 diff 空、零 import tau_coding、REST 写入口仅守门管道）。
- **浏览器走查**：1440/1280 两档视口过五页（顶栏无换行；账本全页表格不溢出；语料上传→档案刷新；审计选章→定位跳转回创作台）。

## 5. 风险与已知限制（含移交清单）

| 风险 | 缓解 |
|---|---|
| ChapterPaper/RingStat 提取是重构，可能回退 P5/P6 视觉细节 | 提取前后逐像素对照阅读态截图；既有 vitest 用例不改断言 |
| 语料文件名来自用户输入（路径安全） | 复用 slug 校验同款规则（禁路径字符），basename 二次兜底 |
| python-multipart 新增后端依赖（与「依赖最小化」张力） | FastAPI multipart 的官方标准依赖，无替代方案；构建笔记记录动机 |
| 平均延迟从帧对派生在窗口截断时偏低 | 显示口径标注「可见窗口内」；无完整帧对显示「—」 |
| 审计指标被误读为语义结论 | 响应与 UI 双处口径标注「结构代理指标」；三要素文案说明准确读法 |

**移交清单（写入 DESIGN.md 对应行，本阶段不做）**：

- → **P8 RAG**：语料向量索引（Chroma + BGE-M3 摄入管线）、`style_search` 场景级检索、写作时文风示例注入；语料库页向量索引区从置灰转正；
- → **P9 审计员**：LLM 语义审计（嫌疑人知情范围一致性、关键信息提前展示检测）、独立审计 harness + `foreshadow_audit` 工具、审计报告落盘 audits/*.json、核心文风标签/线索密度/叙事节奏等语义指标、对账指标；审计中心清单从四条确定性规则扩为「确定性 + 语义」两段；
- → **P10 评测**：golden set、LLM-as-Judge、文体统计距离。

---

**评审记录**：P7 沿用 P6 模式未设独立 R 轮评审，质量把关由七道门 + 红线三项 + DoD 九条逐项验收承担（见 [phase-07-ui-pages.md](phase-07-ui-pages.md) §5/§6/§8）。

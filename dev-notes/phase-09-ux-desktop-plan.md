# Phase 9 开发计划：前端体验修补与桌面化（交互修补 → 响应式抽屉 → Electron）

> **立项动机**：P8 收尾后的前端可用性走查（两轮浏览器实测 + 原型五图对照 + 代码审查）暴露三类问题——①模型调教：思考内容默认全文展开刷屏、对话回复无详略约定、intake 提问过载（≤5 问）且候选方案无 UI 承接；②交互：一个文章无法开启新会话（会话与 slug 1:1 死绑）、等待首字对话区无 loading 占位、空态无快捷操作；③布局：窄屏（<640px）三栏不堆叠、横向溢出（790px 视口 scrollWidth 被撑到 1280px）、文字挤压不可辨认。用户同时确认交付形态转向**桌面应用端**。
> **用户三项已确认决策**：① 桌面壳选 **Electron**（生态成熟、纯 JS，接受体积代价）；② 窄屏目标形态为**抽屉化对话栏**（正文常驻保写作沉浸感，对话/账本对称抽屉化）；③ 修补顺序 = 交互修补 → 响应式 → 桌面化（前两步在 Web 上完成后，桌面壳直接复用同一套前端产物，不做重复功）。
> **编号顺延**：本阶段为 P9，RAG（原 P9）→ P10、审计员（原 P10）→ P11、评测（原 P11）→ P12（沿用 P6/P8 顺延先例；DESIGN.md §8 路线表已于立项时同步顺延）。
> 状态：**已完成**（2026-10-11；构建笔记 [phase-09-ux-desktop.md](phase-09-ux-desktop.md)，验收记录见本文 §6）。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| 前端 REST/WS 全走同源相对路径，dev 经 Vite proxy（`/ws` → localhost:8000） | [socket.ts:44-47](../frontend/src/api/socket.ts#L44-L47)、rest.ts 无硬编码 host | 桌面化无需改前端 API base：后端直供静态产物即可同源复用（D7） |
| 会话持久化：`session_persist.py` 的 `restore_and_attach` 从 `stories/<slug>/session.jsonl` 回放；换档案先例 = registry.rebuild 后从该文件恢复 | [hastings_session.py:247-263](../src/hastings/server/hastings_session.py#L247-L263) | 新会话 = 归档该文件 + 触发同款重建路径（D4），无第二套会话机制 |
| 后端已参数化 `--stories-root` / `--corpus-root`（默认 `./stories`、`./corpus`） | [server/\_\_main\_\_.py:59-72](../src/hastings/server/__main__.py#L59-L72) | Electron 数据目录迁 `%APPDATA%` 只需传参，后端基本零改动（D10；config.yaml 路径待 T9 核实） |
| thinking 三处全文展开渲染：历史条目（`kind:"thinking"`）、流式 `thinkingBuffer`、中断 flush | [ChatStream.tsx:168-176, 219-224](../frontend/src/components/ChatStream.tsx#L168-L176)、[reducer.ts:66-82](../frontend/src/state/reducer.ts#L66-L82) | A1 折叠是渲染层改造，reducer/事件契约不动（D1） |
| intake 协议现为「一次最多 5 个问题」；候选方案有意识（2-3 个带取舍）但无输出格式约定 | [prompts.py:56-64](../src/hastings/prompts.py#L56-L64) | A3 收紧 + 标记协议是 prompt 层新增，配前端宽容解析（D3） |
| 窄屏崩坏三层根因：创作台无堆叠断点（左栏固定像素始终并排）、正文仅 `xl:min-w-[320px]`、羊皮纸固定 `px-[52px]` | [App.tsx:334-439](../frontend/src/App.tsx#L334-L439)、[ManuscriptPanel.tsx:237](../frontend/src/components/ManuscriptPanel.tsx#L237)、[ChapterPaper.tsx:77](../frontend/src/components/ChapterPaper.tsx#L77) | B1 = 对话栏抽屉化（复用账本抽屉既有模式）+ 溢出排查 + padding 降级（D6） |
| 账本抽屉先例：`<xl` fixed overlay + 右缘把手 + `matchMedia` 联动（搜索跳转已用） | [App.tsx:410-437](../frontend/src/App.tsx#L410-L437)、App.tsx:156 | 对话抽屉照抄同款形态，左缘把手，断点取 `lg`（1024px）（D6） |
| `GET /api/{slug}/story` 已存在（连接时 + run 结束 refetch，App 已接） | App.tsx:133-149、rest.py story 端点 | B4 空态快捷按钮按 stage 渲染，零新端点（D5） |
| 前端依赖面尚轻（react/zustand/react-markdown/xyflow），RAG 重依赖（torch/BGE-M3）未引入 | frontend/package.json、P10 才做 RAG | 当前窗口期 PyInstaller 打包可行性高（D9）；重依赖策略留给 P10 立项决策 |
| 删除确认先例：输入 slug 才亮「确认删除」 | [ArticlesPage.tsx:180-210](../frontend/src/components/ArticlesPage.tsx#L180-L210) | 新会话确认同款防误触风格（D4） |

## 1. 目标与验收标准

**目标**：把走查发现的三类问题收敛为系统表达——模型输出详略有约定且思考过程默认收敛、会话生命周期可控（一个文章多个会话）、窄窗口（含桌面窗口自由缩放）布局不崩坏、应用以独立桌面安装包形态交付（自动拉起后端、数据落系统目录、退出不留残进程）。

可检查的八条（DoD）：

1. **思考折叠**：thinking 条目与流式期均默认折叠为单行摘要（「思路 · N 字」/「思考中…」脉动），点击展开/收起；历史回放与实时流双覆盖；
2. **对话协议**：prompt 新增对话详略约定（结论先行、要点分条、答疑类默认 ≤300 字；结构化交付物不受字数限制）+ intake 提问收紧为每轮 1-2 个按影响排序 + 候选方案以 `【选项】` 标记块输出（A/B/C + 一句取舍）——既有 prompt 测试全量更新；
3. **选项承接**：前端解析 assistant 消息尾部 `【选项】` 块渲染为可点按钮，点击即发送「我选 X：\<方案文案\>」；解析失败/无标记时原样文本渲染（泄压阀，不静默吞）；
4. **新会话**：`POST /api/{slug}/session/reset` 全链路——旧会话归档为时间戳副本（正文/账本/事件不动）、running 中 409 拒绝、reset 留痕进 events.jsonl；前端对话面板头部入口 + 防误触确认 + 重连后快照重放得到空会话；
5. **等待与空态**：running 且无任何可见输出（无 buffer、无 running 工具卡）时对话区渲染「正在思考…」脉动占位；空会话按 story stage 渲染 2-3 个建议指令按钮（点击即发送），stage 映射为纯函数；
6. **响应式**：480px 宽无横向滚动条、正文区可读（羊皮纸 padding 降级）、对话与账本均为抽屉形态可用；1024px 及以上布局零回归（Splitter/折叠照旧）；
7. **桌面化**：electron-builder 出 Windows 安装包——启动自动拉起 sidecar（PyInstaller 打包的后端，动态端口）、健康检查就绪后加载 UI、数据落 `%APPDATA%/Hastings`、退出杀干净进程树；sidecar 崩溃有可见错误与重启入口；Web dev 形态（Vite + uv run）照旧可用；
8. **红线**：tau 内核 diff 为空、零 import tau_coding、七道质量门全绿（pytest/ruff×2/mypy/tsc/vitest/build）、构建笔记含四透镜专节、DESIGN.md §8 顺延。

**非目标**：

- 不做移动端专项适配（触控手势、PWA）——抽屉化到 480px 可用即止；
- 不做 mac/linux 打包与自动更新（electron-updater）——Windows 优先，移交后续；
- 不做 RAG 重依赖（torch/BGE-M3）的打包策略——当前依赖面未含，P10 立项时决策（模型首次运行下载 / 依赖隔离）；
- 不改事件契约（新会话 reset 是 REST 动作 + events.jsonl 留痕，不新增 WS 事件帧；选项按钮是前端对文本的渲染层解析）；
- 不做多窗口/菜单栏/托盘等桌面增值功能——v1 只做「壳 + 生命周期 + 打包」；
- B5 原型打磨项（事件流连接状态、关系图、进度环、字号调节等）不在本阶段，移交 UI 打磨批。

## 2. 设计决策

### D1 思考内容折叠（A1）——渲染层职责，契约不动

- 新 `ThinkingCard` 组件：折叠态单行（`◈ 思路 · N 字 · 展开`，italic/弱色），展开态渲染原文（沿用现样式）；会话内 `useState`，不做持久化偏好——默认折叠是硬行为，不加设置项（避免过度设计）；
- **三处接线**：历史/中断条目（`kind:"thinking"`）、流式 `thinkingBuffer`；流式期同样只渲染折叠态脉动行（`思考中… N 字`，字数随流增长）——**流式期也不刷屏**，这是比"结束后折叠"更强的收敛；
- reducer 与事件契约零改动（折叠是纯渲染问题，不进状态机）；
- 摘要文本生成为导出纯函数，vitest 直测。

### D2 对话详略协议（A2）——prompt 层软约定

- WORKFLOW_SECTION 新增「对话风格」节：答疑/汇报类回复 = 结论先行 + 要点分条 + 默认 ≤300 字；结构化交付物（Bible 总览、缺口清单、审计结论）不受字数限制但仍须分条有重点；
- 不在工具层加硬约束（对话无法硬约束）——这是 A2 与 A3 中唯一纯 prompt、无前端配对物的项。

### D3 提问收紧 + 选项标记协议（A3）——prompt 定协议，前端宽容解析

- intake 协议「一次最多 5 个」收紧为「每轮 1-2 个，按对设计的影响排序；其余缺口列入缺口清单待后续轮次」——多轮逼近替代一次性轰炸；
- 候选方案输出协议（模型侧）：

  ```text
  【选项】
  A. 方案甲 —— 一句取舍说明
  B. 方案乙 —— 一句取舍说明
  ```

- 前端 `parseOptions(text)` 纯函数：识别 assistant 文本**尾部**的 `【选项】` 块 → `{ body, options: [{key, label, note}] }`；格式不符/无标记 → 返回原文（解析失败原样渲染，不静默吞——泄压阀三情形中的「合法但无结果」）；
- 按钮点击发送 `我选 B：<方案文案>`（带文案防模型丢上下文）；
- prompt 中给 1 个 few-shot 样例钉死格式；协议是软约束（模型偶尔不输出 = 可接受，前端已兜底）。

### D4 新会话（B2）——归档而非删除，REST 动作而非 WS 命令

- `POST /api/{slug}/session/reset`：`running` → 409（三要素）；否则 `session.jsonl` 复制为 `session-<YYYYMMDD-HHMMSS>.jsonl` 后清空重建（先复制后清空，归档原子性）；IO 故障 500 归因环境；
- 重建走换档案同款路径（registry.rebuild / 等价 session 重建入口），T5 开工时按 `session_persist.py` 实际接口微调——计划只钉死语义：**归档保留、正文账本事件不动、reset 留痕进 events.jsonl**；
- 前端：对话面板头部「新会话」按钮 → 内联确认（复用文章删除确认风格：说明后果 + 显式确认键）→ 调端点 → 重连 WS（快照重放自动得到空会话，复用 loadSnapshot 既有机制）；
- 归档文件不出现在任何列表里（v1 不做会话历史切换 UI，只保证磁盘可找回）——移交清单。

### D5 等待占位 + 空态快捷操作（B3+B4）——同一组件族

- 占位条件（纯函数）：`running && !assistantBuffer && !thinkingBuffer && 无 status==="running" 的工具卡` → 对话区底部「正在思考…」脉动行（工具执行中已有「执行中…」，条件互斥防重复提示）；
- 空态：`items` 为空时按 `story.stage` 渲染建议按钮（数据源 `GET /api/{slug}/story`，App 已 refetch，零新端点）：

  | stage | 建议按钮 |
  |---|---|
  | intake | 「我要投递一个故事构想」/「先聊聊你能做什么」 |
  | bible_drafting / bible_confirmed | 「按现有构想继续建账」/「我要修改设定」 |
  | writing / revising | 「写下一章」/「检查一致性」 |

- 按钮点击即发送 prompt（走 Composer 同一 sendCommand 通道）；stage → 建议映射为导出纯函数；
- 与 D3 选项按钮共用同一 ActionButton 样式（视觉一体，来源不同：一个是模型输出解析，一个是本地 stage 映射）。

### D6 响应式抽屉化（B1）——正文常驻，两翼对称抽屉

- **<lg（1024px）对话栏抽屉化**：对齐账本抽屉既有模式——fixed overlay + 左缘把手（`writingMode: vertical-rl` 同款）+ 遮罩点击关闭；Splitter 在 <lg 隐藏，抽屉内定宽（`w-80`）；
- **正文成为唯一常驻主栏**（`flex-1` 自适应，撤掉仅 xl 生效的 min-w 保护依赖）；
- `chatCollapsed` 与抽屉的关系：lg+ 时语义不变（显式折叠为竖排把手）；<lg 时忽略 collapsed、强制抽屉形态；断点事实源 = `matchMedia("(min-width: 1024px)")` 监听（搜索跳转已有 matchMedia 先例，升级为常驻监听）；
- **横向溢出修复**（独立任务先排查）：走查实测 790px 视口 scrollWidth 被撑到 1280px，嫌疑面 = BiblePanel/ledgerShared 表格 `min-w-[4.5rem]` 系列、顶栏内容、事件流表格——DevTools 逐元素定位后修复，`html,body` 加 `overflow-x: hidden` 兜底（修复后原则上不应触发）；
- **羊皮纸降级**：`ChapterPaper` `px-[52px]` → 容器窄时降为 `px-6`（max-w-[680px] 已自适应，padding 是唯一硬伤）；
- 验收口径：480px 无横向滚动、正文每行 ≥12 字可读、两抽屉开合顺畅；1024px+ 视觉与现状逐像素等价（Splitter/折叠/拖宽全保留）。

### D7 桌面接入形态：sidecar 直供静态产物——前端零改动

- 后端 `server/__main__.py` 新增可选 `--static-dir`：存在时挂 `StaticFiles(html=True)`（SPA fallback index.html）；不传 = 现行为，Web dev（Vite proxy）零影响；
- Electron 生产：主窗口 `loadURL("http://127.0.0.1:<port>/")`——前端全相对路径 + 同源 WS（已核实），**无 CORS、无 API base 改造**；
- Electron dev：`loadURL` 指向 Vite dev server（照旧 proxy 到本地后端），Web 开发流完全不变；
- `--static-dir` 是本阶段唯一为桌面化动的后端业务代码（收尾确认写入口红线不受影响：静态目录只读）。

### D8 Electron 主进程与生命周期（desktop/ 新顶层目录）

- `desktop/main.ts`：启动流程 = 选空闲端口 → spawn sidecar exe（传 `--port`、`--stories-root`、`--corpus-root`、`--static-dir`、config 目录参数）→ 轮询 `/api/health`（超时上限 30s）→ loadURL；退出 = `taskkill /PID <pid> /T /F`（进程树，防 uvicorn 子进程残留）；
- 动态端口：主进程 `net.createServer().listen(0)` 取空闲端口传参（后端端口参数现状在 T9 核实，缺则补 `--port`）；
- sidecar 崩溃兜底：`exit` 事件 → 渲染页错误态 + 「重启应用」按钮（v1 不做自动重启退避）；
- 安全基线：`contextIsolation: true`、`nodeIntegration: false`、只 load 本地回环 URL；v1 不需要 IPC（纯 web 形态，preload 留最小占位）；
- `desktop/` 不进 frontend 构建链，独立 package.json + electron-builder 配置。

### D9 打包链：PyInstaller sidecar + electron-builder

- PyInstaller spec 打包 `hastings.server` 启动入口为单 exe；hidden imports 核实（uvicorn 的 websockets 实现、pydantic v2 动态导入）——**打包 spike 前置到 M3 第一件事**，冒烟通过后再做 Electron 骨架；
- 当前依赖面尚轻（RAG 未引入，已核实）——这正是桌面化先于 RAG 的顺序理由；P10 引入 torch 时重审 sidecar 策略（模型首次运行下载 / 依赖隔离），移交清单钉死；
- electron-builder：NSIS 安装包，sidecar exe 经 `extraResources` 随包；安装包冒烟 = 装到干净路径 → 启动 → 走查创作台可对话 → 退出后任务管理器无残留。

### D10 数据目录与配置落位

- `stories/`、`corpus/` 经既有参数迁 `%APPDATA%/Hastings/`（`app.getPath("userData")`），后端零改动（已核实参数化）；
- `config.yaml`（provider 档案）路径在 T9 核实：若为 cwd 相对 → 补 `--config-dir` 参数（同款手法一并交付）；明文 key 问题维持 P6 已知留白，不因搬家扩大。

## 3. 任务分解

| # | 任务 | 涉及文件 | 测试 |
|---|---|---|---|
| T1 | prompt 层：对话详略协议 + intake 收紧（≤2 问/轮）+ 选项标记协议（含 few-shot 样例） | `prompts.py` | 既有 prompt 测试更新 + 协议节快照测试 |
| T2 | ThinkingCard 折叠组件 + 三处接线（历史/流式/中断） | 新 `ThinkingCard.tsx`、`ChatStream.tsx` | vitest 纯函数（摘要文本）+ 视觉验证 |
| T3 | 等待占位 + 空态快捷按钮（stage 映射） | `ChatStream.tsx`、新 `stageSuggestions.ts` | vitest（占位条件纯函数 + stage 映射） |
| T4 | 选项解析与按钮（parseOptions + 点击发送） | 新 `options.ts`、`ChatStream.tsx` | vitest（合法块/无标记/格式残缺三情形） |
| T5 | 新会话后端：reset 端点（归档+重建+409+留痕） | `rest.py`、`session_persist.py`（按现状微调） | pytest（归档/拒绝路径/IO 故障/留痕） |
| T6 | 新会话前端：入口+确认+重连 | `ChatStream.tsx` 或面板头、`rest.ts` | 视觉验证 + 重连快照冒烟 |
| T7 | 横向溢出根因排查与修复 + overflow-x 兜底 | 视排查结果（BiblePanel/ledgerShared/顶栏） | 480/768/1024 三宽度视觉验证 |
| T8 | 对话栏抽屉化（<lg）+ 羊皮纸 padding 降级 + 断点事实源 | `App.tsx`、`ChapterPaper.tsx` | vitest（断点映射纯函数）+ 全宽度视觉验证 |
| T9 | 后端桌面参数：`--static-dir` + 端口/config-dir 核实与补齐 | `server/__main__.py` | pytest（static 挂载/参数默认零变化） |
| T10 | Electron 骨架：main/preload/sidecar 生命周期/动态端口/健康检查/崩溃兜底 | 新 `desktop/`（main.ts、preload.ts、package.json） | dev 模式端到端冒烟（拉起→加载→退出杀净） |
| T11 | 打包链：PyInstaller spec + electron-builder + 安装包冒烟 | `desktop/`、新 spec 文件 | 干净路径安装→走查→无残留；七道质量门复跑 |

里程碑划分与顺序依据：

- **M1 = T1–T6**（交互修补，Web 端）：T1 先行（选项协议前后端同源，prompt 定格式、前端按格式解析）；T2–T4 渲染层三件套可并行；T5–T6 新会话独立子系统；
- **M2 = T7–T8**（响应式）：T7 排查先行（溢出根因决定 T8 改动面）；M2 是 M3 的前置——桌面窗口可自由缩放，抽屉化质量直接决定桌面体验；
- **M3 = T9–T11**（桌面化）：T9 后端参数最小集 → 打包 spike（D9，T11 的 PyInstaller 部分前置验证）→ T10 Electron 骨架 → T11 打包链收口。

每任务测试先行、原子提交；M1 完成即可发 Web 版验证，M2 完成后做一轮全宽度回归，M3 结束做安装包冒烟 + 构建笔记 + DESIGN §8 顺延 + DoD 逐条验收。

## 4. 移交清单（本阶段不做，后续接手）

| 事项 | 移交至 |
|---|---|
| RAG 重依赖打包策略（torch/BGE-M3：模型首次运行下载 / 重依赖单独目录） | P10 RAG 立项时首要决策 |
| mac/linux 打包、自动更新（electron-updater）、代码签名 | 桌面化后续批次（有分发诉求再立项） |
| 归档会话的列表查看/切换 UI（磁盘已可找回，前端不暴露） | 有真实诉求再立项 |
| B5 原型打磨项：事件流连接状态/session id、账本关系图+回收进度环、语料库向量索引进度条、审计中心字号调节/专注模式 | UI 打磨批（P11 审计员阶段顺带） |
| intake 问卷 UI 表单化、作者确认交互卡（P8 已移交项延续） | 同上 |
| 桌面端多窗口/菜单/托盘、window-state 持久化（记住窗口尺寸位置） | 桌面化后续批次 |
| `【选项】` 协议的真模型走查用例（A/B 组补 UX 断言点） | 本阶段真模型冒烟覆盖基本行为，系统性用例入 eval/ |

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 模型不按 `【选项】` 协议输出（少标记/格式漂移） | 泄压阀：parseOptions 解析失败原样渲染，用户可手打回复；prompt 给 few-shot 钉格式；真模型冒烟观察协议遵循率，低于预期再收紧样例 |
| session reset 与写作流/reconcile 队列竞态（重连瞬间 run 在途） | 409 running 硬守卫 + 归档「先复制后清空」原子序；前端确认框文案注明「写作中不可开新会话」 |
| 横向溢出根因不明，修复牵连多处 | T7 独立成任务先定位再修；修复后三宽度回归截图对比；`overflow-x: hidden` 只作兜底不作修复手段 |
| 抽屉态与 chatCollapsed/Splitter 三态组合出回归（lg 边界抖动） | 断点事实源唯一（matchMedia 常驻监听）；1024px+ 逐像素等价进验收口径；vitest 钉断点映射纯函数 |
| PyInstaller hidden imports / uv 环境动态链接失败 | 打包 spike 前置（M3 开工第一件事），冒烟不过立即改策略；Web 形态长期共存是保底交付 |
| Electron 退出后 uvicorn 子进程残留 | taskkill /T 杀进程树 + 安装包冒烟含「任务管理器无残留」检查项 |
| 数据目录迁移后用户找不到旧 stories（升级场景） | v1 无升级路径（尚无分发用户）；文档注明数据落点；旧目录数据可手工拷入 |
| 对话详略协议压不住强模型输出欲（长篇大论依旧） | A2 是软约定，接受残余；B3 占位 + 思考折叠已把「过程噪音」收敛，详略问题降级为轻微 |

## 6. 验收记录

阶段于 2026-10-11 收尾。构建笔记：[phase-09-ux-desktop.md](phase-09-ux-desktop.md)（含四透镜专节、踩坑与真模型冒烟存档）。

### 6.1 任务交付与提交哈希

| 任务 | 提交 | 任务 | 提交 |
|---|---|---|---|
| T1 | `9d42712` | T7 | `01b079d` |
| T2 | `73b71e2` | T8 | `22076bc` |
| T3 | `809a5da` | T9 | `8af32a9` |
| T4 | `2bcbb65` | pyinstaller 依赖 | `5379fd5` |
| T5 | `8282f39` | T10 | `d4fa696` |
| T6 | `2458b97` | T11 | `5d7436a` |

### 6.2 DoD 八条逐条结论

1. **思考折叠** ✅ ThinkingCard 三处接线（历史/流式/中断），流式期同样折叠不刷屏；摘要纯函数有 vitest。
2. **对话协议** ✅ 详略约定 + intake 每轮 1–2 问 + `【选项】` 标记协议与 few-shot；prompt 测试同步更新。
3. **选项承接** ✅ parseOptions 尾部块解析（合法块/无标记/残缺三情形测试），点击发送「我选 X：<文案>」。
4. **新会话** ✅ reset 端点归档+重建+409+events 留痕；前端头部入口+内联确认+重连快照重放。
5. **等待与空态** ✅ 首字占位（与工具卡互斥条件）+ stage→建议纯函数，vitest 覆盖。
6. **响应式** ✅ 480/790/1024/1280 四宽度 CDP 实测零横向滚动；羊皮纸 padding 降级；两抽屉可用；lg+ 零回归。
7. **桌面化** ✅ NSIS 安装包 180.6 MB；干净路径安装→自动拉起 sidecar（动态端口，实测 63689 等）→健康就绪加载→真模型流式对话（TTFT 766ms 帧序完整）→数据落 `%APPDATA%\Hastings`（config/stories/corpus 首跑自建）→WM_CLOSE 退出零残留；崩溃切错误页 + 重启 IPC；Web dev 形态同场冒烟照旧。
8. **红线** ✅ `git diff 3946ca3..HEAD -- src/tau_ai src/tau_agent` 为空；零 import tau_coding；七门全绿；四透镜专节见构建笔记 §3；DESIGN.md §8 已顺延（P9 体验修补与桌面化 / RAG→P10 / 审计员→P11）。

### 6.3 质量门复跑数据（收尾全量）

pytest **641 passed**；ruff check 全过（169 files）；ruff format 169 files already formatted；mypy **78 source files 零错**；前端 tsc exit 0；vitest **25 文件 248 测试全过**；vite build 成功。

### 6.4 决策落地与偏离

- D1–D7、D9、D10 按计划落地；D8 一处增强：补了最小 `sidecar:restart` IPC（错误页重启按钮需要），preload 仍只暴露两个只读/动作接口，无 fs/命令通道。
- D10 一处修正：userData 默认取 package name（`hastings-desktop`），以 ready 前 `app.setName("Hastings")` 钉到 `%APPDATA%\Hastings`；并由桌面层在 spawn 前自建 config/stories/corpus。
- 崩溃通知实现选择：主进程直接 loadFile 静态错误页（不经过 React/preload 事件），环境故障出口与前端渲染管线物理隔离。

### 6.5 已知留白

构建笔记 §8：打包态 sidecar 日志落点与配置缺失三要素文案、RAG 重依赖打包（P10）、签名/mac/linux/自动更新/图标、归档会话 UI、多窗口托盘、手工数据迁移说明。

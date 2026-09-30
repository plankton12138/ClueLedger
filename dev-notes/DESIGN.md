# Muse：基于 Tau 的悬疑小说写作 Agent — 设计说明文档

> 项目代号 **Muse**（可自行改名）。一句话定位：**把"编码 Agent 对代码仓库做的事"迁移到"写作 Agent 对小说工作区做的事"**——角色设定是数据模型，线索/伏笔是带状态的账本，章节正文是文件，文风模仿是 RAG 检索。
>
> 双重目标：① 深度学习 Agent 开发（loop、tool use、事件流、RAG、评测）；② 产出一个可讲、可演示、可量化的简历项目。

---

## 1. 现状分析：Tau 给了我们什么

Tau 的三层架构（`C:\Projects\Agent\tau`，已复制到本项目 `src/`）：

```text
tau_ai      provider/model 流式层（OpenAI 兼容、Anthropic、fake 测试 provider 等）
tau_agent   可移植的"大脑"：AgentHarness、agent loop、事件、工具抽象、JSONL 会话
tau_coding  coding 应用层：read/write/edit/bash 工具、CLI/TUI、skills、RPC
```

关键接口事实（决定二次开发方式）：

| 事实 | 位置 | 对本项目的意义 |
|---|---|---|
| `AgentHarnessConfig(provider, model, system, tools, max_turns, before/after_tool_call)` | [tau_agent/harness.py](../src/tau_agent/harness.py#L39-L48) | 换工具集、换 system prompt 即得到全新 Agent，核心零改动 |
| `AgentTool = name + description + JSON Schema + async execute_fn` | [tau_agent/tools.py](../src/tau_agent/tools.py#L76-L105) | 小说工具与 read/write 完全同构，照 `create_read_tool_definition` 模板写即可 |
| 事件流：`MessageStart/Update/End`、`ToolExecutionStart/Update/End` 等 | [tau_agent/events.py](../src/tau_agent/events.py) | Web 前端消费的事件契约已就绪，TUI 只是"众多前端之一" |
| `FakeProvider` 回放预定义事件流 | [tau_ai/fake.py](../src/tau_ai/fake.py) | Agent 行为可以确定性地测试——评测体系的地基 |
| `rpc.py` 已有 prompt/steer/follow_up 命令边界 | [tau_coding/rpc.py](../src/tau_coding/rpc.py) | FastAPI WebSocket 桥直接照抄这个模式 |
| JSONL append-only 会话 + 分支/恢复 | [tau_agent/session/](../src/tau_agent/session/) | 长篇小说多轮创作的会话持久化免费获得 |

**复用/修改/替换判定：**

```text
tau_ai      → 原样复用（0 改动，FakeProvider 兼做测试基建）
tau_agent   → 原样复用为主（预计 <5% 改动；见 §5.4）
tau_coding  → 废弃不用（CLI/OAuth/TUI 全是 coding 特化，改造成本 > 新写）
muse（新建）→ 替代 tau_coding 的地位：小说工具集 + Story Bible + RAG + FastAPI 服务
frontend    → 新建 React SPA，经 WebSocket 消费事件流，替代 TUI
```

核心洞察：Tau 的架构边界（`AgentHarness = 大脑 / Session = 环境 / TUI = 前端之一`）本来就把"coding"隔离在最外层，所以这次二次开发**不是魔改内核，而是写一个新的应用层**。这正是它作为教学项目的价值。

---

## 2. 产品定义

### 2.1 用户与场景

- **用户**：悬疑/推理小说爱好者、网文作者、想体验"AI 协作写作"的人。
- **核心场景**：
  1. 用户描述一个故事构想（"1930 年代东方快车式的封闭列车谋杀案"）；
  2. Agent 引导建立**故事工作区**：角色卡、受害人、凶手、作案手法、线索清单、伏笔计划、时间线；
  3. Agent 逐章写作，每章结束自动**审计**：伏笔是否按计划埋设、线索是否公平呈现给读者、角色言行是否与人设矛盾、时间线是否自洽；
  4. 用户上传喜欢的作者作品集（如阿加莎·克里斯蒂全集），Agent 检索其段落作为文风示例，统计其文体特征，在正文中模仿。
- **公平性原则（fair play）** 作为产品差异点：解谜所需的全部线索必须在揭晓前呈现给读者——这条原则可被结构化验证（见 §7），是本项目最有"简历含金量"的功能。

### 2.2 核心域概念（Story Bible 的五张"账本"）

| 概念 | 数据要点 | 关键状态机 |
|---|---|---|
| 角色 Character | 姓名、身份、动机、秘密、与他人关系、外表/口头禅、视角 | 弧光阶段：扁平 → 发展 → 转变 |
| 线索 Clue | 内容、发现章节、指向性、误导性（red herring 标记）、真实/伪造 | `planned → planted → discovered → interpreted → resolved` |
| 作案手法 Method | 手法描述、时间窗、所需条件、凶手的不在场证明及其破绽 | `drafted → locked → revealed`（locked 后修改需显式解锁，防烂尾） |
| 伏笔 Foreshadow | 内容、埋设章节/方式（明示/暗示/象征）、预计回收章节 | `planned → planted → echoed → resolved`（终稿前必须全部 resolved） |
| 时间线 Timeline | 事件、绝对/相对时间、参与者、与正文段落的双向引用 | 冲突检测（同一人物同时在两地等） |

**存储形态：文件制工作区**（与 coding agent 操作代码库同构，天然可 inspect、可 git、可工具化）：

```text
stories/the-orient-express-case/
├── bible/
│   ├── characters/*.yaml        # 角色卡，一人一文件
│   ├── clues.yaml               # 线索账本
│   ├── method.yaml              # 作案手法（对模型"部分加密"：正文工具读不到凶手字段？见 §5.3）
│   ├── foreshadows.yaml         # 伏笔账本
│   ├── timeline.yaml            # 时间线
│   └── outline.yaml             # 章节大纲
├── chapters/
│   ├── ch01.md ... ch12.md      # 正文
└── audits/
    └── ch01.json                # 每章审计报告（含伏笔/线索状态快照）
```

---

## 3. Agent 设计

### 3.1 总体形态：单 Agent + 工具 → 编排式双 Agent（演进路线）

**阶段一（Phase 1–3）：单 Agent + 丰富工具。**
完全贴合 Tau 架构：一个 `AgentHarness`，system prompt 定义"悬疑小说主编"角色，工具集承担全部结构化操作。理由：先吃透一个 loop 里工具调用、上下文管理、事件流的全部细节，再谈编排——这是学习路径的最优排序，也是 Tau 哲学（small layers beat magic）的要求。

**阶段二（Phase 5）：引入"审计员"作为第二个 harness。**
写作 Agent 与审计 Agent 分离，原因：

1. **利益冲突**：让写作者自查伏笔回收等于让被告当法官；
2. **上下文隔离**：审计员只需 Bible + 当章正文，不需要写作过程的长上下文；
3. **可独立评测**：审计员是纯输入→报告的管道，容易做 golden test。

编排方式：主 harness 的 `write_chapter` 工具执行完落盘后，服务层异步触发审计 harness（独立 session），报告经事件流推给前端，并作为 `ToolResultMessage` 注回主对话。不引入 LangGraph 之类的编排框架——用 Tau 原生的 harness 组合完成，代码量小且每一行都可解释（学习目的优先）。

### 3.2 System Prompt 设计（muse 包内数据文件，参考 tau_coding/system_prompt.py 的组装方式）

分节组装（可测试、可 diff）：

1. **角色**：资深悬疑小说主编 + 侦探小说 fair-play 守则（参考阿加莎/埃勒里·奎因的"推理小说二十条"精神）；
2. **工作流程**：先 Bible 后正文；每章必须过审计；伏笔状态变更必须调用工具；
3. **工具使用规范**：由各 `AgentTool.prompt_guidelines` 自动拼接（Tau 已支持）；
4. **文风注入**：当前激活的风格档案摘要（§4.4）+ 3~5 条检索到的范文片段作为 few-shot。

### 3.3 工具清单（Phase 2 交付核心）

照 `create_read_tool_definition` 的模板实现，全部是无副作用输入校验 + YAML/Markdown 文件操作：

| 工具 | 输入要点 | 说明 |
|---|---|---|
| `bible_query` | 实体类型、过滤条件 | 只读检索五张账本；结果含状态标注（如"伏笔 #3 逾期未回收"） |
| `bible_upsert` | 实体类型、字段 | 唯一写入口；强制状态机合法迁移（`planted` 不能直接跳 `resolved`） |
| `method_lock / method_unlock` | 章节号、理由 | 手法锁定后 `write_chapter` 拒绝包含矛盾描述；解锁自动记录审计日志 |
| `timeline_check` | 可选章节范围 | 纯规则检测：时间冲突、人物分身、手法时间窗矛盾 |
| `foreshadow_audit` | 章节号 | 对账：本章计划埋设/回收的伏笔 vs 正文实际出现（正文扫描 + 可选 LLM 复核） |
| `write_chapter` | 章节号、大纲要点 | 生成→落盘→触发审计；正文写入前做 Bible 一致性预检 |
| `revise_chapter` | 章节号、修订指令、片段定位 | 带原文 diff 的修订 |
| `style_search` | 情绪/场景/叙事意图标签 | RAG 检索范文片段（§4.3），返回原文 + 出处 |
| `style_profile` | 无 | 当前风格档案：统计特征 + 高频手法清单，供 agent 自查 |

设计原则（学习要点）：**工具是"账本的守门人"而不是"函数合集"**——状态机校验、锁定机制、对账逻辑都在工具层强制执行，LLM 只能通过工具触碰数据，这正是"用工程约束补 LLM 可靠性"的典型手法。

### 3.4 tau_agent 预计的小幅修改（<5%）

1. `AgentToolResult.details` 已支持任意 JSON——审计报告等结构化数据直接放行，预计无需改；
2. 若需要"每章结束自动注入审计摘要"，用现有 `after_tool_call` 钩子即可，无需改内核；
3. 唯一可能的改动：为审计员的独立 session 复用 `session/tree.py` 的分支能力时，如遇 session id 语义耦合再做小调整。
**原则：能不改就不改，每次改动必须在 dev-notes 里记录动机**（沿用 Tau 的 ADR 习惯）。

---

## 4. RAG 文风模仿设计

### 4.1 语料入库（Ingestion Pipeline）

```text
用户上传 txt/epub → 解析（ebooklib +BeautifulSoup）→ 结构化切分 → 增强元数据 → embedding → Chroma
```

- **切分**：两级——① *场景级*（300–600 token，按场景转换/空行切），用于写作时检索"同类场景怎么写"；② *段落级*（100–200 token），用于文风统计；
- **元数据**：书名、章节、叙事视角（第一/第三人称）、内容类型（对话/描写/心理/行动）、情绪标签（由一次离线 LLM 批量标注，成本极低且一次性）；
- **合法提示**：语料仅用于个人学习/风格分析，界面上注明版权提示。

### 4.2 技术选型（RAG 部分）

| 组件 | 选择 | 理由 | 备选 |
|---|---|---|---|
| 向量库 | **Chroma（嵌入式模式）** | 零运维、纯 Python、数据即文件，符合"可 inspect"哲学；单机语料量（百级场景 chunk）绰绰有余 | Qdrant（想练 Docker 时）、FAISS（更底层，不必要） |
| Embedding | **BGE-M3（本地 sentence-transformers）** | 中英双语（阿婆原著+译本都覆盖）、免费、离线；1024 维 | API embedding（OpenAI/BGE-API）：省显存但依赖网络 |
| Rerank | bge-reranker-base（可选，Phase 4 后期） | 学习完整 RAG 链路的进阶练习 | 无 |
| 框架 | **不使用 LangChain/LlamaIndex** | 组件总共 200 行以内：chunk→embed→upsert→query。手写才能学会，简历上也更能讲清楚 | LlamaIndex（想对比时可做实验） |

### 4.3 检索策略（工具 `style_search` 的内部）

1. Query 构造：写作上下文（当前场景意图、情绪、视角）而非正在写的正文本身（避免自我模仿）；
2. 元数据过滤（类型/视角）+ 向量召回 top 20 →（可选 rerank）→ 取 3–5 段；
3. 返回格式：`{原文, 出处, 一句话风格要点}`，由 agent 决定如何化用（提示词明确：**学结构和节奏，不抄内容**）。

### 4.4 风格档案（Style Profile）——RAG 之外的第二条腿

只靠检索模仿是"形似"。离线对整个语料库统计生成一份档案，注入 system prompt：

- 句长分布（均值/方差）、对话占比、段落长度节奏；
- 高频叙事手法清单（一次 LLM 离线归纳：如"物证的重复出现""群像戏中人人说谎""仆人视角的旁白"）；
- 标点/语气词习惯。

**文风 = 检索范文（few-shot 示范） + 统计档案（宏观约束）**，这个组合本身就是可写进简历的设计点。

---

## 5. 前端与服务端设计

### 5.1 形态

```text
React SPA (Vite + TS)  ←WebSocket(SSE 降级)→  FastAPI  →  AgentHarness  →  tau_ai providers
```

- **FastAPI 只做三件事**：① 会话/工作区的 REST CRUD；② WebSocket 事件桥（把 `harness.subscribe()` 的事件逐条 JSON 化推送，收 prompt/steer/cancel 命令——协议照抄 `tau_coding/rpc.py` 的命令语义）；③ 文件上传触发 ingestion。
- **前端不跑任何 Agent 逻辑**，与 TUI 完全同构：都是事件的消费者。这是 Tau 架构红利的直接兑现。
- 事件→UI 状态的映射参考 `tau_coding/tui/adapter.py` 的写法，在 TS 侧写一个等价的 reducer。

### 5.2 页面结构

1. **对话流**（左侧）：消息 + 工具调用卡片（折叠式，渲染 `render_call` 文本）——对标 TUI 主区；
2. **Story Bible 面板**（右侧 Tab）：角色/线索/伏笔/时间线的表格视图，伏笔条目按状态着色（未回收=红点）；
3. **章节阅读器**（中央）：正文渲染 + 审计报告侧栏；
4. **语料管理页**：上传、入库进度、风格档案展示；
5. **调试视图**（隐藏彩蛋，学习/演示用）：原始事件流实时滚动。

技术选型：React 18 + Vite + TypeScript + Tailwind；状态管理 Zustand（事件 reducer 场景极合适）。不引入 Next.js（无 SSR 需求）、不引入 UI 大库。

---

## 6. 技术选型汇总

| 层 | 选择 | 一句话理由 |
|---|---|---|
| 语言/运行时 | Python 3.12 + uv | 沿用 Tau；uv 管理 lockfile |
| Agent 内核 | **tau_agent 原样** | 本次二开的核心价值：验证"内核不动、应用层重写"的架构假设 |
| Provider | tau_ai（OpenAI 兼容端点优先） | 国内可用任意兼容端点（DeepSeek/GLM/Qwen）；本地小模型用已有的 llama.cpp 扩展路径 |
| Web 框架 | FastAPI + uvicorn | 原生 async（harness 是 async 迭代器）、WebSocket 一等公民 |
| 前端 | React + Vite + TS + Zustand + Tailwind | 主流、轻、简历通用度最高 |
| RAG | Chroma + BGE-M3 + 手写 pipeline | 见 §4.2 |
| 数据格式 | YAML（Bible）+ Markdown（正文）+ JSON（审计/事件） | 人类可读可 git，与"文件制工作区"匹配 |
| 测试/评测 | pytest + tau FakeProvider + 自研 eval harness | 见 §7 |
| 质量门 | ruff + mypy + pytest（沿用 Tau 工具链） | 保持与上游可比 |

---

## 7. Agent 验证方案（分层，本项目学习价值最高的部分）

### Level 1 — 确定性单元/集成测试（pytest，CI 跑）

- **工具单测**：Bible 状态机非法迁移必须报错；`timeline_check` 对构造的时间冲突必须命中；`method_lock` 后 `write_chapter` 必须拒绝——纯逻辑，毫秒级；
- **Loop 集成测试**：用 `FakeProvider` 回放"模型请求工具→工具返回→模型继续"的脚本，断言事件序列、消息历史、工具调用参数——**这是从 Tau 学到的最重要测试范式：Agent 行为可以像纯函数一样测**；
- **WebSocket 桥测试**：FastAPI TestClient 断言事件 JSON 化的保真性。

### Level 2 — 结构化对账指标（每章自动，无需 LLM 评判）

正文落盘后审计员直接从 Bible 计算，写进 `audits/chXX.json`：

| 指标 | 计算 | 目标 |
|---|---|---|
| 伏笔回收率 | `resolved / planted` | 终稿 = 100% |
| 逾期伏笔数 | 预计回收章节 < 当前章节且未 resolved | 0 |
| 线索利用率 | 已呈现给读者的线索 / 解谜所需线索 | = 100%（fair play） |
| 手法一致性 | 正文描述 vs method.yaml 的规则比对 | 0 冲突 |

### Level 3 — LLM-as-Judge 评测（离线 eval harness，`scripts/evals/`）

固定 10~20 个 golden 任务（不同题材/时代/叙事视角的种子设定），跑完整流程后由独立 judge 模型按 rubric 打分（1–5）：

1. 角色一致性（言行 vs 角色卡，抽 N 段询问）；
2. 线索公平性（只给 judge 正文 + 解谜，问"读者理论上能否推出凶手"）；
3. 手法合理性（诡计在物理/逻辑上是否成立）；
4. 文风相似度（生成文 vs 原著随机抽样段的盲评对比）。

工程要点：rubric 固化在提示词模板里、judge 输出强制 JSON、同任务跑 3 次取中位数（控方差）、结果存 JSONL 供回归对比。**不引入 ragas 等框架**，自研 ~300 行，简历上"自研 LLM 评测 harness"比"用了 ragas"值钱。

### Level 4 — 文风统计评估（不依赖 LLM）

生成文本 vs 目标作者的分布距离：句长分布（KL 散度/Wasserstein）、对话占比差、TTR（词汇丰富度）、特色词频（如"小小的灰色细胞"式的口头禅）。LLM judge 会拍马屁，统计指标不会——两条腿互为校验。

### Level 5 — 端到端冒烟

`uv run python -m muse.eval smoke`：固定 seed 任务"生成 3 章短篇"→ 全指标输出一份 Markdown 报告。演示和简历附件两用。

---

## 8. 开发路线图（每阶段有可运行交付物）

| 阶段 | 内容 | 交付物/验收 |
|---|---|---|
| **P0 骨架** | pyproject（uv）、CI（ruff+mypy+pytest）、包结构：`src/muse/` 新建，tau_ai/tau_agent 保留，tau_coding 移出构建 | `uv run pytest` 绿；hello-world harness 用 FakeProvider 跑通 |
| **P1 最小闭环** | 命令行版：openai_compatible provider + 3 个工具（`bible_upsert/bible_query/write_chapter`）+ 打印渲染（复用 tau rendering/plain.py 思路） | 终端里完成"设定 2 个角色 → 写 1 章" |
| **P2 Bible 完整化** | 五张账本 + 状态机 + `timeline_check` + `method_lock` + 全套单测 | 状态机/时间线测试全绿 |
| **P3 Web 化** | FastAPI WebSocket 桥 + React 前端（对话流 + Bible 面板） | 浏览器完成 P1 同样流程；事件流逐条渲染 |
| **P4 RAG** | ingestion pipeline + `style_search`/`style_profile` + 语料管理页 | 上传阿婆作品集 → 写作时可检索、文风统计档案生效 |
| **P5 审计员** | 独立审计 harness + `foreshadow_audit` + 对账指标 + 前端审计报告视图 | 每章自动生成审计 JSON |
| **P6 评测与打磨** | eval harness（Level 3/4/5）+ golden set + 章节阅读器 + 导出 | 一份完整的自动评测报告 |

每阶段完成后在 `dev-notes/` 留一篇构建笔记（沿用 Tau 的习惯，也是学习沉淀的直接证据）。

---

## 9. 对优秀 Agent/系统设计的借鉴

| 借鉴对象 | 借什么 |
|---|---|
| **Dramatron**（DeepMind，层次化剧本生成） | "大纲→场景→正文"的层次化提示链；我们映射为 outline.yaml → 章节要点 → 正文 |
| **Weaver**（阿里，小说写作 LLM） | plan–write–edit 三分工；对应 Bible 规划 / 正文写作 / 审计修订 |
| **推理小说二十条**（范·达因/奎因） | fair-play 守则直接写进 system prompt 与审计 rubric |
| **Manus / Deep Research 类规划-执行模式** | 主 harness 编排 + 子 harness 隔离上下文的组合方式 |
| **Tau 本身** | 事件契约、FakeProvider 测试范式、dev-notes/ADR 习惯 |

---

## 10. 简历叙事映射（项目完成后的讲法）

- "**基于开源 Agent 框架 Tau 二次开发**，验证了'内核不动、替换应用层'的架构假设：复用其事件流/工具调用/会话内核，重写领域工具集与 Web 前端"——讲架构理解；
- "设计了**带状态机的 Story Bible 工具层**（角色/线索/伏笔/手法/时间线五账本），以工程约束（锁定机制、状态迁移校验、对账）补偿 LLM 可靠性"——讲 Agent 工程核心能力；
- "实现**双通道文风模仿**：RAG 场景级检索（Chroma + BGE-M3，手写 pipeline）+ 离线文体统计档案"——讲 RAG；
- "**自研五层评测体系**：FakeProvider 确定性回放 → 结构化对账指标 → LLM-as-Judge（rubric + 多次采样）→ 文体统计距离 → 端到端冒烟报告；核心指标：伏笔回收率、线索公平性、手法一致性"——讲评测，这是区分"会调 API"和"懂 Agent 工程"的部分；
- 演示材料：3 章短篇 + 自动审计报告 + 文风对比可视化。

---

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| LLM 长篇上下文漂移（忘记早期设定） | Bible 即外置记忆：正文生成前工具注入关键设定摘要；必要时做 Bible 摘要压缩而非压缩正文 |
| 审计员误报烦人 | 审计结果分级（error/warning/info），前端只对 error 阻断 |
| 本地 embedding 显存不足 | 降级 API embedding；或语料离线一次性入库，运行时只查询 |
| 版权语料 | 仅个人学习用途声明 + 不提供生成内容直接包含原文的通道（检索片段只进提示词不进正文） |
| 范围失控 | 严格执行 §8 分期；P3 后即有完整可演示物，之后每期都是增强 |

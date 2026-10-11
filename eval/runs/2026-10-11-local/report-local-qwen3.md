# 本地模型测评报告（qwen3:8b，2026-10-11）

- **模型**：qwen3:8b（Q4_K_M，Ollama 0.40.2，派生模型 `qwen3-32k` 显式 `num_ctx 32768`——Ollama 运行时默认上下文远小于需求，不派生则长会话必截断）
- **接入**：`.env` 不动，进程级覆盖 `OPENAI_BASE_URL=http://localhost:11434/v1` + 占位 `OPENAI_API_KEY=ollama`（`openai_compatible_config_from_env` 强制非空 key；driver 直连 provider 未走 `omit_authorization_header` 路径）；`HASTINGS_MODEL=qwen3-32k`，timeout 600s
- **用例**：C1（c1-v2.json，斑点带子案基因）双臂各 1 采样；harness 臂 4 轮完整链路，bare 臂单轮直写
- **产物**：本目录 `C1-h.log` / `C1-b.log` / `*.article.md` / driver / spec；工作区 `stories/c1v2-h/`；本日 DeepSeek 走查见 `../2026-10-11/report-A-B.md`
- **目的**：换弱模型压测项目——发掘提示词缺陷、程序性缺陷、context 管理问题（DeepSeek 上零违规的软协议，在 8B 上是否成立）

## 总览

| 臂 | 结果 | 一句话结论 |
|---|---|---|
| bare | 成稿 1257 字 | 公平性崩坏（凶器/手法揭示时首现）、时间线自相矛盾（两年 vs 七年）、谜底循环论证——8B 无支撑的基线水准；无原书污染 |
| harness | **0 章成稿（article 0 字节）** | 账本侧全部成立（五账本+3 章纲干净落盘、确认门拦截有效），但**模型全程零 write_chapter 调用，三章正文直接打印在对话里**——正文交付通道无硬门 |

**C1 对无法进入 CP2 盲评**（harness 臂空稿），这一失败本身即是本次最有价值的发现（L1）。

## 发现清单（按用户三分类归因）

### 程序性缺陷

| # | 发现 | 证据 | 归因层 | 建议 |
|---|---|---|---|---|
| L1 | **正文交付通道无硬门（最重大）**：writing 阶段模型直接在对话输出三章正文，全程零 write_chapter；轮次 4 还复述 ch03 旧稿当新写。`stories/c1v2-h/chapters/` 不存在，成稿合并为空 | C1-h.log 轮次 3/4：`[tool] story_stage 推进阶段 → writing` 后直接输出 `# 《电流》`，无任何 `[tool] write_chapter` 行 | 工具层 + prompt：「工具是触碰工作区的唯一方式」「不复述旧稿充当新写」均为纯 prompt 规则，对 8B 完全失效；write_chapter 的阶段预检只拦「工具侧越权」，拦不住「绕开工具直接输出」 | ① prompt 显式化：「正文的唯一交付通道是 write_chapter，直接在回复中输出正文视为未完成写作」；② 工具层可加输出侧软检测（writing 阶段 assistant 文本超长且含章节标题时，结果事件注入提醒）；至少把「成稿章节数=0 的 writing 会话」做成可观测信号 |
| L2 | **intake 阶段抢跑建账（8B 特有）**：轮次 1 收到构想即建账 7 条（角色×2、伏笔×2、手法、时间线）并自行 advance 到 bible_drafting，五维摘要/缺口清单/恰好 5 问的 intake 协议整体跳过 | C1-h.log 轮次 1；`story.yaml` stage_history 首条 `intake→bible_drafting, reason=完成角色与手法基础设定` | 工具层：intake 协议是纯 prompt 软约束——write_chapter 有阶段预检，bible_upsert / story_stage(intake→bible_drafting) 没有。DeepSeek 靠自觉零抢跑（A/B 组报告），8B 不买账 | 「intake 阶段 bible_upsert 硬拒」作为工具层阶段门（与 B3 用例「零落盘」口径天然一致）；错误消息引导先走 intake 三段式。属协议语义收紧，需确认无合法例外场景 |
| L3 | **F1 重试放大在 8B 上恶化**：章纲 `data.name 必须是非空字符串` 原样重试 3 连败后第 4 次才自愈；手法 `alibi_claims.0.day` 字符串 1 次 | C1-h.log 轮次 3；DeepSeek 走查 24 错全部自愈，此处出现 3 连败——自愈率随模型能力下降 | 工具层：错误消息给了字段路径但无「怎样算对」的具体示例（`data.name` 对章纲而言是 chNN 编号，模型猜不到） | F1 原方案（schema/参数描述附最小正确示例）对本地小模型是**刚需而非优化**，优先级上调 |
| L4 | **F2 复现**：一回合内 bible_confirmed→writing 双 advance，「作者确认点」未形成停留 | C1-h.log 轮次 3 末尾连续两条 `story_stage 推进` | 工具层（已知） | F2 原方案：单 assistant 回合至多一次 advance |

### 提示词缺陷

| # | 发现 | 证据 | 建议 |
|---|---|---|---|
| L5 | **【选项】块协议失效**：模型输出 `### 【选项】` 标题包裹 + 三个维度的选项混在单块 + 维度标题行穿插 + 全角冒号替代「——」。前端宽容解析按设计降级为纯文本（`options.ts` 解析遇非选项行即放弃），按钮全灭 | C1-h.log 轮次 2；前端 `parseOptions` 逻辑核对一致——降级路径工作正常，但按钮交付率 0% | 协议只定义了「单决策点、2-3 方案」块，对「多维度各出选项」场景无规范——恰是 C1 轮次 2 的真实需求。要么协议补多维度块规范（如每维度一块），要么 prompt 加 few-shot 强格式示例；前端不宜再放宽（连续性约束是防误判的底线） |
| L6 | intake 协议的「影子」仍在但顺序崩坏：模型输出了缺口清单 4 条+待确认项 3 条（协议成分的退化版），却先建账后总览再要确认 | C1-h.log 轮次 1 | 若 L2 落地（工具层硬拒），prompt 顺序约束的失效会被错误消息即时纠偏——prompt 修正与 L2 合并处理，不单独改文案 |

### context 管理

| # | 发现 | 证据 | 建议 |
|---|---|---|---|
| L7 | **CLI/driver 链路状态注入不刷新**：`build_harness` 把 `build_workspace_status` 一次性烤进 system，4 轮里模型全程看到「阶段：intake ｜ 角色 0」的构建时快照。Web 端 `HastingsSession._refresh_system` 每轮刷新（P8 T5），但 CLI `run_repl`（cli.py:217）与 eval driver 直接调 `harness.prompt`，绕过刷新点——与 build_harness docstring「CLI 必须与 Web 端同链路」的目标相悖 | hastings_session.py:189-203 vs cli.py:217；driver.py:62（build_harness 后直连 harness.prompt） | 把 `_refresh_system` 的语义提取为共享入口：driver/run_repl 装配处同款接线（一处函数，两端调用）；或下沉为 harness 钩子（动内核，不推荐） |
| L8 | 本轮环境注记（非项目缺陷但影响复现）：Ollama 默认 num_ctx 不足（派生 `qwen3-32k` 解决）；driver 要求占位 OPENAI_API_KEY（no_auth 场景未走 `omit_authorization_header` 路径，与 ollama WIP 的 build_provider 教训同源）；**driver 对 0 章/空 article 静默成功**，本次靠 stderr「0 章」才暴露 | driver.py:79-83（成稿合并无空值告警） | driver 成稿合并处加空产物 stderr 告警（三要素）；本地模型跑法（num_ctx/占位 key/派生模型）补进 eval/README |

## 正面验证（弱模型下依然成立的机制）

- **确认门拦截+引导成功**：`bible_confirmed` 前置拒绝（手法未锁定 0/1、章纲 0 条）后，模型按错误消息补 method_lock+3 条章纲再推进——三要素错误消息的引导闭环在 8B 上成立。
- **工具层 schema 校验可靠**：尽管重试 3 次，最终落盘的 outlines/method 数据全部干净合法；`alibi_claims.day` 二次调用传对了整数。
- **事件账本生效（F4 修复验证）**：`stories/c1v2-h/events.jsonl` 199KB，driver 装配点挂 EventStore 两端生效。
- **审计留痕完整**：连违规的 intake→bible_drafting advance 也进了 stage_history（reason/model 自述），事后可审计。
- **思考分离正确**：tau_ai 对 Ollama `reasoning` 字段处理干净，全程无 `<think>` 泄漏进正文/工具参数；PlainRenderer 零未渲染事件。
- **前端宽容解析泄压阀**：格式残缺时原样渲染不吞内容，符合「宁可暴露不要灵异」设计。

## 结论

换 8B 弱模型压测的价值在于把「模型自觉」从「系统机制」里剥离出来：

1. DeepSeek 走查里零违规的 intake 协议与正文落盘，本质是**强模型在替软协议兜底**——L1/L2 两个硬门缺口在强模型时代已存在，只是未被触发。按四透镜第②条（硬约束放工具层），L1/L2 是最优先立项项。
2. F1/F2 在弱模型下复现且恶化，验证了 A/B 组改进方向的普适性。
3. L7（状态注入刷新点只在 Web 链路）是本次新发现的真实 context 管理缺陷，与模型强弱无关。
4. harness 的账本结构、确认门、事件契约、审计留痕在 8B 下全部保持成立——结构化工具层是弱模型场景下最有韧性的部分。

**后续**：① L1/L2 立项（工具层硬门）；② L7 修复（刷新点共享）；③ F1 示例方案优先级上调；④ driver 空产物告警 + eval/README 补本地模型跑法；⑤ L1 修复后换新 slug 复跑 C1 双臂，补 CP2 盲评。

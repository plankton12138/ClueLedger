# 本地模型对照评测报告（qwen3-32k，C1 双臂，2026-10-12）

- **模型**：qwen3-32k（Ollama，由 `qwen3:8b` 派生、显式 `num_ctx 32768`）
- **接入**：`.env` 不动，进程级覆盖 `OPENAI_BASE_URL=http://localhost:11434/v1` + 占位 `OPENAI_API_KEY=ollama` + `OPENAI_TIMEOUT_SECONDS=600` + `HASTINGS_MODEL=qwen3-32k`
- **用例**：C1（斑点带子案基因，防污染委托书），slug `c1v4-h`（全新工作区）；双臂各 1 采样
- **产物**：本目录 `C1-h.log` / `C1-b.log` / `*.article.md` / `driver.py` / `spec.json`；harness 工作区 `stories/c1v4-h/`
- **目的**：同一委托书、同一 driver，仅换模型来源，量 harness 相对 bare 的净增益；对照 P10 加固（L1–L9）在弱模型下是否兑现

## 1. 总览

| 臂 | 结果 | 一句话结论 |
|---|---|---|
| bare | 成稿 **742 字**（《药瓶》） | 单轮直出完整故事，有侦探点破、凶器（药瓶）贯穿；但人物指代自相矛盾（同一叙述者忽为「被赶出家门的妹妹」忽为「妹妹的姐姐」）、凶手/动机揭示靠侦探一句话空降、篇幅偏短。无原书污染。 |
| harness | **0 章成稿**（article 0 字节） | 四轮全程卡在 **intake** 阶段：账本抢跑落盘、阶段推进两次被拒后不自愈，模型始终没走到 writing，轮次 4 以「手动修订同步」框架把正文直接打在回复里。 |

**本轮 harness 输给 bare**——harness 的账本/守门结构没换来任何成稿。这不是「harness 无用」，而是把两个系统级缺口在弱模型下逼了出来（H1/H3）。

## 2. 发现清单

### 程序性缺陷

| # | 发现 | 证据 | 归因层 | 建议 |
|---|---|---|---|---|
| **H1** | **intake 写账门在全新工作区恒为 inert（最重大）**：轮次 1 模型于 intake 阶段成功 `bible_upsert` 5 条（角色佐藤凛/佐藤健、线索 cl01/cl02、伏笔 fo01），P10 D1 硬门一次都没拦 | `C1-h.log` 轮次 1 五条 `[tool] bible_upsert` 无 `[tool error]`；`stories/c1v4-h/` 下 **无 story.yaml**，但 `bible/` 已落 2 角色+2 线索+1 伏笔 | 工具层：`reject_intake_write`（[bible_tools.py:326-337](file:///c:/Projects/Agent/myagent/src/hastings/tools/bible_tools.py#L326-L337)）以「story.yaml 不存在」判定旧工作区并放行；而 story.yaml **只在 story_stage 成功推进时**才落盘（[story_tools.py:134](file:///c:/Projects/Agent/myagent/src/hastings/tools/story_tools.py#L128-L134)），`ensure_dirs()` 不建它。全新工作区在首次成功 advance 之前没有 story.yaml → 与「旧工作区」不可区分 → 门被一并放行。**门恰好在它最该生效的首轮抢跑场景失效** | 不能仅以 story.yaml 存在性区分新旧：改为在工作区创建/首次 prompt 时显式落盘 `stage=intake` 的 story.yaml，或用「story.yaml 缺失 **且** 无任何账本文件」才判旧工作区。补一条集成测试（全新工作区首轮 bible_upsert 必被拒） |
| **H2** | **多步阶段机对弱模型不可达**：从 intake 唯一合法推进是 `→bible_drafting`，模型轮次 1 试 `bible_confirmed`、轮次 3 试 `writing`，两次均拒；错误消息已给出「怎样算对：从合法边中选：bible_drafting」，模型**不照抄、不重试**，四轮终态仍是 intake | `C1-h.log` 轮次 1/3 两条 `[tool error] story_stage ... 不是 intake 的合法去向`，其间无任何 `→bible_drafting` 调用 | 模型能力 + prompt：弱模型缺「顺序多步工具调用」规划，且对纠偏文案不执行。D4「照抄式纠偏」在此类**控制流纠偏**（而非参数格式纠偏）上失效 | ① 在 `story_stage` 的 advance 错误路径补「一次只能推进一步，本次请先 target='bible_drafting'」的**单值指令**；② 或评估「无 target 缺省即下一阶段」的默认值在弱模型下的可达性；③ 复跑观察 |
| **H3** | **正文直出守门漏报（三重原因叠加）**：轮次 4 模型把约 505 字正文（含「《暴风雨夜的铜刀》」式段落、手法定稿）直接输出在回复里，未落盘、无任何 prose_guard 告警 | `C1-h.log` 轮次 4；driver stderr 仅一条「0 章」告警，无 `[prose_guard]` 行 | 工具层：三道防线全部落空——① `ProseLeakGuard` 按 `stage ∈ (writing, revising)` 门控（[prose_guard.py:87](file:///c:/Projects/Agent/myagent/src/hastings/manuscript/prose_guard.py#L87)），本次卡在 intake **不在门控内**；② 阈值 600 字（[prose_guard.py:31](file:///c:/Projects/Agent/myagent/src/hastings/manuscript/prose_guard.py#L31)），本次正文约 505 字**未达阈**；③ 标题正则要求 `#` 或 `#《》`（[prose_guard.py:35-38](file:///c:/Projects/Agent/myagent/src/hastings/manuscript/prose_guard.py#L35-L38)），模型用 `**粗体**` 无 `#`**不匹配**。任一即漏报，三者同时命中 | ① 触发条件解耦于阶段（或至少纳入 intake/bible_* 的「长正文+章节特征」）；② 阈值/标题正则按弱模型实际排版放宽（粗体、纯文本标题）；③ `warn_empty_chapters` 同理解耦阶段门 |

### 提示词 / 协议缺陷

| # | 发现 | 证据 | 建议 |
|---|---|---|---|
| **H4** | **【确认】块在非按钮链路（CLI/driver）成死路**：轮次 1–3 模型反复输出 `【确认】` 块等待作者确认，无人点按；下一轮用户文本「确认建账，推进到写作阶段」被模型理解为「直接跳到 writing」，触发 H2 的跳段被拒 | `C1-h.log` 轮次 1/2/3 三处 `【确认】` 块；轮次 3 紧接 `story_stage → writing` 被拒 | `CONFIRM_MARKER` 是前端按钮协议；在无按钮链路（CLI/driver）应给模型替代指引（「若为文本输入链路，收到肯定回复后须自行调用 `story_stage` 推进，而非输出确认块」），否则确认块把弱模型钉死原地 |
| **H5** | **intake 三段式被跳过**（与首次压测 L2 同源，prompt 侧残留）：轮次 1 收构想即建账并试图 advance，未做「五维摘要→缺口清单→关键问题」 | `C1-h.log` 轮次 1 直接进工具调用 | 若 H1 修复（写账门生效），抢跑会被错误消息即时纠偏——prompt 顺序约束可与 H1 合并处理，不单独改文案 |

### context 管理

本轮未见新的 context 管理缺陷；P10 D6 刷新点共享已在 driver 链路接线（[driver.py:76-77](file:///c:/Projects/Agent/myagent/eval/driver.py#L76-L77) 每轮 `refresh_system_prompt` + `compact_context`），本轮转录无「陈旧阶段快照」的直接反证，但也无法从转录正面确认模型所见（留白）。

## 3. 正面验证（弱模型下仍成立的机制）

- **阶段迁移硬校验可靠**：两条非法推进（intake→bible_confirmed、intake→writing）均被拒，错误消息含合法去向——控制流硬门本身工作正常，问题在模型不采纳（H2）。
- **事件账本生效（F4）**：`stories/c1v4-h/events.jsonl` 93KB，装配点一处接线两端生效。
- **思考分离干净**：qwen3 的 `reasoning` 字段全程未泄漏进正文/工具参数（工具参数 schema 校验全过），PlainRenderer 零未渲染事件。
- **driver 可观测性（D7）**：0 章成稿 stderr 三要素告警正常抛出，本轮靠它第一时间定位到「harness 空稿」（ProseLeakGuard 静默，见 H3）。
- **bare 臂无原书污染**：委托书未含原书元素，bare 自创《药瓶》，未见斑点带子案标志性设定复述。

## 4. 结论

1. **剥离「模型自觉」后，瓶颈不在工具层硬约束，而在阶段机的可操作性**：P8/P10 立的一批硬门（写账门除外）在本轮几乎没被触发——因为弱模型**先卡死在上游的阶段推进**，根本没走到 writing。对弱模型，harness 的净增益取决于「模型能否完成顺序多步工具调用」，当前 8B 级做不到。
2. **H1 是真实且可修的 reachability 漏洞**：D1 门在全新工作区（首轮抢跑）恒不生效，恰是其设计目标场景；现有测试只覆盖 `intake_write_rejection` 纯函数（[test_story_state.py:122](file:///c:/Projects/Agent/myagent/tests/test_story_state.py#L119-L137)），缺「全新工作区 → bible_upsert 必被拒」的集成断言，属测试盲区。
3. **H3 揭示守门设计假设过强**：prose_guard 假设「模型已抵达 writing」，而失败点提前到 intake；且阈值/正则按强模型的 Markdown 习惯设定，弱模型排版（粗体/短正文）全部漏过。
4. **bare 交付、harness 空稿**：本轮 harness 相对 bare 为负增益。结论不是「bare 更好」，而是**在无多步规划能力的模型上，harness 的强结构反而抬高了到达终点的门槛**。

## 5. 后续（建议按序）

> 第 1–4 项中，H1/H3 已实施并换新 slug 复跑验证——见 [../2026-10-12-local-rerun/report-rerun.md](../2026-10-12-local-rerun/report-rerun.md)。

1. 修 H1：工作区显式落 `stage=intake` 的 story.yaml + 集成测试（全新工作区首轮写账必拒）。
2. 修 H3：prose_guard 触发与阶段解耦（或纳入 intake/bible_*）、放宽阈值与标题正则（粗体/纯文本）。
3. 修 H2/H4：`story_stage` advance 错误路径给单值指令（「本次请先 target='bible_drafting'」）；非按钮链路的确认推进指引。
4. 换新 slug 复跑 C1 双臂，验证 H1/H3 是否收口（期望 harness ≥1 章）。
5. 视情扩展 C2/C3 双臂（本轮全部成本用于定位系统缺口，尚未做 CP2 盲评）。
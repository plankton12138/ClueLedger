# 第二轮复跑报告（H2/H4 修复 + H6 发现·修复，C1 harness 臂，2026-10-12）

> 上游：首轮发现 [../2026-10-12-local/report-local-qwen3-v2.md](../2026-10-12-local/report-local-qwen3-v2.md)、H1/H3 复跑 [../2026-10-12-local-rerun/report-rerun.md](../2026-10-12-local-rerun/report-rerun.md)。
> 本报告记 H2/H4 修复后的 c1v6-h 复跑（发现 H6），以及 H6 修复后的 c1v7-h 复跑（部分验证）。

## 1. 本轮改动（H2/H4）

| 缺陷 | 改动 | 文件 |
|---|---|---|
| H2 跳段纠偏不可执行 | `_resolve_target` 越级被拒时改为**单值指令**——直接给出「本次请把 target 改为 X 后重试」，把「从合法边里挑」变成「照抄这一步」 | [story_tools.py](../../../src/hastings/tools/story_tools.py#L78-L92) |
| H4 确认块在非按钮链路成死路 | WORKFLOW_SECTION 追加「确认块不是终点：作者肯定回复后必须自己调用 story_stage 推进，不得重复输出确认块；被拒按错误消息改正后立即重试」 | [prompts.py](../../../src/hastings/prompts.py#L137-L142) |

## 2. c1v6-h 复跑（H2/H4 修复后）：发现 H6

**仍 0 章成稿**，但暴露了一个新的、可确证的守卫缺陷。

| 轮次 | 模型动作 | 工具结果 |
|---|---|---|
| 1 | 输出 `【选项】`×2 + `【确认】` | 无工具调用（停在 intake） |
| 2 | 连发 5 条 `bible_upsert`（角色/线索/伏笔/手法…） | **全部被拒**（`写入被拒：当前阶段是 intake`，H1 生效）→ 改输出 `【确认】`+`【选项】`，**未按错误消息的逃生门去 advance** |
| 3 | `story_stage advance target='writing'` → 被拒（H2 单值提示给出 `'bible_drafting'`）→ **重试** `story_stage advance`（缺省 target） | 重试被 **`本回合已推进过一次`** 拒绝 ← **H6** |
| 4 | `write_chapter 写入第1章` | 被拒（阶段仍是 intake） |

### 2.1 H6（新）：回合规速守卫把「被拒的 advance」也算作已推进

**现象**：轮次 3 里模型**先被工具拒绝**一次（越级 `target='writing'`），改正后的**合法重试**（缺省 → `bible_drafting`）反被守卫拦下，阶段卡死在 intake。H2 的单值提示已让模型**愿意重试**，却被守卫误拦——两个修复相互抵消。

**根因**：[advance_guard.py](../../../src/hastings/manuscript/advance_guard.py) 的 `before_tool_call` **先加后判**——任何一次 advance 调用（无论最终成功或被工具拒绝）都会消耗本回合的配额。语义应为「本回合成功落盘过一次」，却实现成了「本回合被调用过一次」。

**修复**：`before_tool_call` 只做阈值判定、不再计数；计数改由事件流驱动——订阅 `ToolExecutionStartEvent` 登记待判的 advance 调用（携 `args`），`ToolExecutionEndEvent` 按 `is_error` 决定是否计入（`is_error is False` 即工具真正落盘才 +1）。tau 的 loop 保证 start 事件先于 `before_tool_call` 派发给订阅者、end 事件携 `is_error`，零内核改动。

**测试**：`test_advance_guard.py` 改写 2 条（改为经事件对模拟成功落盘后才计入）+ 新增 2 条——`test_rejected_advance_does_not_consume_quota`（被拒不占配额）、`test_rejected_then_valid_advance_succeeds_in_same_prompt`（**复现 c1v6-h 轮次 3 的失败模式**：越级被拒 → 同回合改正重试必须放行）。**748 passed；ruff check/format/mypy 全绿。**

## 3. c1v7-h 复跑（H6 修复后）：阻断解除，但暴露协议预算问题

同一委托书换新 slug 再跑：

| 轮次 | 模型动作 | 工具结果 |
|---|---|---|
| 1 | 连发 6 条 `bible_upsert` | 全部被拒（H1 生效）→ 输出 `【选项】` |
| 2 | 重复输出 `【选项】` | 无工具调用 |
| 3 | `story_stage advance target='writing'` → 被拒（H2 单值提示） | 本轮未重试，改输出 `【确认】` 块 |
| 4 | **`story_stage advance → bible_drafting`** | **成功**（`story.yaml` 落盘：`intake → bible_drafting`）← **H6 修复生效** |

- **H6 验证 ✅**：c1v6-h 中同一回合的合法重试被守卫误拦；修复后 c1v7-h 的推进**成功落盘**（`stories/c1v7-h/story.yaml`），阶段从卡死变为前移一格。
- **H2/H4 部分生效**：单值提示让模型在**下一轮**照抄出 `bible_drafting`（跨轮重试），但同轮仍倾向先输出确认块而非立即重试。
- **仍 0 章**：轮次 4 完成推进后模型进入一段超长生成（>24 分钟未返回，触发本地 provider 超时），本次复跑被手动终止，转录止于轮次 4。

### 3.1 结构性观察：4 轮脚本 ×「每回合至多一次 advance」装不下阶段链

即使 H1–H6 全部打通，`intake → bible_drafting → bible_confirmed → writing` 需要**三**次推进；而回合规速守卫（P10 D3）规定**每个 prompt（=每轮）至多一次 advance**。C1 spec 只有 4 轮，轮次 1–2 通常被 intake 问答占满，仅剩 2 轮推进空间——**harness 臂在 4 轮内到达 `writing` 并成稿在结构上几乎没有余量**。这不是单个代码缺陷，而是**协议预算 vs 评测脚本长度**的错配：

- 真实交互中，每格推进对应一次作者回复（守卫的本意），轮数由作者掌握；
- 评测脚本把 4 轮压成「intake→确认→推进→成稿」，隐含要求模型在**同一轮里连推多格**，恰是守卫要拦的行为。

## 4. 结论

1. **H2/H4 修复方向正确但不足以让 harness 成稿**：H2 的单值提示确实提高了照抄率（跨轮），H4 的「确认块不是终点」未能让模型在收到肯定回复的同轮内立即调用 `story_stage`。
2. **H6 是这一轮最有价值的可确证缺陷**，已修复并经单测（含失败模式复现）+ c1v7-h 复跑双重验证：守卫从「数调用次数」纠正为「数成功落盘次数」，被拒的 advance 不再误吃配额。
3. **harness 臂 0 章的主导因素已从「代码缺陷」转为「协议预算」**：阶段门链 + 每回合一次推进的守卫，与 4 轮评测脚本不匹配。要么拉长脚本轮数、要么为评测显式放宽守卫（需设计决策）。
4. **对弱模型 harness 净增益仍为负**（bare 742 字成稿 vs harness 0 章）。

## 5. 建议后续

1. **协议预算（需决策）**：或把 C 组 harness 脚本从 4 轮扩到 6–8 轮（给每格推进留一轮），或在评测 profile 下放宽 `MAX_ADVANCES_PER_PROMPT`（如 3）——两者都会改变对照口径，宜先定原则再动手。
2. **H4 加强**：在非按钮链路（CLI/driver）对确认块后紧跟的肯定回复，尝试用**更强措辞**或**在工具错误消息里**提示「现在这一轮就调 story_stage，不要再问」，而不是仅靠 prompt 段。
3. **H5（intake 三段式跳过）**：轮次 1–2 模型均未走「五维摘要→缺口清单→关键问题」三段式，直接抢跑建账/给选项——属 prompt 遵循度问题，待评估是否值得再下沉。
4. **H3 端到端证据**仍缺（c1v5/c1v6/c1v7 均无正文直出触发 `ProseLeakGuard` 的完整落盘）。
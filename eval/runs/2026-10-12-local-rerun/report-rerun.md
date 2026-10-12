# 复跑验证报告（H1/H3 修复后，C1 harness 臂，2026-10-12）

> 上游发现见 [../2026-10-12-local/report-local-qwen3-v2.md](../2026-10-12-local/report-local-qwen3-v2.md)。本报告只记「修复 → 换新 slug 复跑」的验证结论。

## 1. 本轮改动（H1/H3）

| 缺陷 | 改动 | 文件 |
|---|---|---|
| H1 intake 写账门 reachability | `reject_intake_write` 改为：无 story.yaml **且账本为空** → 按 intake 拦；仅「无 story.yaml 但有账本内容」的旧工作区放行 | [bible_tools.py](../../../src/hastings/tools/bible_tools.py)（新增 `_has_ledger_content`） |
| H3 正文直出守门漏报 | `ProseLeakGuard` 去掉 writing/revising 阶段门控（任意阶段判定）；阈值 600→400；标题正则兼容整行粗体 `**标题**` | [prose_guard.py](../../../src/hastings/manuscript/prose_guard.py) |

**测试**：新增/改写 6 条——全新工作区建账必拒、旧工作区放行、任意阶段触发、整行粗体标题触发、行内粗体不误报、下调阈值捕获中等长度正文。连带修正 `test_tools_bible` / `test_tools_method` / `test_tools_outline` 三个 fixture（补种 `stage=bible_drafting` 的 story.yaml——原 fixture 依赖 H1 的漏洞才可写账）。**744 passed；ruff check All passed；ruff format 200 files；mypy 核心包 83 files Success。**

## 2. 复跑结果（slug `c1v5-h`，qwen3-32k，harness 臂，4 轮）

**仍 0 章成稿**，但失败模式已改变。

| 观察点 | 修复前（c1v4-h） | 修复后（c1v5-h） |
|---|---|---|
| intake 建账 | 5 条 `bible_upsert` 静默成功（抢跑落盘） | **8 条全部被拒**（`写入被拒：当前阶段是 intake`），账本为空 |
| 阶段推进 | 轮次 1/3 两次推进**被拒后不自愈**，四轮终态停在 intake | 轮次 1 推进 `intake→bible_drafting` **成功**（story.yaml 落盘）；轮次 3 推进 `→writing` 被拒 |
| 终态阶段 | intake（`story.yaml` 不存在） | **bible_drafting**（`story.yaml` 落盘，stage_history 有 1 条迁移） |
| 正文直出 | 轮次 4 直出约 505 字正文 | 轮次 4 仅一个 `【确认】` 块（约 90 字），**无正文直出** |

### 2.1 H1 验证 ✅

`stories/c1v5-h/story.yaml` 的 `stage_history` 记录了唯一一条 `intake → bible_drafting`，且 `bible/` 下**无任何账本文件**——证明：① 全新工作区的首轮建账被硬门拦下（修复前会静默落盘）；② 门的错误消息把模型引导到了正确的推进动作。H1 收口成立。

### 2.2 H3 验证 ⚠️ 单测通过，本轮未被端到端触发

本轮模型**没有**再直出正文（轮次 4 只输出确认块），故 `ProseLeakGuard` 未被真实触发。H3 的修复由单测覆盖（任意阶段触发 / 粗体标题 / 400 字阈值），但**缺少一次真实正文直出的端到端证据**——留白。

### 2.3 新观察：H2/H4 成为唯一阻断

修复 H1 后，上游瓶颈收敛到**阶段导航**与**确认块死循环**：

- **顺序推进不可达（H2）**：轮次 3 模型欲从 `bible_drafting` 直达 `writing`，被拒（合法边是 `bible_confirmed`），**不照抄纠偏、不重试**，之后三轮再无任何工具调用。
- **【确认】块死循环（H4）**：轮次 1–4 反复输出同一 `【确认】` 块等待作者点按，non-UI 链路无人响应，模型把「确认块」当成了「已完成推进」的替代物。

### 2.4 副作用：逃生门措辞被模型复述为推进理由

轮次 1 模型在无作者明确指令的情况下推进，`stage_history.reason` 记为**「作者已明确要求开始建账」**——正是门错误消息逃生门的原句（[story_state.py:135](../../../src/hastings/bible/story_state.py#L135)）。推进属控制面（不拦、留痕可审计）是既定设计，但**模型会把逃生门文案当成自我授权的理由**，使 `reason` 语义失真。属可观测性层面的新观察，不影响数据正确性。

## 3. 结论

1. **H1 修复经复跑验证成立**：intake 写账门从「恒为 inert」变为「首轮即生效」，且错误消息的引导把模型从「静默抢跑」推向了「正确 advance」——硬门 + 三要素错误消息的组合在 8B 上产生了正向引导。
2. **H3 修复由单测覆盖，端到端未触发**（本轮回合无正文直出）。
3. **harness 仍 0 章**：阻断点已从 H1（数据面）迁移到 H2/H4（控制面——阶段顺序推进 + 确认块）。这两个不是「数据正确性」缺陷，而是**弱模型的操作可达性**问题，超出 H1/H3 的修复范围。
4. **对弱模型，harness 的净增益仍为负**（bare 742 字成稿 vs harness 0 章），根因是阶段机对 8B 级模型不可操作。

## 4. 建议后续

1. **H2**：`story_stage` advance 被拒时给**单值指令**（「本次请先 `target='bible_drafting'`」），把「从合法边中选」这种枚举式纠偏改成「照抄式」；可加「一次只推进一步」提示。
2. **H4**：非按钮链路（CLI/driver）去掉/弱化 `CONFIRM_MARKER`，或补「文本链路收到肯定回复后须自行调用 `story_stage`」的替代指引。
3. 修毕后换新 slug 复跑 C1，期望 harness ≥1 章，再做 CP2 盲评。
4. 视情扩展 C2/C3。
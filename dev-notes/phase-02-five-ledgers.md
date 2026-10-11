# Phase 2 构建笔记：五账本全量与状态机

> 对应 [DESIGN.md](DESIGN.md) §8 路线图 **P2 Bible 完整化** 行；计划见 [phase-02-five-ledgers-plan.md](phase-02-five-ledgers-plan.md)。
> 状态：**已完成（R3 blocker 清零，R5 结论见 §7）**。执行记录：T1 `c7fb011` / T2 `58c17a7` / T3 `96819e9` / T4 `12aa63b` / T5 `e1917a5` / T6 `7b87e6d` / T7 `2d33003` / R3 修复 `49d84d9`。
> 关键词：五账本、状态机、手法锁定、时间线检测、泛型账本存储

## 1. 交付内容

- **五账本模型**（[models.py](../src/hastings/bible/models.py)）：`Character` 增量加 `arc` 弧光字段；新建 `Clue`、`Method`（含 `alibi_claims` 与 `history` 审计链）、`Foreshadow`、`TimelineEntry`。
- **状态机**（[state_machine.py](../src/hastings/bible/state_machine.py)）：`TRANSITIONS` 迁移表 + `INITIAL_STATUS` + `validate_transition` / `validate_initial`，错误消息三要素。
- **泛型账本存储**（[store.py](../src/hastings/bible/store.py)）：`load_ledger` / `save_ledger`（约束型 TypeVar），yaml 调用仍收敛本模块；缺文件视为空账本，P1 旧工作区兼容。
- **工具层守门**：`bible_upsert` / `bible_query` 注册表模式泛化五账本（[bible_tools.py](../src/hastings/tools/bible_tools.py)）；`method_lock` / `method_unlock` 与锁定拦截、history 审计链（[method_tools.py](../src/hastings/tools/method_tools.py)）；`timeline_check` 工具包装（[timeline_tools.py](../src/hastings/tools/timeline_tools.py)）。
- **时间线检测纯函数**（[timeline_check.py](../src/hastings/bible/timeline_check.py)）：R0 数据自检 / R1 人物分身 / R2 不在场破绽（D3 方案 A），账本快照进、冲突列表出。
- **CLI 与 prompt**：`/bible [type]` 直读命令（防崩 REPL）；工作流程节扩为六条（状态迁移 + 锁定语义一句话版）。
- **测试**：179 个（P1 末 165 → +14），新增 [test_state_machine.py](../tests/test_state_machine.py)、[test_timeline_check.py](../tests/test_timeline_check.py)、[test_tools_method.py](../tests/test_tools_method.py)、[test_tools_timeline.py](../tests/test_tools_timeline.py)，扩展 store / bible_tools / writing_loop（五账本全流程剧本）。

## 2. 关键决策（D1–D8 落锤）

| 决策 | 落锤情况 |
|---|---|
| D1 迁移表 | 按计划落地；`old == new` 视为 no-op 放行；`locked→drafted` 在表内登记但只归 `method_unlock` 工具可走（upsert 侧由 `_reject_locked_method_edit` 拦截） |
| D2 时间模型 | 结构化 `day: int` + 可选 `HH:MM`，相对时间由模型换算、原文进 `note`；检测是纯区间运算 |
| D3 不在场破绽（方案 A） | 落地。**实施期修订一处**：R0 追加拒绝零长区间（`t_end == t_start`），见 §4 |
| D4 method 部分加密 | 推迟到 P5，与 fair-play 泄漏检测统一设计 |
| D5 推迟项红线 | outline 账本化、段落级双向引用、write_chapter 矛盾拦截、foreshadow_audit 均未带入（R3 清单 #6 核对通过） |
| D6 依赖零新增 | pyproject / uv.lock 全程零 diff |
| D7 单文件账本 | `clues/foreshadows/method/timeline` 均为带 name 主键的列表；method 允许多条，锁定按 name；`method_lock` 参数加 `name/chapter/reason` |
| D8 跨账本校验 | timeline 参与者必须已建档，在 upsert 的 `cross_check` 钩子强制；其余跨账本引用 P2 不校验 |

## 3. Agent 设计经验（本阶段最重要的教训）

### 3.1 错误消息是喂给模型的 API——且不能描述不存在的现象

状态机错误消息严格三要素（哪里错：`old → new`；为什么错：无此边/无此状态；怎样算对：合法去向列表），模型收到后可以确定性地选下一条边，而不是试错。锁定拦截更进一步给了**两跳指引**：「改内容先 method_unlock；若已揭晓，用 bible_query 取全量、status 设 revealed 整体重交」——因为 upsert 是覆盖写，模型不知道"取回再交"这个协议，必须由错误消息教会它。

R3 #1 从反面印证了同一条原则：含 R0 数据错误的条目若继续参与 R1/R2，模型会收到**第二条与事实不符的消息**——把倒挂区间 (23:00→01:00 被解析成 1380..60) 与全天事件的"相交"当成真实的人物分身冲突来描述。喂给模型的每条消息都是它对世界的观测，描述幻觉现象比少报一条更糟：修正是修不动的，因为"错在哪"本身是假的。教训：**不变量写进了 docstring，就要有测试钉住它的每一条路径**（"格式非法→排除"有测试，"跨零点→排除"没有，于是实现漂移了三个提交才被 R3 抓住）。

### 3.2 硬约束放工具层，prompt 只负责解释世界

状态迁移校验只出现在两处工具执行函数里；`state_machine.py` 本身是纯数据表（规则的事实源），`store.py` 纯读写，prompt 的工作流程节只有一句「状态只能沿合法迁移边推进，且必须经工具完成」——解释世界为何有此约束，不重复枚举边表（枚举会随 D1 修订漂移）。锁定语义同理：prompt 一句话（"锁定后改任何字段都须先 method_unlock"），完整协议在 `method_lock` 的 guidelines 与拦截错误消息里，单一事实源。

一个值得记住的手法：`_reject_locked_method_edit` 把「缺省 status = 保持现状态」设计进拦截判定——模型只需回传 `status: revealed` + 原内容即可走 locked→revealed 这条唯一经 upsert 的出边，而 `locked→drafted` 一律拒（这条边在协议上只归 unlock 工具）。**约束收窄到工具后，覆盖写协议反而变得可教**。

### 3.3 泄压阀：三情形在 timeline_check 与 /bible 的落点

- **违规**（参数/数据不合法）：状态迁移拒绝、锁定拦截、参与者未建档——`is_error=True` + 修正指引，模型可自我修正；
- **合法空结果**：`bible_query` 查无 → 正常返回「不存在 + 两种下一步」（创建或列出全部），绝不报 error——防止模型陷入道歉-重试循环；`timeline_check` 无冲突 → 正常返回「时间线自洽（检查了 N 条事件、M 条主张）」，让"干净"也是一个可引用的正面结论；
- **环境故障**：`LedgerFormatError`（账本根节点非列表）显式暴露而非静默当空；`/bible` 直读入口 catch 全部读取异常转可读文本，唯一直读入口不崩 REPL（R3 #4 教训的制度化）。

### 3.4 可观测性是事件契约的一部分

- `Method.history` 把锁定/解锁（章节+理由+时间戳）随账本落盘：**状态为何变化对模型与读者同等可见**——审计链不是调试日志，是账本数据的一部分；
- `timeline_check` 结果同时服务两类读者：文本行喂模型（含三要素），`details` 留结构化数据给 P3 前端（`conflict_count` + 每条 `rule/day/persons/entries/message`）；
- 每个工具都有 `render_call` 单行摘要（"锁定手法 怀表调时诡计"），用户在终端获得对齐感而不被参数淹没。

## 4. 评审记录

六项关注清单（计划 §4-R3）全部通过：状态校验仅在工具层、错误消息三要素、`/bible` 防崩、details 结构化、D5 零带入；区间运算发现两处边界缺口，双验证者均确认（2/2 高置信），当轮修复：

1. **R0 命中条目仍参与 R1/R2**（blocker）：实现只对「时刻格式非法→`_interval` 返回 None」做了排除，`day<1` 与跨零点倒挂区间照常参与分身/不在场判定，与 docstring 声明的不变量矛盾，且会向模型描述不存在的重叠（§3.1）。修复：`check_timeline` 先收集 R0 命中再过滤；`_check_alibi` 改收（方法, 主张）配对。
2. **零长区间静默失效**（major）：`t_end == t_start` 通过 R0，半开区间判定恒 False，不在场破绽检测对该主张完全失效且无提示。修复：R0 追加零长分支（事件+主张两处），消息三要素。

修复提交 `49d84d9`（测试先行，+5 用例），四门复跑全绿。

## 5. 踩过的坑

- **upsert 全量覆盖 vs history 工具专属账目**（T4 设计张力）：upsert 是覆盖写，但 `history` 是 lock/unlock 工具的专属账目——覆盖写时无条件保留既有审计链，模型数据里的 `history` 键一律不采纳。推论：模型做 `revealed` 状态迁移必须**全量回传**字段（缺一个就丢一个），这一点只能靠错误消息与 guidelines 反复教。
- **声明的不变量没有测试就是没有不变量**（§3.1 R3 #1 的根因）：docstring 写了"含 R0 的条目不参与 R1/R2"，实现只在一种 R0 子情形成立。行为契约的每条路径都要有失败用例钉住。
- **零长区间**：半开区间运算下 `t_end == t_start` 是空集，所有相交判定静默为 False——对确定性守门人而言，"静默失效且无提示"比"报错"更危险，因为它放过了本该抓住的诡计。

## 6. 验证输出存档

四道质量门（`49d84d9` 后复跑）全绿：ruff/format 无告警，mypy strict（54 source files）零 issue，pytest 179 passed。架构红线（P2 全程 `c7fb011..49d84d9`）：tau_ai / tau_agent 与 pyproject.toml / uv.lock 零 diff；hastings 零 import tau_coding（仅 cli.py docstring 提及设计来源）。

## 7. DoD 对照（计划 §5）

- [x] 依赖零新增；`uv.lock` 无实质变化
- [x] 五账本模型 + 状态机单测全绿；非法迁移错误消息三要素齐全
- [x] method_lock/unlock 全链路测试通过；history 记录锁定/解锁
- [x] timeline_check 逐规则命中构造用例；干净账本零误报
- [x] bible_upsert/query 五账本泛化；查无结果返回引导性提示（非 error）
- [x] P1 存量工作区向后兼容（缺省账本=空，arc 缺省 flat）
- [x] 集成测试三层断言全过（含 method history 与伏笔状态落盘）
- [x] prompt/CLI 更新；`/bible` 直读防崩
- [x] 四道质量门全绿；mypy strict 无新增豁免
- [x] 红线：tau_ai/tau_agent diff 为空；零 import tau_coding；依赖零新增
- [ ] 真实端点人工验收 + transcript 存档 → **按 T8-3 记留白**（.env 就绪，随时可补跑：`uv run python -m hastings p2-acceptance`，剧本照计划 T6 九步）
- [x] R3 评审完成，blocker 清零
- [x] `dev-notes/phase-02-five-ledgers.md` 构建笔记发布

**R5 结论**：13 条 DoD 通过 12 条，唯一未勾项为真实端点人工验收留白（有明确补跑路径，不阻塞阶段关闭）。P2 关闭，允许进入 P3。

## 8. 已知留白（转入后续阶段）

- `timeline_tools._parse_chapter_param` 接受 `ch0`（章节号 0）作为合法参数——过滤语义下无害（只是查不到条目），低优先；
- 零长区间 R0 拒收是对计划 D3/T5 规则集的实施期扩展，已在 §2 记录，若后续对规则集有更多修订应同步回计划文档；
- 真实端点人工验收 transcript 待补（见 §7 留白项）。

# V06-D1 约束加固与界面收口

> 日期：2026-10-22。状态：已实施，等待新增浏览器流程与真实模型验收。

## 目标

C2 交付后复核发现三处约束可以被绕过：固定修改范围可以重新登记放宽、作者选中的必读参考
可以被模型降为可选、检查证据可以引用没有提供给模型的区间。本阶段修掉这三处，并补齐
C2 遗留的界面遗漏与检查阶段的回合预算。

## 实际改动

**修改范围（`workspace/task_scope.py`）**

- 新增重复登记校验：再次 `set_task_scope` 只能收窄——不能新增允许操作、不能撤掉已登记的
  禁止项、不能把固定范围改成可追加范围、不能放宽可追加的操作类别。改成只读时不要求保留
  禁止项（只读范围没有写权限），但重新登记回修改范围仍受「不新增操作」限制。
- 扩大范围仍只有 `extend_task_scope` 一条路：要求可发现范围、操作类别白名单与关联证据。
- 追加的证据从「一段非空文字」改为「依据文件 + 原文引文」（`evidence_path` + `evidence`）：
  工具入口用本轮读取记录核对这句原文确实在提供给模型的区间里，凭印象写的理由不能追加。
  事件里记下依据文件与引文，回看能看出这次追加读的是什么。

**参考材料（`workspace/materials.py`）**

- `MaterialLedger.register` 改为合并：同一路径的 `required` 只保持或加严，`author_selected`
  类别保持，作者来源的引文与事件定位优先。模型再登记同一份文件只能补充说明。
- `register_references` 的「发现的材料」也加区间判断：引文必须落在本轮实际提供过的区间里，
  读过文件开头不能拿后面一段当关联证据。

**检查证据（`workspace/review.py`）**

- 证据校验增加区间覆盖判断：引用的 `[start, end)` 必须完整落在本轮实际提供过的某个区间内。
  「这个版本读过」不再足以支撑任意段落的引文。
- `materials.covers` 抽出为公共判断，写入前提的局部替换检查与检查证据共用同一规则。

**检查阶段（`workspace/service.py`）**

- 回合预算跨阶段共享：写作阶段用掉的模型回合从检查阶段扣除；剩余为 0 时不进入检查，
  `review_finished` 记为 `incomplete` 并写明原因，整轮状态为部分完成。回合数来自
  `TurnStartEvent`，与模型循环自己计数的口径一致。
- 普通工具清单里重复注册的 `request_review` 去掉，模型只看到一个同名工具。

**界面（`frontend/`）**

- 目录每行右侧新增「＋」：把该文件加入当前草稿的参考，与输入区「添加文件」共用同一份
  草稿状态，重复点击不重复登记，不打乱选择顺序。
- 发送受理后清空草稿的条件加上参考比对：文字仍是提交文本、参考仍是提交的那一份时才清；
  受理期间新选的参考留在草稿里，等下一次发送。
- 检查报告卡按当前磁盘核对 `freshness`：过期或来源缺失时标注「已过期」并说明旧结论不再
  对应当前版本，同时不再显示「没有发现问题」的结论文案。

## 接口与数据影响

- 没有新增或删除接口，错误码沿用既有集合（重复登记用 `TASK_SCOPE_DENIED_CONFLICT`，
  证据区间不成立用 `REVIEW_EVIDENCE_INVALID`），前端不依赖新增字段。
- 会话事件、`reviews.json`、`conversation-ui.json` 的结构不变。

## 实现取舍

- **只收窄而不禁止重复登记**：模型经常在读到新内容后重述任务理解，直接禁止会打断正常流程；
  收窄是无害的，放宽才有绕过风险。
- **代价是模型必须一次登记完整**：先登记窄范围、再想补充的模型会被拒绝，只能走 `extend`
  或请作者重新提要求；这是不让重新登记变成绕过路径的必要代价，错误提示里写明了两条出路。
- **改只读时允许丢禁止项**：只读范围没有写权限，禁止项失去意义；从只读回到修改仍要过
  「不新增操作」，所以放宽路径没有打开。
- **预算用尽就明确不检查**：剩余回合不足以完成「读原文 + 出报告」时，报未完成比拿一两回合
  硬跑更容易判断报告是否可信。
- **过期提示只读核对**：回看报告不重跑模型，只用当前指纹判断版本是否还对应。

## 验证证据

2026-10-22，本次工作区：

| 检查 | 结果 |
|---|---|
| `uv run pytest -q` | 446 通过（C2 交付时 432，本阶段新增 14） |
| `uv run ruff check src tests` | 通过 |
| `uv run mypy` | 78 个源文件通过 |
| `npm test`（frontend） | 11 个文件、104 项通过（新增 2 项） |
| `npm run build`（frontend） | 通过 |
| `npx playwright test --output=<数据目录>`（端口 8931） | 16 项通过（新增「目录里的加入对话把文件放进本轮参考」1 项） |

新增后端用例：`test_reregistration_cannot_widen_the_scope`、
`test_reregistration_cannot_drop_denied_paths`、
`test_reregistration_cannot_switch_a_fixed_scope_to_extendable`、
`test_reregistration_cannot_widen_extend_actions`、`test_reregistration_can_narrow_the_scope`、
`test_tool_reregistration_cannot_bypass_a_fixed_scope`、
`test_model_cannot_downgrade_an_author_selected_reference`、
`test_register_merges_duplicate_paths_without_downgrading`、
`test_discovered_reference_must_quote_a_read_range`、
`test_extend_requires_evidence_from_a_read_range`、
`test_evidence_must_lie_inside_the_provided_range`、
`test_evidence_span_across_a_read_gap_is_rejected`、
`test_evidence_inside_a_partial_read_passes`、`test_check_phase_shares_the_turn_budget`。
前端新增：`目录里加入对话只追加参考，不重复也不打乱顺序`、`发送后不清掉受理期间新选的参考`。

说明：本环境中 `npm run test:e2e` 的默认输出目录清理会被批量删除保护拦下，改用
`--output` 指向数据目录后运行；用例本身没有改动断言范围。

## 偏差与未完成项

- 浏览器验收与七类真实模型任务不在本阶段范围内，由 [V06-D2 在线验收](V06-D2-acceptance.md)
  承接；本阶段只保证受控 provider 下的规则与接线。

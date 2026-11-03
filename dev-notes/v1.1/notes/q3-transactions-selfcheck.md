# Q3 实施记录：事务同步与感知区分、结构提案与写后自查（M4+M5）

日期：2026-11-03。阶段：Q3 完成。前置：Q0（契约冻结）、Q1（起步与结构层）、Q2（注入装配）。
契约依据：[q0-contracts.md](../../notes/q0-contracts.md) 第 2、3、5、6、7 节。

## 切片与提交

### 补遗（c49d7e63）：Q1-E 前端遗留补提交

核对发现 Q1 切片六（2631bf43）提交信息声称了前端改动，但 `api/structure.ts`、
`StructureView.tsx`、`WritingLayout/WritingNavigator` 与 e2e 适配实际未入提交。
本提交补齐（tsc 无错误 + 301 tests 通过）；同时并入 v1.0.9 的模块阅读让位记录
（`notes/2026-11-02-module-reading.md` 与 README 链接）。

### Q3-A（1e97c34f）：台账随事务自动维护

- `story_store.py`：`_update_ledger` 挂在 `_persist_locked`（提交）与 `_recover_locked`
  （恢复重放）两处——正文补丁应用成功后按正文路径章号登记（`record_chapter`）或回退
  （`remove_chapter`，`after == "absent"`）。恢复路径重放同一调用，登记幂等。
- **关键修正——骨架不算「已写」**：P6 起建章即建正文（骨架 `# 标题` 也是正文文件），
  若按「写了正文文件」登记，台账永远全绿、对账缺章检测失效。判定规则：标题行以外
  还有非空内容才算「真的有正文」；章节被改回骨架时撤除登记。台账不变量：
  **chapters == 有真实正文的章**。
- 备选方案（variant != main）不带登记；写失败按 `OSError` 与正文写入同一失败语义。
- 测试 `test_ledger_transactions.py` 5 例：提交登记、撤销回退、重复入档覆盖、恢复重放
  幂等、改回骨架撤除。

### Q3-B（0ef3972b）：原子性兜底

- `service.py`：`_expected_prose_missing` + `_turn_had_file_writes`，挂在回合收尾
  （`_finalize_story_candidate` 之后、目标评估之前）。命中即抛
  `EXPECTED_PROSE_MISSING`，由既有 `WorkspaceError` 收尾路径落成失败归因事件。
- 触发条件全部来自任务元数据与事件收据，不解析对话原话：写作用途回合
  （write/revise）+ 章节目标在任务元数据里（打开的正文路径/章节焦点，与 Q2-C
  对账同一来源）+ 本轮有结构/模块写入收据 + 无正文候选文档。纯讨论回复
  （没有写入）与已记录问题（needs_input）不算失败——只针对试验复现的
  「只交文件更新不交正文」形态。
- 直接落盘的结构更新不回滚（与取消暂存裁决一致）：失败归因 + Git 可回退 + 回执可见。
- 测试 `test_atomic_prose_guard.py` 7 例。

### Q3-C（977406cd）：感知区分

- 新 `workspace/perception.py`（纯函数）：`UpdateNote`（label/category/note/affected）、
  `validate_update_notes`（计划性变化必须带说明、不重复标注）、`unannotated_labels`
  （没标注到的变更如实列出，不替模型补分类、不阻断入档——标注是感知层不是授权层）、
  `planned_change_lines`（计划性变化单独一行：改了什么 + 影响哪里 + 基于哪个修订，
  行尾附「不同意，改回来」；记账返回空 = 静默）、`receipt_rows`（全部标注进回执）。
- `update_story` 工具新增 `updates` 参数：入档前校验（未知分类/缺说明直接拒绝
  `STORY_CHANGE_INVALID`）；提交后事件 `story_changes_committed` 携带 `updates`
  （记账仅此处可见），计划性变化另发 `structure_updates_noted` 事件（对话回显行）。
- 提示词：`UPDATE_STORY_GUIDELINES` 补分类要求；`UPDATE_STORY_UPDATES_PARAM_DESCRIPTION`。
- 前端回显卡渲染（M4 主要落点里的「回显卡支持结构/模块差异段」）**未做**，
  如实登记到未覆盖项。
- 测试 `test_perception.py` 9 例。

### Q3-D（f1fd52cf）：写后自查三层与强制对账流程

- 新 `prompts/self_check.py`（版本化，`SELF_CHECK_TEMPLATE_VERSION = 1`）：
  系统提示含强制对账流程（第一步核对目标章与台账，更早缺章不得写目标章、改为补缺）；
  用户提示注入铁律/结构全文（第 1—3 层）、台账事实与对账结论、必发生清单要求；
  `split_self_check` 确定性拆分正文与自查段（取最后一个 `### 自查` 标记，三层
  必发生/铁律/读者任一缺内容即按缺失处理）；`self_check_error` →
  `SELF_CHECK_MISSING_CONFLICT`。
- `creative_assist.py`：`prose_draft` / `prose_continue` 提示词改走模板，
  `_drafting_injection` 与对话装配层用同一份台账事实做对账（主防线在装配层，
  提示词强制流程是第二道保险）；生成完成后解析自查段——缺失按失败归因
  （候选 status=failed，正文文本保留可见），齐全则 `self_check` 行随候选落盘。
- `creative_candidates.py`：候选模型新增 `self_check` 字段。
- 自查如实标注「模型自检，非系统校验」；不承诺无漏报（提示词与模板均写明）。
- 测试 `test_self_check.py` 8 例 + `test_creative_assist.py` 2 例新增
  （缺自查确定性失败、强制对账提示词）；既有采用测试的假模型回复补自查段。
- 修改确认后的复检（M5「采纳建议→随事务入档并复检」）**未做**，登记到未覆盖项。

### Q3-E（3e4db511）：建章提案与标题候选

- `agent.py` 新 `create_chapter` 工具：走既有建章事务（`writing_actions.create_chapter`，
  章节计划 + 正文路径 + 骨架一次提交，失败回滚不留半登记）。
- 提议-确认门（确定性）：作者本轮原话必须同时含建章意图与确认
  （`_chapter_creation_confirmed`，关键词判定与 `task_intent` 同一惯例），
  且工具 `confirmation` 引文必须出现在作者原话里（否则
  `STORY_AUTHORIZATION_CONFLICT`，与授权引文同一规则）；未确认 →
  `CHAPTER_CONFIRMATION_REQUIRED`。标题可留空（按章序默认命名）。
- 请求键幂等（事务层既有能力），骨架不入台账（Q3-A 规则）。
- 测试 `test_chapter_proposal.py` 5 例（真实工具链驱动）。

## 验证

- 定向：Q3 各切片测试全过（5+7+9+10+5 例）+ 相关链路回归
  （chat / story_tools / chapter_writing / creative_assist / story_store）。
- ruff 全过；mypy 新改文件全过（`creative_assist.py` 两处既有瑕疵不动，
  stash 验证与本阶段无关）。
- 全量 `tests/workspace`：**1583 例，0 errors，1 failure**——`test_activity` 把关用例
  发现 `create_chapter` 缺可读动词（把关测试按设计生效），补动词后 `test_activity`
  27 passed；其余 1582 例全部通过（junitxml 权威统计，较 Q2 +36）。

## 未覆盖项（如实登记）

1. 前端回显卡的结构/模块差异段与计划性变化行渲染（事件与回显文本已就绪）。
2. 自查建议采纳后的复检交互（复检仅对改动项）。
3. `write_structure`（改铁律）的感知标注参数——机制在 `update_story` 验证，
   结构文件写入侧未接。
4. 台账端到端在线验证与对抗用例（UC-D 对抗项）随 Q4。

# Q2 实施记录：注入装配与护栏（完成）

日期：2026-11-03。阶段：Q2（M3）。契约依据：[q0-contracts.md](q0-contracts.md) 第 3、4 节。

## 已交付切片

### 切片一（Q2-A，39a6c5b2）：`workspace/structure_assembly.py` 纯函数层

1. `split_structure_layers` / `structure_section_text`：按铁律段切出第 1—3 层注入文本
   （铁律 → 结构全文 → STYLE 空占位）；标记丢失降级全文注入并如实标注（不猜边界、
   不静默修复）。空结构（起步前）返回空文本——STYLE 占位不单独注入起步前作品。
2. `ledger_section`：第 5 层台账注入——每章登记行（章号 + 短修订号）+ 优先级声明
   （台账与结构「已写」标记冲突以台账为准）。空台账返回空文本（分区按空跳过）。
3. `reconcile_note`：对账结论注入说明——`redirect` 写明改派章与禁止章；作者确认
   跳章时记录被跳章；放行且无跳章不注入（事实已在台账里）。
4. `chapter_number_of`：从正文路径解析章号（复用 Q0 约定，非章节路径返回 `None`）。
5. `loose_keyword_check` / `LooseCheckRow`：必发生清单写后宽松机检——折叠空白与
   大小写后按包含匹配，只报命中与缺漏（相对偏差），不判失败、不阻断。
6. `StructureSnapshot`：装配快照（注入层、铁律条数/降级、台账章、对账决策、按需
   选中模块、清单机检行），`to_dict()` 随 `state_injected` 事件进请求回执。
7. 测试 17 例（`tests/workspace/test_structure_assembly.py`）。

### 切片二（Q2-B，85948634）：装配六层接线

1. `assembly.py`：新增 `STRUCTURE_INJECT_BUDGET = 2_400`（渲染后分区上限，结构
   文件本身冻结 1600）与 `LEDGER_INJECT_BUDGET = 1_200`，并入 `PROFILE_BUDGETS`
   （writer 合计 61,900 → 65,500，注释同步）。
2. `context.py`：`build_assembled_context` 新增 `structure` / `ledger` 参数与分区——
   `structure` 在 catalog 之后、modules 之前（契约第 1—3 层先于第 4 层），
   `ledger` 在 story 之后（第 5 层晚于第 4 层）。structure 分区 `required=True`
   （常驻不裁；真装不下按装配故障报缺口）。写入与检查两种用途都装。
3. `prompts/sections.py`：分区标题 `structure`→「全书结构」、`ledger`→「正文台账」；
   台账注入文本不再自带小节头（标题由分区统一提供，避免重复）。
4. `agent.py` `build_turn_assembly` 透传两个新参数。
5. 测试：`tests/workspace/test_context_structure.py` 4 例（顺序、常驻、空跳过、
   台账行）；`test_context_budget.py` 预算合计断言更新。

### 切片三（Q2-C，1385d7be）：服务层确定性对账与快照回执

1. `service.py` `_structure_injection`：读结构状态（`read_structure_state`）与台账
   （`load_ledger(workspace.root / prose-ledger.json)`），产出注入文本与快照；
   目标章已知时执行 `reconcile`，改派说明并入台账分区文本。
2. `_metadata_target_chapter`：目标章**只来自任务元数据**——当前打开的正文路径或
   章节/选段焦点；对话原话说「写第 3 章」不算（不从自由格式解析），那道保险由
   M5 起草提示词强制对账承担。
3. 装配点（`_run`）与修改后检查阶段（review）都注入结构层；`state_injected` 事件
   新增 `structure` 块（快照）。检查阶段不对账（检查不改写目标章）。
4. 跳章参数（`allow_skip`）未从元数据接线——跳章只能由作者明确确认（M5 承接），
   测试登记这一约束。
5. 测试 10 例（`tests/workspace/test_service_structure_injection.py`）：无结构无台账
   无目标注入空、结构与降级快照、台账事实与优先级、缺章改派（目标第 3 章 →
   改派第 2 章，因台账只登记第 1 章）、齐章放行、改既有章不拦、焦点章路径、
   非章节路径不对账、跳章默认不放行。

### 切片四（Q2-D，12be1147）：模块按需选择

1. `retrieval.py` `relevant_module_note`：按目标章**确定性**筛选相关模块条目——
   线索看「埋设章 ≤ 目标 ≤ 回收章」（`planned_plant_chapter` / 
   `planned_resolve_chapter`），其余类型看 `planned_chapter == 目标`。扫描上限
   `SELECTION_SCAN_LIMIT = 300`（超限如实说明没扫完）、结果上限 8 条；命中只是
   定位行（不等于读过，`read_record` 读取原文），叠加在模块索引分区之后。
2. `service.py` 装配点：目标章已知（含对账改派后的章）时叠加「本轮相关」段并把
   命中模块 id 记入快照 `modules_selected`；目标未知时模块分区保持纯索引
   （v0.8 现状），由模型在会话内按需检索。
3. 测试：`test_retrieval.py` 4 例（覆盖区间、精确匹配、无命中、无库/无绑定）。

## 基线差异登记

- 结构分区预算（2,400）是渲染后分区上限，与结构文件冻结预算（1,600）是两回事；
  前者含小节标题与降级标注，后者约束文件本身——契约未变更，只是装配侧的余量。
- 台账注入需要台账文件存在于作品根目录；台账写入在 M4（事务层）实现，Q2 期间
  台账为空 → 分区按空跳过，行为与「起步前无台账」一致，不算缺依赖。
- 模块「按需选择」的判据用条目计划字段（planned_chapter 族），这是模块预设的
  既有字段（presets.py allowed_fields），无需模块机制改动（M 计划「模块机制零改动」
  保持成立）。
- writer 总预算 61,900 → 65,500 字符；`test_context_budget.py` 断言同步。

## 未覆盖项（Q2 剩余 / 归入后续阶段）

- 必发生清单的**模型自列**与回执展示依赖起草提示词（M5 起草任务扩展）；
  Q2 交付了快照字段与宽松机检纯函数。
- 台账注入的端到端验证（真实入档事务写入台账后注入）随 M4 事务同步交付。
- 对抗用例（风格指令挤压剧情、伪造「已写」标记）在线验证归 Q4。

## 验证

- 切片一 17 passed；切片二定向 58 passed；切片三 10 passed + `test_chat.py` 回归
  53 passed；切片四 15 passed；ruff / mypy 通过。
- 全量 `tests/workspace` 回归：**1547 passed / 0 failed / 0 errors**（2026-11-03，
  junitxml 统计；1512 → 1547）。注：本轮会话环境的临时目录清理钩子会在 pytest
  收尾清理时拦截并吞掉终端摘要行（exit 1），测试结果以 junitxml 为准，与代码无关。

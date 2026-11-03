# Q1 实施记录：起步链路与结构文件层（进行中）

日期：2026-11-03。阶段：Q1（M1、M2）。契约依据：[q0-contracts.md](q0-contracts.md)。

## 已交付切片

### 切片一：起步纯逻辑与设定卡确认端点（face6198）

1. `workspace/book_startup.py`：
   - `card_report`：设定卡候选文本确定性检查（非空、`CARD_TEXT_LIMIT=2000`）。
   - `confirm_startup_book_intent`：**一次性门槛**——已有非空意图拒绝
     （`BOOK_INTENT_CONFIRMED_CONFLICT`，不覆盖）；旧格式作品拒绝
     （`STORY_FORMAT_UNSUPPORTED`）；通过后复用 `save_book_intent` 联合提交
     （首次 `expected="empty"`，request_key 幂等，`store.undo` 可整体撤销）。
   - `guided_reply_report`：引导回合宽松机检（问题计数，一次一问偏差报告，不拦截）。
   - `structure_draft_prompt`：起步结构起草提示词（`STRUCTURE_PROMPT_VERSION=1`），
     输出《STRUCTURE.md》全文（铁律/路线/骨架/模块索引，预算 1600）+ `===MODULES===`
     分隔的首批模块清单；分层判据入提示。
   - `check_first_structure`：铁律标记在开头 + 预算内的确定性检查。
2. `POST /story/{id}/startup/confirm-intent`（`StartupConfirmRequest`）。
3. 测试 11 例（`tests/workspace/test_book_startup.py`）。

### 切片二：结构文件层读写门禁（273d0746）

1. `files.py`：受管 kind 新增 `"structure"`，根级 `STRUCTURE.md` 过受管路径校验
   （`STRUCTURE_PATH` 常量；既有消费方均按具体 kind 比较，无行为影响）。
2. `workspace/structure_io.py`：
   - `read_structure_state`：缺失视为空（digest `None`，写入按创建处理）。
   - `write_structure_document`：预算超限**写入时**拒绝（`STRUCTURE_BUDGET_CONFLICT`，
     压缩版即一次预算内写入，等效契约的「入档前先压缩」且磁盘不留超限文件）；
     首次起草要求铁律段开头（`STRUCTURE_MARKER_REQUIRED`）；更新允许标记丢失如实
     落盘（降级注入是装配层的事）。
3. 测试 8 例（`tests/workspace/test_structure_io.py`）；结构写入不触发全书构思门禁。

## 基线差异登记（对照计划第 2 节）

- `book_intent` 数据结构、`save_book_intent` 联合提交、撤销链路在 v1.0.9 已存在，
  起步确认直接复用，未新增事务类型——与计划一致（「复用既有联合提交事务」）。
- 根级 `STRUCTURE.md` 原不在受管路径模型内（受管路径要求两层目录），已扩展
  `managed_kind`，Q0 契约「作品根目录」不变。
- 预算执行点从「入档事务」前移到「结构写入工具」，语义等效（见切片二第 2 条）。

## 未覆盖项（Q1 剩余）

- **agent.py 工具接线**：`read_structure` / `write_structure` 挂入会话工具列表，
  与起步任务意图的授权范围（结构写入授权）一起做——需要先读 `task_scope` 的授权
  语义，避免把结构写授权漏给普通写作任务。
- **起步会话链路**：起步任务意图（引导/自由发挥）进会话受理与提示装配；设定卡候选
  由模型在对话中产出；确认后同链路起草第一版结构 + 首批模块入档（Q1-D）。
- **结构只读页**（前端）与侧栏入口（Q1-E）。
- 在线冒烟：起步对话到首批模块入档全链路（依赖会话链路完成）。

## 验证

- `uv run pytest tests/workspace/test_book_startup.py tests/workspace/test_structure_io.py
  tests/workspace/test_structure_document.py tests/workspace/test_prose_ledger.py -q`：28 passed。
- ruff / mypy（新模块与改动文件）：通过。
- 全量 `tests/workspace` 回归：**1495 passed / 0 failed**（2026-11-03，切片二提交后复跑）。

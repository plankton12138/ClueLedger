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

### 切片三：结构文档工具接线（7426ec70）

1. `agent.py`：`read_structure` / `write_structure` 两个工具闭包与注册。
   **授权与正文同一套任务范围**——模型须用 `set_task_scope` 登记 `STRUCTURE.md`
   的确切路径（`is_managed_path` 已认），无特殊通道；更新前强制读取前提
   （`ReadRequirement.full`）；直接落盘策略，随作品 Git 记录可回退。
2. `prompts/tools.py`：工具描述与指南（含「台账不进结构文件」提示）。
3. `activity.py`：新工具可读动词（读取/写入全书结构）——既有
   `test_every_writing_tool_has_a_readable_verb` 自动把关。
4. 测试 5 例（`tests/workspace/test_agent_structure_tools.py`）：缺失为空、
   未登记范围拒绝、创建与更新往返、创建门槛、预算拒绝。

### 切片四：起步会话双模式提示词（241cfcbe）

- `startup_conversation_prompt(mode)`：引导模式的约束全部落在提示层
  （一次一问、上限 `STARTUP_QUESTION_LIMIT=6`、每问可跳过、随时「就这样开始」、
  未知保持未知、不追问内部属性清单）；自由发挥不追问；两种模式都以设定卡候选
  收束、只描述起点与悬念。`STARTUP_PROMPT_VERSION=1`。

## 基线差异登记（对照计划第 2 节）

- `book_intent` 数据结构、`save_book_intent` 联合提交、撤销链路在 v1.0.9 已存在，
  起步确认直接复用，未新增事务类型——与计划一致（「复用既有联合提交事务」）。
- 根级 `STRUCTURE.md` 原不在受管路径模型内（受管路径要求两层目录），已扩展
  `managed_kind`，Q0 契约「作品根目录」不变。
- 预算执行点从「入档事务」前移到「结构写入工具」，语义等效（见切片二第 2 条）。

## 未覆盖项（Q1 剩余）

- **起步会话链路接线**：起步任务意图进会话受理与材料装配（`service.py` / `materials.py`），
  把 `startup_conversation_prompt` 接入系统提示；设定卡候选在对话中产出后由前端调
  已有的确认端点；确认后同链路调 `structure_draft_prompt` 起草第一版结构 + 首批模块
  （模型用已接线的 `write_structure` + 模块创建机制登记授权后落盘）。
- **结构只读页**（前端）与侧栏入口。
- 在线冒烟：起步对话到首批模块入档全链路。

## 验证

- 切片一/二定向测试：28 passed；切片三 agent 相关 5 文件 88 passed（含活动流动词把关）。
- ruff / mypy（全部新模块与改动文件）：通过。
- 全量 `tests/workspace` 回归：**1500 passed / 0 failed**（2026-11-03，切片三提交后复跑）。

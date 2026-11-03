# Q0 基线核对与首批交付（实施记录）

日期：2026-11-03。阶段：Q0（部分完成，见「未覆盖项」）。

## 版本号

- 全仓版本 `1.0.8` → `1.0.9`：v1.0.9 的功能提交已交付（先构思写作工作台、实时信息索引、
  撤章节构思门槛、P7 验收用例），版本号此前未跟进。改动五处：
  `pyproject.toml`、`src/hastings/__init__.py`、`frontend/package.json`、
  `frontend/package-lock.json`（两处）、`uv.lock`，并同步 `tests/workspace/test_release_recovery.py`
  的版本断言。`test_release_recovery` 通过。
- 1.1.0 / 1.2.0 仍留给各自交付门槛（v1.2 计划 R 阶段统一 1.2.0）。

## 基线核对结论（v1.2 计划第 2 节）

- **v1.1（全对话创作 P0—P5）未实施**。这影响 v1.2 的 M3（三元事务复用 v1.1 暂存闭环）
  与前端只读页（复用 v1.1 只读渲染）。差异登记如上，不强行开工这两块。
- `story_changes.py` / `story_candidate.py` / `chapter_writing.py` 已存在，但属于 v1.0.x
  线索与构思工作台形态，不是 v1.1 计划的暂存闭环；Q1—Q3 动到它们之前先逐个重读，
  不假设其行为与 v1.1 基线表一致。
- 版本断言、健康检查未发现其他版本引用点。

## 本阶段交付（关键实现顺序第 1、5 步的纯函数部分）

1. `src/hastings/workspace/structure_document.py`：结构文档定位、铁律段切分
  （标记必须在首个非空行，丢失降级全文注入）、内容指纹（与 `files.py` 同算法）、
  字数预算检查（冻结上限 1600 字符）。
2. `src/hastings/workspace/prose_ledger.py`：正文台账（事实层，模型只读不可写）、
   修订登记 / 整体撤销回退、原子落盘（专用 `.lock`，Windows 下不能锁数据文件本身
   再替换——已按 `creative_candidates` 惯例处理）、**确定性对账**
   `reconcile(ledger, target, allow_skip)`：缺章改派最早缺章，作者明确跳过才放行并记录。
3. `tests/workspace/test_structure_document.py`（9 用例）、
   `tests/workspace/test_prose_ledger.py`（13 用例），全部离线、不依赖 v1.1。

验证命令：`uv run pytest tests/workspace/test_structure_document.py tests/workspace/test_prose_ledger.py tests/workspace/test_release_recovery.py -q`
结果：24 passed。

## 未覆盖项（如实）

- 对账的**装配层接线**（`structure_assembly.py`）：需要 M2 的装配契约冻结与 v1.1 装配
  通路，未做。
- 台账的**事务接线**（提交追加 / 撤销回退）：依赖 v1.1 暂存闭环，未做。
- 预算超限的压缩路径：只做了检查信号，压缩交互属于 M1 后半，未做。
- 完整 `tests/workspace` 回归：随本记录提交时的运行结果见提交说明；如失败先修再进 Q1。

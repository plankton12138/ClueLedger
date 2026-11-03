# Q4 实施记录：验收与发布（M6）

日期：2026-11-04。范围：确定性矩阵核对、质量门、版本统一 1.1.0、文档状态同步；在线评测与浏览器走查如实登记为待执行。

## 1. 确定性矩阵核对（Q4-A）

[acceptance.md](../../v1.1/acceptance.md) 第 4 节 13 项逐项映射到既有测试，**全部有覆盖、无缺口**：

| 矩阵项 | 测试 |
|---|---|
| 起步双模式上限与跳过 | `test_book_startup.py`（引导计数、模式提示词）、`test_agent_startup_phase.py` |
| 设定卡确认幂等与撤销 | `test_book_startup.py`（一次门槛 / 拒重复 / 撤销再确认 / 旧格式拒收） |
| 起步结构入档与回退 | `test_structure_io.py`、`test_agent_structure_tools.py`、`test_git.py` |
| 起草候选采用与过期 | `test_creative_assist.py`（采用与过期拒收）、`test_chapter_writing.py` |
| 三元事务全写入路径 | `test_ledger_transactions.py`、`test_atomic_prose_guard.py`、`test_story_joint_commit.py` |
| 整体撤销四层回退 | `test_ledger_transactions.py`（撤销撤登记）、`test_story_store.py`（撤销冲突与保留） |
| 感知分类完整性 | `test_perception.py`（缺说明拒绝、回执与单独说明行） |
| 确定性对账拦截与改派 | `test_prose_ledger.py`、`test_structure_assembly.py`、`test_service_structure_injection.py` |
| 宽松机检 | `test_structure_assembly.py` |
| 建章提案事务 | `test_chapter_proposal.py`（确认门 / 引文一致 / 请求键幂等） |
| 无模型错误码 | `test_chat.py`（受理即失败 `MODEL_NOT_CONFIGURED`）、`test_creative_assist.py`（`MODEL_UNAVAILABLE`） |
| 并发修订 | `test_story_store.py`（过期基线冲突不落盘、外部编辑冲突、撤销冲突） |
| 预算超限压缩路径 | `test_structure_io.py`、`test_agent_structure_tools.py`（`STRUCTURE_BUDGET_CONFLICT`） |

伪造标记对账（UC-S05）由台账优先级与对账改派用例覆盖（`test_service_structure_injection.py`、`test_structure_assembly.py`）。

## 2. 质量门（Q4-B）

- 后端全量 `tests/workspace`：**1583 passed / 0 failed / 0 errors / 0 skipped**（junitxml 权威统计）。
- ruff 全仓：先清零 5 处遗留（`prose_ledger.py` 两处行宽、引号注解；`test_chapter_writing.py`、`test_context_structure.py` 行宽），现 **All checks passed**。
- mypy `src/hastings`：4 处错误，stash 对照确认为**既有瑕疵**（`story_index_jobs.py` / `creative_assist.py` 的 `aclosing` 类型变量、`creative_assist.py` 未注解参数、`story_api.py` union-attr），不在本版处理范围。
- 前端：tsc 无错误；vitest **29 文件 324 tests 全过**；`npm run build` 成功。

## 3. 版本统一 1.1.0（Q4-C）

- `pyproject.toml`、`src/hastings/__init__.py`：1.0.9 → **1.1.0**。
- `frontend/package.json`、`frontend/package-lock.json`：→ **1.1.0**。
- `uv.lock`：`hastings-ai` 包版本 → 1.1.0（httpcore 1.0.9 为第三方依赖，不动）。
- 健康检查 `file_app.py` 走 `hastings.__version__` 动态取值，无需改码。
- 版本断言 `test_release_recovery.py::test_release_version_and_visible_name` 同步为 1.1.0，4 passed。
- `desktop/package.json` 不在版本统一清单（v1.0.9 统一时也未含），保持 1.0.0 不动。
- 源码内「1.0.9 P1」等字样为历史阶段标注，不改。

## 4. 待执行项（如实登记）

| 项 | 状态 | 依赖 |
|---|---|---|
| UC-S01—S05 与在线评测（O01—O10 按新链路改写 + 新增五项） | **待执行** | 仓库 `.env` 在线模型；证据入库 `eval/runs/` |
| 浏览器走查（空作品连续链路 + 四档宽度 + 200% 缩放） | **待执行** | 人工走查 |
| 发布门槛第 1 条（矩阵全绿 + 质量门） | **已达成** | — |
| 发布门槛第 4 条（版本统一 + 文档状态） | **已达成** | — |

在线评测与浏览器走查完成后，本版方可称 1.1 交付（门槛第 2、3 条）。

# V03-4 收口评审修复：并发、迟到响应与跨作品作用域

状态：**已交付**（2026-10-18）。六项可复现问题全部修复并补了回归测试；
前三项涉及正文/账本数据正确性，优先处理。

来源：对第一批（V03-0–V03-4）退出条件的收口评审。问题由额外的并发、迟到
响应与跨作品接口验证复现，不属于已声明顺延的功能，因此计为缺陷。

## 问题与修法

| # | 级别 | 问题 | 根因 | 修法 |
|---|---|---|---|---|
| 1 | P1 | 统一发布可能覆盖期间已保存的新正文 | `operation_service.submit` 用**当前**有效版本替换调用方自带的 `old_revision_id`，逐对象 CAS 形同不存在 | 提交层保留调用方的并发条件，与当前版本不一致即 `MANUSCRIPT_BASE_CONFLICT`；发布器新增 `base_commit_id`，按**准备时**快照求值，使 `old_revision_id` 反映准备时版本 |
| 2 | P1 | 手工修改首次保存接受过期正文 | `save_draft` 取「当前章基础」绑定草稿，打开旧正文后别人保存新稿，首次自动保存把新基础绑给旧文本，随后完成即覆盖 | 草稿基础固定在**会话打开时**的章基础（作者实际读到的那一版）；章在会话打开后才建时回落当前基础 |
| 3 | P1 | 混合采用建议绕过来源过期检查 | `_assert_sources_current` 按章构建 `expected` 字典，同章新建议的基础**覆盖**旧建议的，旧建议不校验即入账 | 改为逐项核对：每条带来源的建议各自比对，任一条过期即拒绝整组（`SOURCE_NEEDS_RECHECK`） |
| 4 | P2 | 迟到调整结果恢复已丢弃候选 | `revise` 在 `await` 生成之后直接覆盖写回，生成期间被丢弃的候选被写回 `ready` | 落盘改走 `aux_mutate`，在**同一事务内**核对候选状态与修订号，不一致即拒绝，迟到结果不落地 |
| 5 | P2 | 摘要接口能取消其他作品的写作任务 | 停止/查询/重试路由 `del project_id` 后直接按 job_id 操作，不校验归属与任务类型 | 任务执行器校验负载里的 `project_id` 与 `job_type`，不一致即 404（与「不存在」不可区分）；路由传作品作用域 |
| 6 | P2 | 建议已生成，操作结果仍显示「尚未整理」 | `followups` 按 `ledger.suggestion_group` **版本对象**判断，而建议从来只写候选组辅助表 | 直接读候选组辅助表：无组 `not_started`，有 pending `available`，组存在但都已决定 `handled` |

## 关键设计决定

**1. 一致性锚在「准备时」，冲突按对象判。**
修复 1 没有把分支 head 当作 CAS 条件——那会让一次无关章的保存阻断整个
发布（V03-4.6 已定的按对象判冲突）。做法是：发布器按 `base_commit_id`
求值，领域命令产出的 `old_revision_id` 即准备时版本；提交层保留它并与
当前有效版本比对。没动过的对象放行，动过的对象显式冲突。测试
`test_unrelated_save_between_open_and_finish_does_not_block` 盯住前者，
`test_first_save_binds_the_version_the_author_read` 盯住后者。

**2. 准备时版本由三个入口传入，不是全局推断。**
试写采用传 `brief.base_head`，手工修改传 `session.base_commit_id`，撤销传
预览的 `after_commit_id`。没有准备阶段的路径（恢复、导入）不传，按当前
head 求值——它们的 `expected_chapter_base` 仍是逐对象闸门。

**3. 越权与不存在必须不可区分。**
摘要接口对别处任务 ID 回 404 而不是 403：否则既能停掉别人的任务，也能
借此探知别处的任务 ID 是否存在。

**4. 状态机不说谎，也不虚构第三种解释。**
建议状态从两态扩到三态：`handled` 表示「整理过且都已决定」。没有它，
「全部采用完」只能报 `not_started`——那是另一种误导。

## 改动范围

- `application/services/operation_service.py`：提交层保留并校验调用方并发条件
- `application/services/publish_manuscript_change.py`：`base_commit_id` 求值基础
- `application/services/edit_session_service.py`：草稿基础绑定会话打开时版本
- `application/services/drafting_service.py`：采用传 `base_head`；调整落盘原子核对
- `application/services/undo_service.py`：撤销传 `after_commit_id`
- `application/services/ledger_candidate_service.py`：来源逐项核对
- `application/workflows/summary_jobs.py` + `server/v2.py`：任务归属与类型校验
- `application/queries/followups.py`：建议状态读候选组
- 前端 `api/followups.ts`：`handled` 状态与文案

## 验证证据

- 新增回归 7 项：副本首存绑定 + 无关保存不阻断、建议混合采用、迟到调整
  不复活、跨作品任务 404、建议状态三态
- 既有契约不回退：手工修改冲突仍为 `MANUSCRIPT_BASE_CONFLICT` 409、
  撤销冲突仍为 `UNDO_DEPENDENCY_CONFLICT`、采用冲突仍为
  `DRAFT_TARGET_CONFLICT`（入口预检先于发布）
- `uv run pytest -q` 与 `npx vitest run` 全量通过；`ruff` / `mypy` /
  `tsc --noEmit` 干净

## 留白

1. **恢复入口的 `expected_chapter_base` 仍可选**：不传即无逐对象闸门
   （作者显式要求回退到某旧版本时不拦）。前端始终传；API 层保留选项。
2. **`adopt` 的状态写回未做原子核对**：采用发布成功后候选只可能从
   `ready` 变 `adopted`，与发布结果一致；生成类路径（`revise`）才是
   迟到写回的危险方向，已修。
3. **语义级披露越界仍不检测**（V03-4.7 已登记）：本次不扩大范围。

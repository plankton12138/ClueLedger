# 1.0.1 验收证据索引

本文件保留收尾批次的证据与当时判断。后续[阶段质量检查](../notes/2026-10-30-quality-audit.md)复现 6 处必需缺口；这些缺口已由[授权与采用修复](../notes/2026-10-30-authorization-adoption-fixes.md)关闭，六项复现、受影响回归、后端全量与浏览器复验通过，当前待在线受影响场景与作者体验。下列原有通过结果不替代最新完成判断。

收尾：2026-10-30。开发检查完成，作者体验确认单独保留。当前运行版本为 `1.0.1`。

## 确定性验证

下表指向实际执行的测试模块；完整用例名称保留在源文件中。受控模型只用于制造事件、故障和竞态，不作为文本质量评测。

| 要求 | 实际证据（仓库相对路径） |
|---|---|
| D01 空白构思、未定真相 | `tests/workspace/test_story_contracts.py`、`test_story_context.py`、`test_story_tools.py` |
| D02 关系、实际时间、回释 | `tests/workspace/test_story_plans.py`、`test_story_contracts.py` |
| D03 方案隔离 | `tests/workspace/test_story_store.py`、`test_story_joint_commit.py`、`test_story_adoption.py` |
| D04 局部采用、依赖闭包、未覆盖报告 | `tests/workspace/test_story_adoption.py`、`test_story_context.py` |
| D05 同键同负载／异负载、提交重放 | `tests/workspace/test_story_api.py`、`test_story_store.py`、`test_story_joint_commit.py` |
| D06 提交决定与逐文件故障恢复 | `tests/workspace/test_story_store.py`、`test_story_joint_commit.py`、`test_recovery_boundaries.py` |
| D07 外部编辑、局部撤销、同目标冲突 | `tests/workspace/test_story_joint_commit.py`、`test_story_store.py` |
| D08 首增量前暂停、迟到输出、提交资格 | `tests/workspace/test_operation_control.py`、`test_story_candidate.py`、`test_model_probe.py` |
| D09 继续、连续插话、代次与幂等 | `tests/workspace/test_operation_control.py`、`test_compaction_api.py`、`test_model_probe.py` |
| D10 刷新与重启、不自动请求 | `tests/workspace/test_operation_control.py`、`test_operations.py`、`test_compaction_service.py`；前端 operation／conversation 状态测试 |
| D11 实际输入、必需依赖、超容量拆组 | `tests/workspace/test_story_context.py`：首组实际输入与后续组持久化去重；`test_story_candidate.py` |
| D12 当前约定、压缩、跨会话 | `tests/workspace/test_compaction_service.py`、`test_story_context.py`；在线 O07 三次、各两条真实压缩记录 |
| D13 试读隔离、标题与隐藏标记 | `tests/workspace/test_story_context.py`：独立会话、空工具、无历史目录、作者节点标签不外发；在线 O11 |
| D14 区间、摘要、过期来源 | `tests/workspace/test_story_projection.py`、`test_story_review.py`、`test_story_candidate.py`、`test_story_joint_commit.py` |
| D15 无线索／无推进／提取失败 | `tests/workspace/test_story_review.py`、`test_story_projection.py`、`test_story_context.py`；空试读拒绝与过期关联拒绝 |
| D16 对象授权、真相、必需约定、来源 | `tests/workspace/test_story_tools.py`、`test_story_candidate.py`：直接作者命令、否定／引文不授权、不得伪造裁决 |
| D17 全模型入口与真实增量 | `tests/workspace/test_model_probe.py`、`test_compaction_service.py`、`test_operation_api.py`、`test_operation_control.py`；在线两条浏览器旅程 |
| D18 旧入口退出、保留写作能力 | `tests/workspace/test_story_overview_removal.py`；浏览器 `story-overview-removal.spec.ts`、章节线索、正文、灵感和模块回归 |

## 浏览器与在线旅程

- 本地全量：98 通过，1 跳过，零自动重试，5.3 分钟。跳过项仅在设置 `HASTINGS_UX_BASELINE` 时生成历史基线；本轮不重写旧版本基线。隔离目录：`data/test-tmp/v101-release-local-20261029`。
- 300 节点／30 章的默认片段与跨片段关系通过；390 像素与 150% 字号检查通过。截图：[窄屏构思](browser-final/story-390-150.png)。
- 在线构思、备选采用、引用进入实际请求：`frontend/e2e/story-workflow-online.spec.ts` 通过，49.4 秒，隔离目录 `data/test-tmp/v101-online-refresh-20261029`。
- 在线候选、实际正文定位、基于实际正文试读、成组撤销：`frontend/e2e/story-scene-online.spec.ts` 通过，50.5 秒，隔离目录 `data/test-tmp/v101-scene-online-e-20261029`。用例验证真实增量早于完成事件、正式文件变化与撤销恢复。[撤销后页面](browser-final/scene-undo.png)。
- 先前场景用例存在按钮可访问名称匹配错误；另一次严格字数场景停在未确认，正式正文未保存。修正用例选择器并将本旅程限定为保存／定位／试读／撤销后另建批次。严格字数与场景质量仍由 O08 的独立用例覆盖；原失败未覆盖。

## 在线语义复核

使用 `.env` 在线 `deepseek-flash`，生产模型与本次证据审阅者不同。审阅者为 Codex；这不代表作者已经确认文本质量或易用性。

[逐次记录](online-review.json)包含每次的运行目录、案例版本摘要、实现摘要、判断和理由。各批的 `reviews.json` 经 `python -m eval score ... --reviews ...` 生成 `rescored.json/md`，原 `report.json/md` 不改写。

| 场景 | 最终适用证据 | 达成 |
|---|---|---|
| O01 空白构思 | A1、B2、B3 | 3/3 |
| O02 真细节误导 | E1–E3 | 2/3 |
| O03 伪造证词 | E1–E3 | 2/3 |
| O04 倒叙 | A1、B2、B3 | 3/3 |
| O05 调查汇合 | G1–G3 | 2/3 |
| O06 备选与局部采用 | A1、B2、B3 | 3/3 |
| O07 跨会话与两次压缩 | A1、B2、B3 | 3/3 |
| O08 场景展开 | C1、B2、B3 | 3/3 |
| O09 生成中插话 | D1、B2、B3 | 3/3 |
| O10 前文重写 | H1–H3 | 3/3 |
| O11 受限试读 | F1–F3 | 3/3 |
| O12 矛盾与取舍 | E1–E3 | 3/3 |

逐案修正后的适用证据共 33 项达成、3 项未达成，每案至少 2/3；达到本版建议门槛。它是**分案例最终适用矩阵，不是同一冻结版本一次运行的总体成功率**。A–H 的完整路径在 JSON 中；旧契约、旧实现、端点断连和补跑均保留。

案例契约变化：O02／O03 预先固定允许时刻与独立证词；O12 允许有效提问进入 `needs_input`；O10 明确没有干燥实验、滴水不能排除更早弄湿的判定前提。修改后的契约各独立执行三次，没有把旧契约的成功片段算进新契约结果。O05 维持原问题，在加强推理边界后独立运行三次。

未达成的三项都在讨论稿中，没有改写作品：E-O02-3 混淆原话含义且自身门口动作不一致；E-O03-2 首次证词当场存在离房矛盾却被采信；G-O05-3 不能定人的脚印仍被用于否定特定人物的在房证词。它们属于推理质量问题，未发现越权写真相、串方案、隐藏输入泄漏、假报实际提交或暂停后写入这类严重失败。

O06 个别回答误称完整复制的备选为空，实际持久化结构及后续修改正确；创建工具现返回复制数量及明确说明。模型仍可能给出超出证据的说法，应用的来源、范围与提交保护不等于保证推理正确。

## 交付边界

代码、接口、版本、开发文档和开发验收已收尾。作者体验确认未进行，状态为“开发完成，待体验验收”；未发布安装包、未推送或提交 Git。旧数据不迁移，桌面端与修订学习不在本版范围。

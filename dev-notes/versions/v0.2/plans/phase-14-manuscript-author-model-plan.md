# Phase 14 计划：正文版本、人工编辑与作者模型

> 状态：已完成（2026-10-15）。构建笔记：[../phase-14-manuscript-author-model.md](../phase-14-manuscript-author-model.md)；模块 as-built：[../modules/manuscript-author-model.md](../modules/manuscript-author-model.md)。
> 依赖：Phase 12 完成；与 Phase 13 可并行，但正文关联 Story ID 的集成验收需等待 Phase 13。
> 目标：把人工编辑变成可靠的版本提交，并以异步、可解释的方式从修改中建立作者模型。

## 1. 开始条件

- Repository/Commit/Branch/Outbox 可用；
- 当前正文保存、revision 归档、write_guard 和 reconcile 行为已盘点；
- 前端能发送 base revision 或可在阶段内增加；
- 已决定第一版 TextUnit 粒度为段落，Scene/Beat 关联可选但字段已预留。

## 2. 阶段交付

1. Manuscript、Chapter、TextUnit 与 Edit Operation；
2. 人工保存统一提交用例；
3. 版本冲突和可合并 diff；
4. Agent Patch Proposal 与逐 hunk 接受/拒绝的领域结构；
5. TextUnit split/merge/move 谱系；
6. Author Observation、Preference Hypothesis 和 Author Example；
7. 编辑分类与偏好激活规则；
8. 作者模型查询/撤销接口；
9. V2 编辑器最小适配；
10. 分析任务失败不影响保存的证明。

## 3. 非目标

- 不接入真实 Writer/Reviser Agent；使用测试 Patch/Fake Worker；
- 不做向量数据库；
- 不训练或微调模型；
- 不自动把项目偏好提升为全局；
- 不执行跨 Canon 的完整影响分析；
- 不替换所有 V1 编辑页面。

## 4. 实施任务

### T14.1 正文与 Edit Operation

先用纯领域测试定义 insert/replace/delete/split/merge/move 的输入、规范化和结果。算法必须生成稳定 hunk ID、保留 TextUnit 谱系，并拒绝重叠或越界操作。

导入整章文本时，使用确定性的段落切分建立初始 TextUnit；后续保存优先提交 Edit Operations，兼容完整文本时由服务器计算 diff 并返回规范化结果。

### T14.2 人工保存用例

实现 `CommitManualEdit`：读取 base revision → 解析/验证编辑 → no-op 检查 → Commit → Outbox。用户界面的保存意图只允许当前章/场景范围。

保存成功响应必须先返回 Commit，再调度分析 Job。分析 Job 创建失败时记录环境告警和待补偿任务，但不撤销正文。

### T14.3 乐观冲突

两个客户端基于同一 revision 编辑：先提交者成功；后提交者得到 `MANUSCRIPT_BASE_CONFLICT`、当前 revision 和三方比较所需数据。前端提供重新载入、手动合并或另存分支，不自动 force overwrite。

### T14.4 Patch 与部分接受

建立 `ManuscriptPatchProposal`、PatchHunk、AcceptPatchHunks、RejectPatch。Fake Worker 生成跨三个段落的 Patch，测试只接受中间 hunk 时首尾不变，提交后未接受 hunk 状态仍可追踪。

### T14.5 Observation 分类

建立规则优先的分类管线：格式/错字、事实修订、结构修订、语言风格、协作反馈。模型分类器作为可选补充，通过 AgentRunner 端口返回 Proposal。低置信度或冲突结果保持 candidate。

### T14.6 偏好归纳和激活

实现 explicit preference、repeated evidence threshold、scope precedence、conflict detection、revoke/supersede。测试使用可注入策略，不把证据次数散落成魔法数字。

### T14.7 作者样例

作者可把某个 TextUnit/Revision 标记为正例或反例。只保存用户自有/授权文本引用；删除样例后新 Context 不再使用，但历史生成记录保留当时引用 ID。

### T14.8 最小 UI/API

- 编辑器显示当前 revision 和保存状态；
- 冲突时展示差异；
- 版本历史可查看和恢复为新 Commit；
- 作者模型页展示 candidate/active/revoked、证据和范围；
- 用户可确认、编辑、撤销偏好。

## 5. 建议文件边界

```text
src/hastings/domain/manuscript/{models.py,operations.py,diff.py,commands.py}
src/hastings/domain/author_model/{models.py,rules.py,commands.py}
src/hastings/application/workflows/manual_edit.py
src/hastings/application/workflows/author_learning.py
src/hastings/application/handlers/manuscript_commands.py
src/hastings/application/queries/{manuscript.py,author_profile.py}
tests/v2/manuscript/
tests/v2/author_model/
```

## 6. 测试先行清单

1. 每种 Edit Operation；
2. split/merge 后谱系；
3. no-op 保存；
4. 人工保存产生 Commit/Outbox；
5. 分析调度失败但保存成功；
6. 并发保存冲突；
7. Patch Scope 越界；
8. 部分接受；
9. 恢复历史产生新 Commit；
10. 格式/事实变化不进入文风；
11. 多个证据形成 candidate/active；
12. scope 优先级；
13. revoke 后 Context 查询排除；
14. 每个 active 偏好可反查 evidence；
15. 项目偏好不会自动成为全局。

## 7. 验收场景

1. 建立一章三段正文；
2. 用户修改第二段并保存，立即得到新 Commit；
3. 模拟分析 Provider 失败，正文仍为新版本；
4. 重跑分析，生成“偏好减少解释性句子”的 candidate；
5. 追加两次独立证据后按策略激活；
6. 查询下一次 Draft Context 可用偏好；
7. 用户撤销该偏好，查询不再返回；
8. 应用重启，正文和偏好状态保持一致。

## 8. 退出条件

- V2 人工保存完全不调用旧 `.revisions`/reconcile 写链；
- 保存与分析解耦有故障测试；
- Agent Patch 无法直接提交；
- 作者模型每条 active 记录都有证据和范围；
- 前端冲突不会自动 force；
- Phase 15 能从 TextUnit 及其 Story 关联建立依赖；
- 模块文档和构建笔记收口。

## 9. 风险控制

- 大段落重写导致锚点漂移：允许生成新 TextUnit 谱系，不依赖字符偏移作为永久 ID；
- 分类误判：默认 candidate，支持反例和撤销；
- 后台任务堆积：保存事件幂等，每个正文 Commit 至多一个同类型分析 Job；
- UI 尚未迁完：只对 V2 项目启用新保存协议。


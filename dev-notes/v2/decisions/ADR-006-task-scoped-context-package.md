# ADR-006：上下文按任务构建（Context Package），不做万能会话记忆

- 状态：已接受（2026-10-14）
- 关联：[DESIGN.md §8.2](../DESIGN.md#82-context-package)、[orchestration-context.md](../modules/orchestration-context.md)

## 上下文

V1 使用单一 `context_manager` 压缩长期会话供所有任务共用。规划、写作、修订和审计需要的"世界切片"完全不同：通用压缩会保留寒暄、丢弃证据，且无法声明"这个上下文基于哪个仓库版本"，模型因此无法区分已决定事实与讨论中的候选方案。

## 决定

每个任务使用显式构建的 Context Package，至少包含：`repository_version`、`task_kind` 与 `target_ids`、`selected_artifacts` 及选择原因、`author_preferences` 及证据范围、`constraints` 与允许的工具、`token_budget` 与裁剪记录、`expected_output_schema`。四类标准包：Draft、Repair、Audit、Style Learning。压缩只能压缩对话性说明，不得删除审批凭证、失败工具结果、基础版本和影响证据；丢失的正文内容用稳定引用按需读回。

## 后果

- 模型输入可复现、可审计："这次写作基于哪个 Commit 的哪些对象"有据可查；
- 上下文构建成为显式代码，裁剪决策可以被测试，而不是黑盒压缩；
- 需要为每类任务维护选择器与预算策略，前期成本高于"把会话塞进去"。

## 替代方案

- 继续通用会话压缩：无法声明版本依据，无法按任务区分约束；
- 全量检索（RAG 塞满）： token 不可控，且检索命中不等于任务相关。

## 触发重新评审的条件

- 某类任务的标准包被证明长期缺料（作者反复人工补上下文）——应调整该类包的选择器，而非回到会话拼接；
- 引入多任务连续工作流（如整书重写）时，需要评审包间版本对齐机制。

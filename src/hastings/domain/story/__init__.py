"""Story Domain：故事事实（Canon）、叙事计划与悬疑三层事实。

本包为纯领域层：只依赖 stdlib 与 `hastings.domain.repository`，
不导入 application/infrastructure/interfaces/agents/tools/tau（见
tests/test_v2_architecture_dependencies.py）。领域命令返回 DomainChange，
不直接写仓库；由 application handler 读快照、执行命令、Guard、再提交。
"""

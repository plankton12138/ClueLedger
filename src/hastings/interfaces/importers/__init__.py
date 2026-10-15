"""接口层导入器子包：把 V1 工作区映射/导入为 V2（Phase 13 映射 + Phase 17 迁移）。

- ``v1_story_mapper``（Phase 13）：逐字段把旧账本映射为 V2 提案的 **dry-run 报告**，
  不写 Repository；
- ``v1_workspace_importer``（Phase 17）：全工作区扫描（逐文件归类）、幂等导入
  （经 Commit Service）与写入后校验。
"""

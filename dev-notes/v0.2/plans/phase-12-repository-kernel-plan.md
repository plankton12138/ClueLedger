# Phase 12 计划：版本化 Repository 与统一提交

> 状态：**已完成（2026-10-14）**。构建笔记：[phase-12-repository-kernel.md](../phase-12-repository-kernel.md)；as-built 规格：[modules/repository-kernel.md](../modules/repository-kernel.md)。
> 依赖：Phase 11 完成。
> 目标：交付不懂小说业务、但能可靠保存版本、分支、提交、关系、Lease 和 Outbox 的仓库内核。
>
> **验收结论**：契约 13 项 × 双后端全绿；7 写点故障注入证明无半提交；八步验收场景双后端复跑通过；pytest 906 passed、ruff / mypy strict 全绿。
> **决策落地**：端口收窄只变具体不改名 ✅；no-op 检测移入事务内（消除 TOCTOU，docstring 已说明，语义不变）✅；Lease 版本跨 release 单调递增防 ABA ✅；Outbox sequence 由提交时分配（发布器只消费）✅；新增不变量 11（关系端点必须存在，FK 背书）✅。
> **已知留白**：单进程 RLock 串行化（跨进程/分布式不在范围）；no_change 幂等记录不落库；Publisher 无内建退避重试；事件类型仅 3 种生命周期事件，业务事件留给 Phase 13/14；inspection 仅覆盖 SQLite。
> **偏离记录**：T12.8 的「REST/CLI 只读调试入口或测试工具」按「测试工具」口径交付（inspection.py 结构化报告 + SqliteRepository.backup_to），未接 CLI 子命令——V1 CLI 是写作 REPL，仓库调试入口等有真实消费者（Phase 13+）再挂。

## 1. 开始条件

- ADR-001/002/003/007 已冻结；
- V2 依赖测试和旧工作区 fixture 可用；
- Repository 端口与错误信封已有稳定导入路径；
- 已确认 SQLite schema 文件和测试数据库可以完全隔离。

## 2. 阶段交付

1. Repository 领域类型和值对象；
2. 内存实现，用于快速领域/应用测试；
3. SQLite schema、迁移器和事务实现；
4. Commit Service：乐观并发、幂等、no-op、原子多对象提交；
5. Branch、Diff、Restore-as-new-commit；
6. Relation Revision 和基础依赖图读写；
7. Lease；
8. Outbox 与可重试 Publisher；
9. REST/CLI 只读调试入口或测试工具，不接管旧项目写路径；
10. Repository 契约测试同时覆盖内存和 SQLite。

## 3. 非目标

- 不实现角色、线索、章节等业务模型；
- 不导入真实旧工作区；
- 不把旧正文写 API 切到 V2；
- 不调用 Agent；
- 不构建最终前端；
- 不做远程数据库、多用户权限或分布式锁。

## 4. 实施任务

### T12.1 值对象和序列化

先测试再实现 `ProjectId`、`ArtifactId`、`RevisionId`、`CommitId`、`BranchId`、`JobId`、`ContentHash`、`Actor`、`Scope`。ID 不接受空字符串、路径或可变显示名；时间统一使用带时区 UTC。

定义规范 JSON 序列化，确保同一内容产生稳定哈希。浮点、datetime、集合顺序需要明确规则。

### T12.2 内存 Repository

实现最小 happy path：创建项目、创建 Artifact、提交首个 Revision、读取 Snapshot。随后按测试增量加入并发冲突、幂等、Branch 和 Relation。

内存实现不能为了方便跳过 SQLite 必须遵守的不变量，否则契约测试失去意义。

### T12.3 SQLite Schema 与迁移器

为每个表建立主键、外键、唯一约束和必要索引。启用 foreign keys。迁移器保存 schema version，支持空库创建和已知版本升级；未知较新版本必须拒绝打开并给出恢复建议。

数据库路径由配置/项目注册表提供，不从 Agent 参数接受任意路径。

### T12.4 Commit Service

实现固定顺序：规范化输入 → no-op 检测 → 开事务 → head/version 检查 → 写 Revision/Relation → 写 Commit/Entries → 更新 Branch → 写 Outbox → 提交。

故意在每个写点注入异常，验证事务完整回滚。

### T12.5 Branch 与 Diff

支持从 Commit 创建 Branch、移动 head、读取两 Commit 间 Artifact/Relation 差异。恢复历史版本通过构造新 Revision 和 Commit，不允许直接移动主分支到旧历史来伪装撤销。

### T12.6 Lease

实现 acquire/renew/release/expire。资源 key 使用规范形式，例如 `project:{id}:artifact:{id}`。续租和提交必须验证 lease version/owner；以可注入 Clock 测试到期，无 `sleep`。

### T12.7 Outbox

Publisher 按项目 sequence 发布，成功后标记 published。消费者使用 event_id 去重。模拟发布成功后进程在标记前崩溃，验证重复投递不会产生重复业务效果。

### T12.8 调试与备份

提供受限的 Repository inspection 命令：列项目、分支、Commit 和校验完整性。不得提供任意 SQL。实现数据库一致性检查和安全复制说明。

## 5. 建议文件边界

```text
src/hastings/domain/repository/{ids.py,models.py,requests.py,results.py}
src/hastings/application/ports/{repository.py,clock.py,ids.py}
src/hastings/application/services/commit_service.py
src/hastings/infrastructure/repository/{memory.py,sqlite.py,schema.py,migrations/}
src/hastings/infrastructure/events/outbox.py
tests/v2/repository/{contract.py,test_memory.py,test_sqlite.py,test_commit_service.py,test_outbox.py,test_lease.py}
```

## 6. 关键测试清单

按顺序先红后绿：

1. stable content hash；
2. 初始 Commit/Snapshot；
3. expected head 冲突；
4. 多 Artifact 原子提交；
5. no-op 不创建 Commit；
6. 幂等重试；
7. Branch 分叉和 diff；
8. restore 产生新 Commit；
9. relation revision；
10. lease acquire/renew/expire；
11. Commit + Outbox 同事务；
12. Publisher 重复投递；
13. 数据库重开后 head/sequence/hash 不变；
14. schema 较新版本拒绝；
15. 并发线程/任务提交只有一个成功。

## 7. 事件与错误

至少发布：`repository.commit.created`、`artifact.revision.created`、`branch.head.moved`。测试事件 payload 不包含完整正文，只含稳定 ID、kind、hash 和摘要。

实现模块文档定义的 Repository 错误码。SQLite 原始异常不能直接暴露给 Agent/UI，日志保留 correlation ID 和内部 cause。

## 8. 验收场景

使用纯测试数据：

1. 创建项目和 main；
2. 一个 Commit 同时加入两个 Artifact 和一条 relation；
3. 从 main 创建 rewrite 分支并提交新版本；
4. 比较两个分支；
5. 对过期 main head 提交并得到冲突；
6. 重新读取后基于新 head 成功提交；
7. 重启 Repository；
8. 验证所有 Commit、Branch、sequence、Outbox 和 hash 一致。

## 9. 退出条件

- 内存和 SQLite 通过同一契约测试；
- 故障注入证明无半提交；
- 旧 V1 项目行为和测试无变化；
- V2 模块文档的 repository 部分更新为 as-built；
- 构建笔记包含四透镜，重点说明错误契约、原子硬约束、no-op 与环境故障、Outbox；
- Phase 13/14 只需调用公开端口，不需要了解 SQLite 表。

## 10. 禁止提前做的事

- 不在 Revision content 中随意塞入未验证的角色/章节结构；
- 不为赶 UI 进度创建直接 SQL 写端点；
- 不让 Outbox Publisher 分配项目 sequence；
- 不用进程内锁替代 Lease；
- 不把数据库文件和旧 YAML 做双向自动同步。


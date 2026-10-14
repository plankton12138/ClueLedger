# Phase 12 构建笔记：仓库内核（版本化 Repository）

> 关键词：仓库内核、Commit Service、乐观并发、幂等重放、no-op、Outbox、Lease、故障注入、契约测试、inspection、备份
> 状态：已完成（2026-10-14）。计划：[phase-12-repository-kernel-plan.md](plans/phase-12-repository-kernel-plan.md)
> as-built 规格：[modules/repository-kernel.md](modules/repository-kernel.md)

## 1. 交付内容

| 交付物 | 位置 | 说明 |
|---|---|---|
| 领域值对象 | `src/hastings/domain/repository/ids.py` | ProjectId/ArtifactId/RevisionId/CommitId/BranchId/JobId/ContentHash/Actor/Scope，显式 frozen dataclass，共享 `_validated_id`（拒空串、路径分隔符、`.`/`..`） |
| 规范序列化 | `domain/repository/serialization.py` | canonical_json（键排序、UTC 归一、repr 浮点、拒 NaN/Inf/set）+ SHA-256 content_hash |
| 领域模型与请求 | `domain/repository/{models,requests,results,snapshot,error_codes}.py` | 9 核心对象、CommitRequest（幂等键/摘要非空强制）、COMMITTED/NO_CHANGE、快照折叠纯函数、16 个稳定错误码 |
| 端口收窄 | `application/ports/repository.py` | Phase 11 方法名全保留，签名从 `Mapping[str, object]` 收窄为领域值对象；新增 RepositoryTxn/RepositoryStorage 事务接缝 |
| 提交引擎 | `application/services/commit_service.py` | 11 步固定顺序单份实现：规范化→幂等重放→head/version→lease→快照/no-op→revisions→commit/entries→relations→head→outbox→幂等键 |
| 内存后端 | `infrastructure/repository/memory.py` | `_MemoryData.clone()` 快照回滚；LeaseRepository + EventOutbox；与 SQLite 共享同一 CommitService |
| SQLite 后端 | `infrastructure/repository/{schema.py,sqlite.py}` | 11 张表；BEGIN IMMEDIATE + 进程内 RLock；IntegrityError→REPO_INTEGRITY_ERROR；`backup_to` 一致性备份 |
| 迁移器 | `infrastructure/repository/migrations/migrator.py` | 空库建到当前版本；较新版本拒绝（REPO_SCHEMA_TOO_NEW，含备份恢复建议）；有表无版本拒绝 |
| Outbox Publisher | `infrastructure/events/outbox.py` | 逐事件发布保序、失败即停保持 pending；DeduplicatingSink 参考实现（消费端 event_id 去重） |
| 只读 inspection | `infrastructure/repository/inspection.py` | `mode=ro` 打开 + 白名单查询（无任意 SQL）；SQLite 自检 + 6 类应用层不变量扫描，结构化报告 |
| 契约测试 | `tests/v2/repository/contract.py` | 13 项检查双后端复跑（test_memory.py / test_sqlite.py）；SQLite 侧另含 4 个迁移专项 |
| 故障注入 | `tests/v2/repository/test_commit_service.py` | FaultTxn 在 7 个写点逐一注入异常，双后端前后状态逐字段相等（无半提交）；RecordingStorage 断言写点顺序 |
| 专项测试 | `test_lease.py` / `test_outbox.py` / `test_inspection.py` / `test_acceptance.py` | Lease 过期与 ABA 防护；Outbox 保序/重投去重；损坏检出与只读保证；计划 §8 八步验收双后端复跑 |

质量门：pytest **906 passed**（Phase 11 基线 775 + 新增 131，零回归）；ruff check / format / mypy strict 全绿。frontend job 未运行——本阶段未触碰 frontend/。

## 2. 关键决策

- **提交算法只有一份**：`CommitService` 通过 `RepositoryStorage.transaction()` 驱动 `RepositoryTxn` 事务原语，内存与 SQLite 各实现同一套原语。这使故障注入与契约测试对双后端完全同构——不写两份提交逻辑，就只有一处会腐烂。
- **no-op 检测放在事务内 head 检查之后**：偏离计划 §4「检测先于开事务」的字面顺序。理由：把读也纳入同一事务快照，消除「检测通过但 head 已被并发提交移动」的 TOCTOU 窗口；语义不变，正确性更强（已在 docstring 说明）。
- **Lease 版本号跨 release 单调递增**：release 不删行，owner 标记为 `released`。删行会让版本号回退，旧 owner 持有的过期凭据在新租约下重新有效（ABA）。测试固化 `released.version > renewed.version`。
- **Outbox sequence 由仓库提交时分配**：批内先取基数（`next_outbox_sequence`）再递增。Publisher 只消费不分配——若发布器分配 sequence，「事件与 Commit 同事务」就名存实亡。
- **关系端点必须存在升格为不变量 11**：提交引擎在写前校验 endpoints ∈（本提交新建 ∪ 已有对象），SQLite 侧由 FK 背书。防止悬空边在快照折叠时静默丢失。
- **先 insert_commit 后 insert_relation**：relation 行引用 commit_id，顺序由 FK 强制。这条「顺序即正确性」的约束写进了模块文档 §4 的步骤 8。
- **端口收窄同步更新 Phase 11 测试替身**：收窄只变具体不改名（计划口径），`test_v2_ports.py` 的 FakeReader/FakeCommitRepository 一并换成领域值对象签名——替身就是下一阶段实现的雏形，留旧签名会误导。

## 3. Agent 设计经验（四透镜）

**① 错误消息是喂给模型的 API。** `REPO_HEAD_CONFLICT` 的 details 携带 `expected`/`actual`/`branch_version`，fix 文案是可执行指令（「重新读取分支 head，基于 cmt_x 重新构造提交」）而非道歉；`REPO_SCHEMA_TOO_NEW` 的 fix 直接给两条恢复路径。模型拿到冲突后不需要猜测下一步——它重读 head、改 expected_head、重放幂等键即可。反过来，SQLite 原始约束异常（IntegrityError）不外泄：模型收到的是 REPO_INTEGRITY_ERROR 三要素信封，原始 cause 留在异常链里给日志。给模型看摘要，给程序看全文。

**② 硬约束放工具层，prompt 只负责解释世界。** 原子性不是「记得在 finally 里回滚」的自觉，而是事务接缝的默认行为——`BaseException → ROLLBACK → 重抛`，调用方连犯错的机会都没有。同类手法贯穿全程：写点顺序靠 FK 与 RecordingStorage 断言固化，不靠注释提醒；版本条件（expected_head / old_revision_id / expected_version）由存储层 rowcount 复查做第二道闸；关系端点存在性由引擎校验 + FK 双保险。没有任何一条不变量依赖调用方阅读文档。

**③ 泄压阀——区分违规 / 空结果 / 环境故障。** no-op 是 `NO_CHANGE` 正常返回 + `no_change_reason`，不是异常——把「没有变化」污名化成故障，调用方就会陷入重试循环。三类不可混淆：幂等重放返回原结果（合法重复）；归一化后无差异是合法空（指明原因即返回）；schema 版本太新是 ENVIRONMENT（重试永远无效，必须换路径）。判据是「重试能否修复」：能→VIOLATION 带修正指引；无操作必要→LEGAL_EMPTY；不能且不该重试→ENVIRONMENT。

**④ 可观测性是事件契约的一部分。** 事件 payload 只含 ID/kind/hash/摘要，契约测试断言「正文 * 100 not in raw」——同一事件流服务两类读者：下游消费者要机器可解析，人工审计要能对账但不该在事件里看到整章正文。inspection 沿同一原则：`integrity_problems` 每条自带「哪里坏 + 怎样恢复」，而不是一个布尔 ok。SQLite 原始异常被信封转译，但 `raise ... from exc` 保留 cause 链——可观测不等于把内部细节泼给调用方。

## 4. 评审记录

- 自查通过：契约 13 项 × 双后端；7 写点故障注入双后端无半提交；八步验收场景双后端通过；依赖方向测试零违规（`agents` 不 import 写端口的规则未被触碰）。
- 退出条件核对：内存与 SQLite 同套契约测试 ✅；故障注入证明无半提交 ✅；旧 V1 行为与测试无变化（V1 测试全绿，未触碰 28 条写入口）✅；模块文档 as-built ✅；构建笔记含四透镜 ✅；Phase 13/14 只需公开端口（SQLite 表不出 `infrastructure/repository/`）✅。
- 待评审留档：提交拆为三个原子 commit（领域+引擎+内存 / SQLite+Outbox / 收口），符合原子提交约定。

## 5. 踩过的坑（代码级，辅助记录）

- **Outbox sequence 全为 1**：三个事件在批量 append 前创建，`next_outbox_sequence` 每次返回同一基数。改为先取 `seq_base` 再批内递增。
- **no-op 跳过后 zip 错位**：循环内 `continue` 跳过 no-op 候选后，用 zip 对齐 prepared 与 new_revisions 会错位。改为循环内累积 `(change, revision, old_id)` 三元组。
- **relation 在 insert_commit 前写入违反 FK**：relation 行引用 commit_id，必须等 commit 落表；排查时还发现一处遗留的重复 insert_commit。
- **lease 过期测试两次假阴性**：请求内重新 acquire 刷新了租约（改为固定旧版本号断言）；相同幂等键走了重放短路（改独立幂等键）。
- **inspection 检测 SQL 报错对象**：UNION 前的写法无论新旧 revision 哪个缺失都只回报 `new_revision_id`，消息误导。改为 UNION ALL 分别回报真正缺失的 ID。
- **`ruff format .` 波及无关文件**：工作区存有其他未提交改动时，全仓 format 会把格式化噪声混进它们的 diff。限定路径 format 是更安全的默认。

## 6. 留白

- **单进程范围**：事务串行化靠进程内 RLock，跨进程并发与分布式锁不在 Phase 12（sqlite.py docstring 已声明）；Lease 已为跨进程互斥预留语义。
- **no_change 幂等记录不落库**：`put_idempotent_result` 跳过 NO_CHANGE（无 Commit 可指），`get_idempotent_result` 的防御性读取分支保留。
- **Publisher 无重试退避**：单事件失败即停保持顺序，重试节奏由调用方决定；sink 侧去重参考实现未含持久化 seen 集合。
- **事件类型仅 3 种生命周期事件**（commit.created / revision.created / head.moved）；领域业务事件（章节定稿、线索登记）留给 Phase 13/14 以 `CommitRequest.events` 通道接入。
- **Scope 过滤仅在 diff 生效**；`list_artifacts` 的 kind 过滤已实现，跨 kind 全局查询视图未做。
- **inspection 仅覆盖 SQLite 文件**：内存后端是进程内对象，无需持久检查。

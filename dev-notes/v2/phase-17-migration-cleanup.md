# Phase 17：旧工作区迁移、写入路径退役（已完成）

> 状态：**已完成**，2026-10-15。前置：[Phase 16](phase-16-agent-workflows.md)。
> 本阶段计划见 [plans/phase-17-migration-cleanup-plan.md](plans/phase-17-migration-cleanup-plan.md)。
> 已交付：T17.1–T17.3 导入器三段式、T17.5 迁移产品流 + CLI/WS 切换、T17.4 迁移界面、导出与往返、T17.6 不可达证明、T17.7 隔离与清理清单、T17.8 恢复演练与容量测量（§1–§7，含三轮评审补验收 §2.5–§2.6/§2.9）；
> 随后经用户决策「项目未上线，V1 直接弃用」，§2.10 四项旧写路径删除以独立 cleanup 提交执行完毕（§2.11），V1 工作区收缩为只读 + 可迁移，阶段收口。

## 1. 为什么做这一阶段

V2 的领域、应用与界面骨架已经能独立跑通创作和修复闭环，但**旧工作区进不来、旧写路径还没退出**。Phase 17 要解决的是「切换」而不是「再加功能」：让现有项目安全进入 V2，让写入只剩一条路，再谈删除旧代码。

本阶段把「迁移」当成一次**可审阅、可复现、可回滚**的操作，而不是一次性脚本：先扫描出报告，作者确认后再写入，写完全量校验。任何无法无损表达的旧数据都不许静默丢弃——它要么带三要素报错，要么带原因归档。

## 2. 交付内容

### 2.1 T17.1 全工作区扫描（`interfaces/importers/v1_workspace_importer.py`）

`scan_v1_workspace` 覆盖 `story_dir` 下除 `manifest.json` 外的**每一个文件**，每个源文件恰有一种归类：`mapped / legacy / ambiguous / ignored / error`。

| 源类别 | 归类 | 处理 |
|---|---|---|
| 旧账本（角色/线索/伏笔/手法/时间线/章纲） | mapped / error | 沿用 Phase 13 `v1_story_mapper` 的逐字段映射 |
| 正文 `chapters/chNN.md` | mapped | 经 `manuscript.commands.import_chapter` 生成章 + 段落 |
| 章节归档 `chapters/.revisions/*` | legacy | 旧历史不重放，哈希/统计进章的 `legacy_metadata` |
| `story.yaml` / `settings.yaml` / `sessions.json` / `events.jsonl` / `sessions/*` | ignored | 带原因归档（运行记录，非故事事实） |
| 未识别的文件 | error | 显式报错，不静默丢弃 |

报告自带 `run_id`（源 manifest hash）与 `ok` 判定：**无 error、无无理由遗漏**才为可迁移。基准 fixture 天然含两个须作者决策的阻断实体（无依据的干扰项、缺真凶的手法），因此其报告是「诚实地不通过」——这正是 T17.1 想要的泄压阀行为。

### 2.2 T17.2 幂等导入（`V1WorkspaceImporter.apply`）

- 幂等键 = `v1-import:{schema_version}:{manifest hash}`，交给 Commit Service；相同输入重试命中幂等表返回原 Commit；
- `confirm_run_id` 必须是作者已确认的 dry-run `run_id`，与当前源不符即拒绝——**源变化后必须重新 dry-run**；
- 报告不通过则拒绝导入；
- 只写入空项目（`head` 为空）：避免把旧源合并进已有 V2 项目；重复导入同一源走幂等重放；
- 全部内容**作为一次原子提交**（故事对象 + 关系 + 正文章节），并附 `v1.workspace.imported` 事件；**不直连 SQL**，不写源文件。

### 2.3 T17.3 校验（`V1WorkspaceImporter.validate`）

在新 Commit 的快照上逐项重算：故事对象数、章节数、**每章规范化正文哈希**、无悬空 ID（关系端点 + 章→叙事章引用）、旧 revision 保留策略、会话/事件只读归档。任一项不过都会显出 `expected / actual`，不用「测试全绿」替代业务核对。

### 2.4 T17.6 旧写路径不可达证明（`tests/v2/migration/test_legacy_write_unreachable.py`）

枚举三类可达面并断言：

1. **REST 路由表**（经 OpenAPI 取全路径）：全部在 `/api/v2` 前缀下，无 `bible/upsert/stage/reconcile` 旧端点，写路由都在 V2 前缀；
2. **工具集**：V2 项目的 `bible_upsert`、`write_chapter`、`method_lock/unlock`、`story_stage` 全部是拒绝桩，只读工具（`bible_query/read_chapter/list_chapters/timeline_check`）保持可用；对照组确认 V1 行为未变；
3. **模块依赖**：`domain/application` 不 import 旧存储与旧运行时（`bible.store`/`events_store`/`server.reconcile`/`manuscript.write_guard`/`sqlite3`/`yaml`）；`interfaces` 除 importers 的**只读**读取外不触达旧写路径。

为此新增 `tools/architecture_gate.py`（`refuse_tool` / `v2_write_rejection`），把 `chapter_tools`、`method_tools`、`story_tools` 的写工具按架构版本替换为拒绝桩——这就是「先让测试在兼容期失败，再逐入口切换直到通过」的落地。

### 2.5 评审补验收：三个可复现缺陷与修复

首轮交付后的一次专项走查发现三处缺陷（均已先补失败测试再修复，见 §5）：

| # | 缺陷 | 根因 | 修复 |
|---|---|---|---|
| P1 | 不可达测试只查独立 `build_v2_app`；**真实服务**仍同时挂旧 REST，对 v2 工作区 `PUT /api/{slug}/chapters/{id}` 实测 **200 且改写 Markdown** | 工具层拒绝桩覆盖不到 REST 这条独立路径 | `server/rest.py` 新增 `_reject_v2_legacy_write`，在章节保存与草稿锁端点按 `workspace_architecture_version` 拒绝（409 + 三要素）；真实装配下的证明见 `tests/test_v2_write_gate.py` |
| P1 | `chapters/archive/ch99.md` 被标为 **mapped** 却不导入，计数/哈希/校验都排除它，报告与校验仍通过 | 扫描分类（`chapters/**.md`）与导入枚举（仅顶层 `*.md`）范围不一致 | 抽出唯一枚举 `_chapter_files` + 判定 `_is_top_level_chapter`；扫描/提案/校验共用；嵌套结构显式报 `error` |
| P2 | 源中本就没有 `sessions.json`/`events.jsonl`（合法旧单会话工作区）时，导入成功但**校验失败** | 校验硬编码要求两者存在 | 校验改为核对**源中实际存在**的运行记录（`_is_runtime_record` + `_iter_workspace_files`） |

修复 P1 时另发现一处同源缺陷：`save_settings` 整表 dump 模型会把 `settings.yaml` 里的
`architecture_version` 抹掉——一次普通的「切换写作语言」即可静默把 V2 项目降级回 V1 路由，
使刚加的门失效。已改为保留未知键（先读回既有映射，只覆盖 `StorySettings` 字段）。

### 2.6 第二轮评审：旧单会话布局的兼容遗漏

上轮三处缺陷修复后的一次复测发现：**真正的旧单会话工作区仍无法迁移**。旧格式（多会话
索引 D13 引入前，定义见 `session_store.legacy_session_file`）在根目录保存 `session.jsonl`，
而分类器只登记了 `sessions.json` 与 `sessions/*.jsonl` 前缀，根级 `session.jsonl` 落入
「未知源文件」被阻断。上轮的 P2 修复只让「缺失会话索引」不再误判，新增测试也只删除了
索引文件、没有覆盖实际旧文件布局——判据与真实数据形态脱节。

修复：根级 `session.jsonl` 纳入 `_IGNORED_FILES`，以带原因的只读历史归档处理；因导入器
语义变化（该文件由 error 改为 ignored），`IMPORT_SCHEMA_VERSION` 递增到 2 使旧幂等记录
失效。补完整导入测试 `test_legacy_single_session_layout_imports`：构造根目录
`session.jsonl`、无索引、无 `sessions/` 的布局，走 `dry_run → apply → validate` 全程，
并断言源文件在导入后原样保留。

### 2.7 迁移产品流与 CLI/WS 切换（T17.5）

上两轮的迁移只是库函数 + 测试。本批把它变成产品流，并完成三条链路的 V2 切换：

1. **迁移映射**（`settings.py`）：`record_v2_migration` 把 `architecture_version: v2` +
   `v2_project_id` + `v2_db_path` 写进 settings.yaml（唯一项目元数据承载处，保留未知键）。
   这是 slug → V2 项目的**唯一映射点**——CLI、WS、前端都从它取项目，不引入第二套映射。
2. **迁移 REST 面**（`server/v2_migration.py`）：`GET /api/{slug}/v2-migration/status`、
   `POST dry-run`、`POST apply {confirm_run_id}`。apply 落成 V2 项目并写映射——映射落盘
   即翻转 `architecture_version`，旧写门（REST 409 / 工具拒绝桩 / CLI 拒绝）**即刻生效**，
   「迁移完成后禁止再走 V1 写路径」由既有 gating 承担，不另设门。导入未落成不写映射
   （不切换 active）；重复迁移 `MIGRATION_ALREADY_DONE` 拒绝。
3. **V2 事件通道**（`server/v2_ws.py`）：`/ws/v2/{project_id}`，首帧
   `{"type":"subscribe","after_seq":N}` → 存量补齐（Outbox 行发布后仍留表，按 sequence
   重放）→ 增量轮询推送。事件源是仓库 Outbox（与 Commit 同事务，ADR-007），不新增事件源；
   Job 仍走 REST 轮询、对话仍走 V1 WS——「分开」的落点是通道分工而非合并总线。
4. **CLI V2 读路径**（`cli_v2.py`）：已迁移工作区的 `/bible` 经 `StoryQueries` 读 V2 项目
   快照，不再读旧 YAML（迁移后账本事实源在仓库，读旧 YAML 会把用户带回旧真相）；
   `architecture_version` 判定点路由，V1 工作区行为不变。CLI 对 V2 只读不写——写走
   Web 端变更中心，不留第二套写旁路。

### 2.8 前端迁移面（T17.4，as-built 口径）

计划按旧版前端布局写七个页面名；as-built 落点是**迁移流程与 V2 主流程在界面上接通**：

- `api/v2migration.ts`：三个迁移端点客户端，V2 错误信封（why/fix）与 V1 detail 统一抽取；
  `summarizeReport` 纯函数产出阻断项与归类计数（错误消息是喂给人的 API，阻断原因逐条透出）。
- `MigrationPanel.tsx`：dry-run 报告展示 → 阻断项清零才亮确认按钮 → apply → 展示 V2
  项目 ID 与校验结论。报告未通过时**不能**确认（UI 层复刻服务端硬约束，而非仅靠提示）。
- `ArticlesPage`（项目页）：逐工作区迁移状态徽标（V2），未迁移可展开迁移面板——
  「V1 项目可选择迁移」的界面入口。
- `ChangeCenter`：V2 项目导出备份入口（`/api/v2/projects/{id}/export`）。
- 差异记录：ChangeCenter/AgentWorkbench（变更与作者流）、正文 V2 保存链
  （`chapters/{id}/save`）已构成 V2 主流程；`ManuscriptPanel` 等组件仍服务 V1 工作区，
  其 V2 化随「V1 工作区全面退役」另行立项（见 §6）。

### 2.9 导出、演练与容量测量（T17.8）

**导出**（`interfaces/exporters/v2_package_exporter.py`，此前完全缺失）：
指定 Commit → 可读备份包：`manifest.json`（逐文件 sha256 + 段落清单 unit_id/文本哈希 +
`package_hash` 指纹）+ `chapters/<order_key>.md`（纯正文）+ `bible/<账本>/<id>.yaml` +
`relations.json`。全部确定性字节（无时间戳），同 Commit 两次导出逐位一致；`verify_package`
对篡改/损坏带三要素显式拒绝；`apply_exported_package` 重导入为一次原子提交（幂等键
`v2-export:{schema}:{指纹}`，只写空项目），往返保持 artifact id / 章节基础（unit_id+文本）/
依赖边逐位一致。REST：`GET /api/v2/projects/{id}/export`（zip）。

**恢复演练**（`tests/v2/migration/test_recovery_drills.py`，注入式故障）：
磁盘满（ENOSPC 注入）→ commit 抛 OSError、目标项目 head 为空、无半项目、active 不切换；
修复后同 run_id 重试幂等落成；中断与重试之间源被改动 → 旧 run_id 拒绝、要求重新 dry-run
（不静默用旧报告覆盖新源）。Provider 不可用不阻止纯数据迁移：迁移链路（导入器/迁移端点）
不触 provider 装配，测试全程无 provider 配置即通过。

**容量测量**（`scripts/measure_capacity.py`，100 章 × 每章约 5000 字，SQLite 后端）：

| 度量 | 数值 | 说明 |
|---|---|---|
| 幂等导入（迁移主路径） | 576 ms | 一次性路径 |
| 打开项目（元数据，不读全书） | 43 ms | plan §7「打开项目不读取全书正文」达标 |
| 读单章全文 | 0.2 ms | 交互路径 |
| 章基础（乐观并发） | 0.3 ms | 交互路径 |
| 人工保存 Commit | 86 ms | V2 写链交互路径 |
| 两快照比较 | 119 ms | 分支/版本比较 |
| 导出（包 + zip） | 333 ms | 一次性路径 |
| 重启恢复（重开 sqlite） | 6 ms | head 直接可用，不重放对话 |

预算选择：交互路径（打开/读单章/章基础/保存）全部在 100 ms 量级以内，低于人的感知阈值；
一次性路径（导入/导出）秒级以下，无需进一步优化。开发笔记本硬件，数值随本笔记归档。

**真实工作区核对**（`stories/` 下 12 个真实 V1 工作区的只读副本逐个 dry-run）：
核对过程暴露第三轮兼容缺口——**会话锁文件**（`.session.jsonl.lock`、
`sessions/.session-*.jsonl.lock`）与**压缩归档会话**（根级 `session-<stamp>.jsonl`）
被判未知文件阻断；修复为带原因只读归档（`IMPORT_SCHEMA_VERSION` 2→3，第三轮 P2）。
修复后：8/12 直接可迁移；其余 4 个仅余**设计内**的作者决策阻断（red herring 缺 basis、
method 缺真凶）——这正是「阻断实体显式报错而非静默丢失」的预期行为。正式迁移仍由
用户决定。

### 2.10 T17.7 清理清单（隔离即达成，删除待退役）

计划口径是「删除**或隔离**」。旧写路径在 V2 项目的可达面已全部隔离并有行为证明
（`tests/v2/migration/test_legacy_write_unreachable.py` 17 项 + `tests/test_v2_write_gate.py`
6 项 + 迁移端点测试中的真实装配拒绝断言）：

| 旧路径 | 隔离机制 | 退役删除条件 |
|---|---|---|
| `bible_upsert` | V2 拒绝桩（工具层）+ 前端不再按其事件刷新 V2 数据 | V1 工作区支持退役时删除工具本体与 YAML 直写 |
| 全局阶段授权（story_stage） | V2 拒绝桩；V2 项目不读 stage 授权 | 同上；`bible/story_state.py` 状态机随 V1 历史保留 |
| 旧 reconcile | V2 层禁 import（架构依赖测试）+ V2 无 sync 入口 | WS sync 命令与 `server/reconcile.py` 随 V1 会话退役 |
| session-owned lock | V2 层禁 import write_guard；REST 写门 409 | `manuscript/write_guard.py`、edit-mark 端点随 V1 退役 |

删除被 V1 工作区持续支持（非目标：不强制迁移）阻断——V1 项目合法使用 V1 写路径，
现在删除会破坏它们。完整删除清单已列于上表「退役删除条件」列，届时独立 cleanup 提交执行。

### 2.11 V1 弃用收尾（用户决策后的执行记录）

主体交付后，用户确认：**本项目尚未上线、没有存量作者，V1 直接弃用**。三项人工验收
随之重估——「桌面安装包升级演练」因无生产用户而作废；「真实工作区用户核对」简化为
「用户确认弃用 V1」本身（迁移仍可按 §2.5–§2.9 的流程随时执行）；「e2e 走查」由本批
改写对齐（见下）。据此前述「删除待退役」的前置条件不再成立，§2.10 四项旧写路径以
独立 cleanup 提交执行完毕：

1. **工具层删除**：`bible_upsert`/`write_chapter`/`story_stage`/`method_lock`/`method_unlock`
   工具与 `tools/architecture_gate.py` 拒绝桩机制（门随门禁对象一起消失）；
   `manuscript/advance_guard.py`、`write_guard.py`、`prose_guard.py`、`guards.py`、
   `bible/state_machine.py` 模块删除。工具集收缩为只读四件：
   `bible_query` / `read_chapter` / `list_chapters` / `timeline_check`。
2. **REST/WS 下线**：`PUT /api/{slug}/chapters/{id}`、edit-mark 两端点、WS sync 命令、
   notice 订阅与 `server/reconcile.py` 删除；`WritingSession` 协议同步收缩。
   V1 REST 写端点对 v2 工作区不再需要 409 门——端点本体已不存在。
3. **prompts 只读语义**：WORKFLOW_SECTION 改为「没有任何写工具；改写请迁移到 V2
   仓库项目」；CONFIRM_MARKER 协议删除，OPTIONS 协议保留。
4. **前端只读化**：`ManuscriptPanel` 收缩为阅读态（头部「V1 只读 · 改写请迁移到 V2
   项目」）；`editLock.ts`、rest 写函数、`onRetrySync` 管线删除；空态文案去掉
   「用 bible_upsert 登记」类指引。`SyncCard`/`ConfirmCard` 作为无生产事件的展示
   残留保留（syncCards 队列恒空）。
5. **保留项**：`bible/store.py` 的 `save_*`（夹具级工具，无生产调用方）、
   `story_state.py` 读半区、store 读路径、迁移导入器、sessions REST、
   `workspace_architecture_version`（导入器与 cli_v2 仍依赖）。
6. **e2e 对齐**：`fake_scripts` 的 `chapter_write` 改为 `chapter_read`，intake 剧本改
   `bible_query` 只读剧本；j2（只读查档不落盘）、j3（正文只读 + 写入口下线断言）、
   j4（空态文案）spec 改写；j1/j5–j8 不受影响（j8 为 V2 域）。

收尾质量门：Python **1278 passed**（较主体交付减少的用例即被删除写路径的测试）、
前端 vitest **279 passed**、`tsc` + 生产构建通过、`ruff` 检查与格式通过、
`mypy` strict 217 文件通过。不可达证明测试改写为「退役语义」：
`test_write_tools_are_retired`（写工具本体不可达）+ `test_read_tools_remain_available`
（只读面完整）；REST 写端点断言由 409 拒绝改为 404/405（端点下线）。

## 3. 关键决策与取舍

1. **迁移报告以「文件」为源项单位，而非「字段」。** 字段级归类（mapped/legacy/ambiguous）属于 Phase 13 的故事映射；迁移报告要回答的是「这个文件去哪了」。两层各自完整、互不重复：故事侧回答「字段怎么映射」，迁移侧回答「文件有没有被漏掉」。覆盖性用「逐文件枚举 + 未识别即 error」来保证。
2. **确定性 ID 端口。** 章节正文经领域命令 `import_chapter` 生成（拿到不变量检查），但 `new_id` 换成确定性闭包，使同一源对象每次导入得到同一 ArtifactId。可复现是幂等与核对的前提，不能靠运行时随机 ID。
3. **旧历史只读保留，不重放。** V2 的版本历史从导入那一刻开始；旧 `chapters/.revisions` 的哈希与统计进 `legacy_metadata`。理由：重放旧稿会与 V2 的 Patch/分支语义冲突，且旧稿没有 `base_revision`。保留证据比保留操作更重要。
4. **一次原子提交 + 幂等键，而非「扫描即写」。** 单一 Commit 让「切换成功后」与「切换前」边界清晰；幂等键绑定源 manifest 让重试安全。拒绝在源变化后继续用旧报告，是为了不把「作者看过的报告」和「实际写入的内容」割裂。
5. **拒绝桩而非删除旧工具。** V2 项目里保留同名工具并固定拒绝，附三要素修正指引，比「未知工具」更利于模型纠偏（与 `bible_upsert` 既有 V2 桩同哲学）。
6. **「可达面」必须含 REST 与模块依赖，不能只看独立 V2 应用。** 工具层做门不等于旧写路径不可达：只要真实服务还挂着旧 REST，就存在绕过。因此不可达证明拆成两层——独立 V2 应用/工具/模块依赖（`tests/v2/migration/`）+ 真实装配下的行为证明（`tests/test_v2_write_gate.py`）。
7. **扫描范围、导入范围、校验范围必须同源。** 三者各写一遍过滤条件是漏导的温床：`chapters/**.md` 与 `chapters/*.md` 差一个 `**`，就能让一个文件「报告里 mapped、实际不导入、校验还通过」。现在统一由 `_chapter_files` 提供唯一枚举，任何偏移都会让新增的一致性测试失败。
8. **slug → V2 项目只允许一个映射点。** 迁移映射（`v2_project_id`/`v2_db_path`）落 settings.yaml——它已经是架构版本与用户设置的承载处；CLI、WS、前端都从这一处取项目。第二套映射（内存表、独立文件）会漂移，漂移的映射等于把用户导向错误的项目。
9. **通道分工优于合并总线。** V2 WS 只承载与 Commit 同事务的业务事件（Outbox 重放 + sequence 补齐）；Job 保持 REST 轮询、对话保持 V1 WS。合并总线要求三种语义（至少一次/可轮询/流式 token）共享一套投递协议，复杂度集中而收益为零。
10. **UI 层复刻服务端硬约束的「形状」，不重复其判定。** 报告未通过时迁移确认按钮禁用，是因为服务端 `apply` 必拒——UI 的禁用只是把同一事实提前呈现；判定本身永远只在服务端一处（迁移端点），前端不放第二把尺子。

## 4. Agent 设计经验

### ① 错误消息是喂给模型的 API

迁移的每条阻断都带三要素并定位到文件：`bible/clues.yaml` 报 `STORY_RED_HERRING_WITHOUT_CAUSE@v1.clue[...].basis`，给出「由作者补写 basis 后重新导入」。`confirm_run_id` 不符时，错误里同时给出「当前 run_id 前缀」与「确认的 run_id 前缀」，模型/用户能立刻判断该重新跑 dry-run 还是该用新报告。拒绝桩的文案统一走 `v2_write_rejection(tool, alternative=...)`，永远包含「为什么错」和「怎样算对」。

### ② 硬约束放工具层，prompt 只负责解释世界

「V2 项目不能写旧路径」是**层内判定**：`create_writing_tools(architecture_version="v2")` 直接把写工具的 `execute_fn` 换成拒绝桩，并在 `description`/`prompt_guidelines` 同步宣告不可用——避免「提示要求调用 / 工具固定拒绝」的循环。判定点只有一个（`workspace_architecture_version` → 传入工具装配），不散落到各工具内部。不可达证明用枚举而非打勾，新增入口时若没同步 gating，测试会失败。

**补验收教训**：工具层的门挡不住 HTTP 层——旧 REST 写端点对同一项目仍可落盘。硬约束要覆盖「模型能到达的每一条通道」，而不是「我们记得改过的那一条」。因此 REST 侧复用同一判定点 `_reject_v2_legacy_write`，并用真实装配的行为测试（拒绝 + 文件字节不变）而不是路由是否存在来证明。更进一步：任何能改写项目元数据的入口（如设置保存）都可能**解除**这道门，所以元数据的写入也必须保留架构键。

### ③ 泄压阀：区分违规、空结果与环境故障

迁移把三类分开：**源有阻断实体**是违规（须作者决策，报告不通过、不写盘）；**幂等重放**是合法空结果（返回原 Commit，`replayed=True`，不报错）；**未知文件**是环境/数据漂移（显式 error，不猜）。校验失败的每一项都保留 `expected/actual`，供作者核对而不是抛一个笼统的「迁移失败」。

**补验收教训**：校验的期望值必须来自**源的实际内容**，不能是固定清单。「旧单会话工作区没有会话索引」是合法的，把它当成必填会把合法源误判为失败（假阳性比漏判更伤信任）。反过来，源中存在的记录必须以 `ignored` 归档、不得丢失——期望与判据同源，才能同时避免漏判与误判。

### ④ 可观测性是事件契约的一部分

导入批次随 Commit 落一条 `v1.workspace.imported` 事件，payload 只放稳定标识与汇总（`run_id`、`schema_version`、文件数、章节数、字数），不放正文——事件是索引不是副本。`run_id` 本身是「源内容指纹」，可复现、不含 mtime，因此同一内容在任何机器上都得到同一幂等键；这让「这次导入对应哪份源」成为可核对的事实，而不是一段日志。

## 5. 评审与验收记录

- 新增 `tests/v2/migration/` 共 **46 项**：迁移主流程 26 项（覆盖性、确定性、只读、阻断报错、幂等、源变化重跑、非空项目拒绝、章节哈希、悬空引用、旧 revision 保留、运行记录归档、嵌套目录拒绝、扫描/导入范围一致、缺失运行记录不误判、旧单会话布局完整导入、会话锁/归档会话归档）+ 恢复演练 2 项 + 不可达证明 17 项。
- 新增 `tests/test_v2_write_gate.py` 共 **6 项**：真实装配下旧写路由挂载、V2 项目保存/草稿锁被拒且文件字节不变、V1 项目行为不变、设置保存保留架构元数据。
- 新增 `tests/v2/exporter/` 共 **13 项**：导出确定性、manifest 完整性、往返逐位一致、重导入幂等、损坏拒绝、历史 Commit、REST zip。
- 新增 `tests/v2/server/test_migration_endpoints.py` 7 项（status/dry-run/apply/失败不写映射/重复迁移拒绝/旧写门生效）+ `test_v2_ws.py` 3 项（订阅重放/增量与重连补齐/坏首帧拒绝）+ `tests/test_cli_v2.py` 6 项（V2 读路径/路由判定/映射保护）。
- 前端：vitest **296 项通过**（含迁移 API 8 项）；`tsc` 干净；生产构建通过。
- 全量 Python **1548 项通过**；`ruff` 检查与格式通过；`mypy` strict 通过。
- 端到端证据：基准 fixture 的完整 `dry_run → apply → validate` 在内存仓库与 SQLite 上跑通，源工作区字节哈希前后一致；导出→重导入往返逐位一致；磁盘满/中断演练通过；容量测量随 §2.9 归档。
- 评审补验收：三轮共五处缺陷先补失败测试（9 项新增用例复现）再修复；修复后同一批用例转绿。

## 6. 尚未交付（余项为后续立项）

1. **真实工作区正式迁移**：迁移链路（dry-run → apply → validate）与 12 个真实工作区
   副本的核对证据已齐（§2.9，8/12 直接可迁移，4 个余作者决策阻断）；何时对哪个工作区
   执行正式迁移由用户决定。
2. **e2e 迁移流场景**：j1–j8 已对齐 V1 只读 + V2 主流程；「新建→写作→迁移→变更中心」
   的 V2 全链路 e2e 场景可随 V2 主流程深化另立。
3. ~~V1 工作区全面退役~~：**已随用户弃用决策执行完毕**（§2.11），不再是余项。

## 7. 评审结论状态

| 计划任务 | 评审判断 | 现状 |
|---|---|---|
| T17.1–T17.3 扫描、幂等导入、校验 | 主流程已有，存在漏导与误判 | 三轮五处缺陷修复；真实工作区核对完成（8/12 直接可迁移，4 个余作者决策阻断） |
| 导出（可读备份 + 往返） | 交付前缺失 | 已交付：确定性包 + manifest 哈希 + 重导入往返逐位一致 |
| T17.5 CLI/WS 切换 | 未开始 | 已交付：迁移产品流（REST 三端点）、CLI V2 读路径、WS 事件通道按 sequence 补齐 |
| T17.4 界面切换 | 未开始 | 已交付：迁移面板 + 状态徽标 + V2 导出入口；正文面板随 V1 只读化收缩为阅读态 |
| T17.6 旧写路径不可达 | 已完成 | 收口：写工具本体与 REST 写端点已删除（§2.11），不可达由「端点不存在」直接成立 |
| T17.7 删除与归档 | 未完成 | **已完成**：§2.10 四项旧写路径以 cleanup 提交执行删除（§2.11），V1 收缩为只读 + 可迁移 |
| T17.8 打包与恢复演练 | 未开始 | 恢复演练与容量测量已交付；桌面安装包升级演练因项目未上线作废（§2.11） |
| 阶段退出条件 | 证据未齐 | **已满足**：自动化证据齐全 + 用户确认弃用 V1 + 三项人工验收按 §2.11 处置完毕 |

**结论**：迁移部分完整交付并有自动化证据；用户决策「项目未上线，V1 直接弃用」后，
四项旧写路径删除执行完毕、e2e 对齐只读语义、全量质量门通过——Phase 17 标记**完成**。

# 现状系统迁移清单（Phase 11 / as-built）

> 生成方式：由 rg 扫描代码生成初始清单后人工核对，反映 2026-10-14 的实际代码。本文件是 as-built 快照，V2 迁移各阶段完成后应更新对应行。
> 范围：`src/hastings` 全部模块 + `eval/driver.py`（周边写路径）。条目均给真实代码位置（文件 + 函数/类名），不确定处标「待核对」。

## 1. 当前数据文件与创建者

### 1.1 工作区数据（`stories/<slug>/`，根目录由 `--stories-root` 指定，默认 `./stories`）

| 文件/目录模式 | 创建者（模块.函数） | 格式 | 内容 | V2 处理 |
|---|---|---|---|---|
| `stories/<slug>/`（目录） | `server.registry.WorkspaceRegistry.create_workspace` → `StoryWorkspace.ensure_dirs`；`get_or_create`/`rebuild` 亦会 ensure_dirs | 目录 | 工作区根 | P17 导入/迁移为 V2 项目 |
| `bible/characters/<name>.yaml` | `bible.store.save_character`（唯一入口：`tools.bible_tools` 的 bible_upsert） | YAML（pydantic dump） | 角色卡 | P13 拆入 domain/story 模型 + domain/repository 存储 |
| `bible/clues.yaml` | `bible.store.save_ledger`（经 `tools.bible_tools._make_ledger_ops` 的 write_all） | YAML 列表 | 线索账本（name 主键，整表覆盖） | 同上 |
| `bible/foreshadows.yaml` | 同上 | YAML 列表 | 伏笔账本 | 同上 |
| `bible/method.yaml` | 同上（另 `tools.method_tools._save_methods`） | YAML 列表 | 诡计手法账本（含 history 审计链） | 同上 |
| `bible/timeline.yaml` | 同上 | YAML 列表 | 时间线事件账本 | 同上 |
| `bible/outlines.yaml` | 同上（另 `tools.chapter_tools._advance_outline` 落盘后自动推进状态） | YAML 列表 | 章纲账本（name + chapter 双主键） | 同上 |
| `story.yaml` | `bible.store.save_story_doc`（唯一写入口：`tools.story_tools` 的 story_stage；REST 无写口） | YAML | 六阶段生命周期 + `stage_history` 留痕 | P13 六阶段全局状态机退出，改为退出授权与写入控制（DESIGN §11） |
| `settings.yaml` | `settings.save_settings`（入口：`server.rest.update_settings`） | YAML | 写作语言 / provider 档案名 / 模型名 / context_window_tokens | P17 随接口层迁移；「待核对」见 §7 注 ② |
| `chapters/chNN.md` | **两个写入口**：① `tools.chapter_tools.execute_write`（agent 的 write_chapter，直接 `write_text`）；② `manuscript.store.save_chapter`（作者受管写，REST PUT /chapters） | Markdown | 章节正文（标题行由写入方生成） | P14 版本化 Manuscript + 统一提交（人工/Agent 共享历史与守卫，DESIGN §11） |
| `chapters/.revisions/<chapter_id>/NNN.md` + `NNN.json` | `manuscript.store.save_chapter`（先落 JSON 元数据后落 MD 归档，再原子替换主文件） | Markdown + JSON | 作者侧归档版本（ts/hunks/stats）；**agent 的 write_chapter 不产生归档** | P14 并入统一版本历史 |
| `sessions/<session_id>.jsonl` | `session_persist.attach_session_storage._persist`（订阅 message_end，经 tau `JsonlSessionStorage.append`）；压缩重写：`context_manager._persist_compacted` | JSONL（MessageEntry） | 会话记忆（模型视角，只收定稿消息） | P16 会话不再是项目记忆（DESIGN §13）；上下文按任务构建 |
| `sessions.json` | `session_store._write_index`（被 `load_index`/`_init_or_migrate`/`create_session`/`activate_session`/`rename_session`/`delete_session`/`touch_session` 调用，原子替换） | JSON | 会话索引（active_id + 元数据列表） | P16/P17 迁入 repository；损坏备份重建逻辑退出 |
| `session.jsonl`（旧格式，迁移源） | 历史遗留；`session_store._init_or_migrate` 将其 rename 归位到 `sessions/<id>.jsonl` | JSONL | P10 之前的单会话文件 | P17 旧工作区导入器覆盖 |
| `session-<stamp>.jsonl`（会话归档副本） | `session_persist.archive_session_file`（copy2 + unlink 原件；压缩/重装配前调用） | JSONL | 压缩前的会话备份 | P14 统一提交后由 Commit 历史替代 |

### 1.2 事件账本（工作区内）

| 文件/目录模式 | 创建者（模块.函数） | 格式 | 内容 | V2 处理 |
|---|---|---|---|---|
| `events.jsonl` | `events_store.EventStore._write_line`（经 `append`/`append_event`；触发方：`make_event_persister`、`server.hastings_session._persist_notice`、`_on_agent_end` → `context_manager.record_token_usage`） | JSONL（ts/seq/type/data） | 全量事件帧（可观测性账，含 notice、token_usage；`message_update` 不入库） | P12 统一 Commit Sequence + Outbox（DESIGN §11「多实例序号」问题点，见 §7 注 ④） |

### 1.3 服务级数据（与 stories/ 平级）

| 文件/目录模式 | 创建者（模块.函数） | 格式 | 内容 | V2 处理 |
|---|---|---|---|---|
| `config.yaml`（`--config`/`--config-dir` 可改路径） | `server.provider_config.save_server_config`（入口：`server.rest._persist_book` ← PUT/DELETE `/api/providers/{name}`） | YAML | provider 档案目录（**api_key 明文落盘**，文档声明属文件系统权限边界） | P12 基础设施模型接入配置 |
| `corpus/<name>.txt\|.md` | `rag.corpus_store.CorpusStore.save`（入口：POST `/api/corpus`）；删除：`CorpusStore.delete` | 纯文本 | 作者级语料资产（文件名即主键，同名须先显式 DELETE） | P14 纳入 Author Model 证据源 |
| `eval/` 跑测产物（out_dir，工作区外） | `eval/driver.py`（`mkdir` + `write_text` 转录/正文） | Markdown/JSON | 走查评测转录 | 不属运行时写路径，V2 评测体系另行规划 |

## 2. 当前工具与所有写路径

工具均由 `cli.create_writing_tools`（CLI/eval）与 `server.hastings_session.HastingsSession.from_workspace`（Web）两处装配，绑定同一 workspace。

| 工具名 | 所在文件 | 写目标（哪些文件/账本） | 保护机制（lock/守卫/覆盖保护） | V2 目标（意图明确的命令 / Proposal / 退出） |
|---|---|---|---|---|
| `bible_upsert` | `tools/bible_tools.py`（`upsert_execute`，经 `_REGISTRY` 六实体注册表） | characters/*.yaml 或 clues/foreshadows/method/timeline/outlines 五账本（整表覆盖写） | ① `reject_intake_write` intake 阶段写门；② 状态机校验（`bible.state_machine`）；③ locked 手法拦截（`_reject_locked_method_edit`）；④ 章号唯一（`_check_outline_chapter_unique`）；⑤ 参与者建档校验（`_check_timeline_participants`）；⑥ `validate_entity_name` 路径穿越防护；⑦ history 字段不采纳（lock/unlock 专属账目） | 由意图明确的领域命令替代（泛化覆盖易擦除字段、绕过状态，DESIGN §11/§13） |
| `bible_query` | 同上（`query_execute`） | 只读 | 查无结果返回合法空结果 | 退出（读经 repository 查询面） |
| `write_chapter` | `tools/chapter_tools.py`（`execute_write`） | `chapters/chNN.md`（**直接 `write_text`，无归档、非原子**）+ `save_ledger`（outline 状态自动推进） | ① EditMark 草稿锁（作者优先于 force）；② RunKindGuard（reconcile 轮拒写）；③ 阶段门（仅 writing/revising）；④ 章纲门（章须在 outlines）；⑤ 角色门（须有角色卡）；⑥ force 覆盖保护；⑦ warning 级预检（手法未锁定/伏笔悬空）进 details | P14 迁为 Patch/Proposal 提交，与人工写入共享版本历史与守卫（agent 直写正文与作者受管写保护不对等，见 §7 注 ①） |
| `read_chapter` | 同上（`execute_read`） | 只读 | 缺章报错附可用章节列表 | 退出 |
| `list_chapters` | 同上（`execute_list`） | 只读 | 空目录合法空结果 | 退出 |
| `method_lock` | `tools/method_tools.py`（`lock_execute`） | method.yaml（status→locked + history 追加，整表覆盖） | `reject_intake_write`；重复锁定/终态拒绝 | P13 领域命令（锁定=冻结语义保留） |
| `method_unlock` | 同上（`unlock_execute`） | method.yaml（status→drafted + history 追加） | `reject_intake_write`；未锁定/终态拒绝；locked→drafted 这条边仅归本工具 | 同上 |
| `story_stage` | `tools/story_tools.py`（`create_story_tools.execute`） | story.yaml（stage + stage_history 留痕） | ① 迁移表校验（`bible.story_state.validate_transition`）；② 确认门前置条件（`_derive_prereqs`：角色≥2/手法全锁定/章纲≥1）；③ advance/rollback 均须 reason；④ 外部守卫 `manuscript.advance_guard.StageAdvanceGuard` 单回合 advance 限 1 次（`before_tool_call` 拦截） | P13 六阶段状态机退出，改授权与写入控制（DESIGN §11） |
| `timeline_check` | `tools/timeline_tools.py`（`check_execute`，纯函数在 `bible.timeline_check`） | 只读 | 无冲突=合法空结果 | 退出（审计工作流的一部分，P16） |

工具层外挂守卫（不改写盘，但参与写控制）：`manuscript.guards.attach_guards`（装配点：`cli.build_harness` 与 `HastingsSession.from_workspace`）、`manuscript.advance_guard.StageAdvanceGuard`、`manuscript.prose_guard.ProseLeakGuard`（正文直出经 steer 纠偏）。

## 3. REST / WS / CLI 入口

### 3.1 REST（`server/rest.py` `create_rest_router`；装配于 `server/app.py` `create_app`）

| 入口 | 方法/路径 | 调用的模块 | 是否写数据 | V2 归属（interfaces 层） |
|---|---|---|---|---|
| health | GET `/api/health` | `app.py` 内联 | 否 | P17 interfaces |
| workspaces 列表 | GET `/api/workspaces` | `registry.list_slugs` | 否 | 同上 |
| 新建工作区 | POST `/api/workspaces` | `registry.create_workspace` | **是**（建目录） | 同上 |
| 删除工作区 | DELETE `/api/workspaces/{slug}?confirm=` | `registry.delete`（rmtree） | **是**（整目录删除） | 同上 |
| provider 目录 | GET `/api/provider-catalog`、GET `/api/providers` | `provider_catalog`、`provider_config` | 否 | 同上 |
| provider 增改 | PUT `/api/providers/{name}` | `_persist_book` → `provider_config.save_server_config` | **是**（config.yaml） | 同上 |
| provider 删除 | DELETE `/api/providers/{name}` | 同上 | **是**（config.yaml） | 同上 |
| provider 模型发现/连通测试 | GET `/api/providers/{name}/models`、POST `/api/providers/test` | `rest.discover_models` | 否（外部 API 调用） | 同上 |
| Ollama 三态/目录/已装 | GET `/api/ollama/status`、`/catalog`、`/models` | `server.ollama` | 否 | 同上 |
| Ollama 下载/删除 | POST `/api/ollama/pull`、DELETE `/api/ollama/models/{name}` | `server.ollama` | **是**（写 Ollama 服务端，非工作区） | 同上 |
| 会话列表 | GET `/api/{slug}/sessions` | `session_store.list_sessions` | 否（可能触发迁移写，见 §7 注 ⑤） | 同上 |
| 新建/切换/删除/重命名会话 | POST `/api/{slug}/sessions`、POST `.../activate`、DELETE `.../{session_id}`、PATCH `.../{session_id}` | `session_store.create_session`/`activate_session`/`delete_session`/`rename_session` + `registry.rebuild` | **是**（sessions.json；delete 另 unlink 会话文件） | 同上 |
| 手动压缩 | POST `/api/{slug}/session/compact` | `session.compact_context` → `context_manager.compact_context` | **是**（重写 sessions/<id>.jsonl + 归档副本 + events.jsonl 留痕） | P16 Context Builder，P17 接口层 |
| 状态/账本/统计/生命周期/审计 | GET `/api/{slug}/state`、`/bible/{entity_type}`、`/stats`、`/story`、`/audit/{chapter_id}` | `bible.store` 读、`bible.stats`、`bible.audit` | 否 | 同上 |
| 章节列表/读章 | GET `/api/{slug}/chapters`、`/chapters/{chapter_id}` | `manuscript.store` 读 | 否 | 同上 |
| 作者草稿锁 | PUT / DELETE `/api/{slug}/chapters/{chapter_id}/edit-mark` | `session.edit_marks`（`manuscript.write_guard.EditMarkRegistry`） | **纯内存互斥**（不落盘，改变写控制状态） | P12 Lease + 乐观版本 |
| 作者受管保存 | PUT `/api/{slug}/chapters/{chapter_id}` | `manuscript.store.save_chapter` + `session.request_reconcile` | **是**（chapters/*.md + .revisions + 触发对账） | P14 统一提交；P15 Change 工作流 |
| 事件流查询/导出 | GET `/api/{slug}/events`、`/events/export` | `events_store.EventStore.read` | 否 | P17 interfaces |
| 工作区全量导出 | GET `/api/{slug}/export` | `rest.build_export_zip`（内存 zip，不含 .revisions） | 否 | 同上 |
| 设置读/写 | GET / PUT `/api/{slug}/settings` | `settings.load_settings`/`save_settings` + `registry.rebuild` 或 `session.apply_*` | **是**（settings.yaml） | 同上 |
| 语料三件套 + 文风档案 | GET `/api/corpus`、POST `/api/corpus`、DELETE `/api/corpus/{name}`、GET `/api/corpus/style-profile` | `rag.corpus_store`、`rag.style_profile` | POST/DELETE **是**（corpus/） | P14 Author Model 证据源 |

### 3.2 WS（`server/ws.py` `websocket_endpoint`，路径 `/ws/{slug}`）

| 入口 | 命令帧 type | 调用的模块 | 是否写数据 | V2 归属 |
|---|---|---|---|---|
| WebSocket | `prompt` | `session.auto_compact`（可能重写会话文件）+ `session.prompt` → harness → 各工具 | **是**（间接：工具写账本/正文；压缩重写 sessions/<id>.jsonl；events.jsonl 全量落帧） | P16 agents + P17 interfaces |
| WebSocket | `steer` / `follow_up` / `abort` | `session.steer`/`follow_up`/`cancel` | 否（改变运行态） | 同上 |
| WebSocket | `get_state` / `get_messages` | `session.context_usage`/`messages` | 否 | 同上 |
| WebSocket | `sync` | `session.resync_reconcile` → `server.reconcile.ReconcileQueue.resync` | **是**（对账轮经 bible_* 工具写账本） | P15 Change 工作流 |

事件帧（assistant 流、notice、token_usage 等）只推送不落盘（落盘由 session 内订阅者完成）。

### 3.3 CLI

| 入口 | 方法/命令 | 调用的模块 | 是否写数据 | V2 归属 |
|---|---|---|---|---|
| `python -m hastings <slug>` | `hastings.__main__.main` → `cli.run_repl` | `cli.build_harness`、`session_persist.restore_and_attach` | 自由输入 = prompt 流（**是**，同 WS prompt）；每轮前 `refresh_system_prompt` + `compact_context`（可能重写会话文件） | P17 interfaces（CLI 保留为薄壳） |
| REPL `/bible [type]` | `cli.bible_command_output` | `bible.store` 直读（唯一绕过工具层读 YAML 的地方） | 否 | 同上 |
| REPL `/compact` | `cli.compact_command_output` | `context_manager.compact_context` | **是**（同手动压缩） | P16 Context Builder |
| REPL `/sessions`、`/session <id>`、`/new` | `cli.format_sessions`、`session_store.activate_session`、`create_session` | `session_store` | `/session`、`/new` **是**（sessions.json） | P17 |
| REPL `/quit` `/exit` `/q` | 退出（`prose_guard.warn_empty_chapters` 空稿告警） | — | 否 | — |
| `python -m hastings.server` | `server/__main__.py` `main` → uvicorn | `provider_config.load_server_config`、`registry`、`app.create_app` | 启动本身不写数据（只读 config.yaml/env） | P17 |

## 4. session、lock、reconcile、events 的所有者

| 机制 | 实现位置 | 状态存放 | 生命周期 | V2 替代物（仓库 Lease+乐观版本 / Change 工作流 / Commit Sequence+Outbox） |
|---|---|---|---|---|
| 会话消息持久化（记忆账） | `session_persist.py`：`attach_session_storage`（message_end 订阅）、`restore_and_attach`、`load_session_messages`；坏文件泄压：`quarantine_session_file` | `sessions/<active_id>.jsonl` | 随 `registry.rebuild` 重装配重放；压缩时 `_persist_compacted` 全量重写；OS 故障只告警不断流 | P16 会话退出「项目记忆」地位；上下文按任务构建（DESIGN §8/§13） |
| 会话索引（多会话） | `session_store.py`：`load_index`（损坏→备份重建）、`create_session`/`activate_session`/`rename_session`/`delete_session`/`touch_session`（均经 `_write_index` 原子替换） | `sessions.json` | 惰性初始化/旧格式迁移（`_init_or_migrate`）；删除活跃会话自动切换或新建 | P16/P17 迁入 repository；「活跃会话」概念随上下文任务化退化 |
| 作者草稿锁（人机互斥的 agent 侧） | `manuscript/write_guard.py` `EditMarkRegistry`（挂 `HastingsSession._edit_marks`）；HTTP 入口 PUT/DELETE edit-mark；保存成功自动释放（`rest.update_chapter`） | **纯内存**（进程重启清空；单用户无锁超时/跨进程锁，已知限制） | 逐章 acquire/release | 仓库 Lease + 乐观版本（重建不能丢互斥状态，DESIGN §11） |
| 运行类别守卫（对账期禁写正文） | `manuscript/write_guard.py` `RunKindGuard`（单槽位）；置位/复位在 `server/reconcile.py` `_run_one`；`tools/chapter_tools.execute_write` 经闭包读 | **纯内存** | 随对账轮 begin/end | Change 工作流的授权语义（对账=可审阅的领域变化，不是运行期旗标） |
| HTTP 侧互斥（作者保存守门） | `server/rest.py` `update_chapter` 的 `is_running/is_compacting` 检查（409）；各会话操作经 `_reject_while_running` | 会话运行态（harness 内存） | 每请求检查 | Lease + 乐观版本统一裁决 |
| busy/压缩互斥 | `server/session.py` `AgentBusyError`；`server/hastings_session.py` `prompt`（三重检查：未配模型/running/compacting）+ `_compaction_lock`（per-session asyncio.Lock） | **纯内存** | 进程内 | Lease |
| 对账队列 | `server/reconcile.py` `ReconcileQueue`：`enqueue`（REST 保存后）→ debounce 合并 → 等闲出队 `_pump`/`_retry_when_idle` → 以 user 消息进同一 harness；失败保留 `_last` 供 `resync` | **纯内存**（`_pending`/`_ready`/`_last`，进程重启丢失待对账差异） | 每会话一个；`close` 解绑 | Change/Impact/Revision 工作流（对账不是消息同步，DESIGN §11） |
| registry 会话缓存 | `server/registry.py` `WorkspaceRegistry`：`get_or_create`（per-slug asyncio.Lock + 双检）、`rebuild`、`is_current`（WS 防陈旧实例写会话文件）、`peek`、`delete` | **纯内存**（slug→WritingSession） | 懒装配、常驻不驱逐 | P17 归入 application/interfaces 装配 |
| 事件账本 | `events_store.py` `EventStore`：`append`（asyncio.Lock 串行 + seq 自增）、`_scan_last_seq`（重启续号）、`read`（倒序分页/分类/整账统计） | `events.jsonl`（append-only） | 每会话实例一份（`HastingsSession.__init__` 挂载）；写盘失败 stderr 告警不中断；`message_update` 不入库 | 统一 Commit Sequence + Outbox（多实例序号问题点，DESIGN §10/§11） |
| 六阶段生命周期 | `bible/story_state.py`（StoryDoc/迁移表/确认门）+ `tools/story_tools.py`（唯一写入口）+ `manuscript/advance_guard.py`（限速守卫） | `story.yaml` | 惰性落盘（旧工作区兼容） | 退出全局状态机 → 退出授权与写入控制（P13/P15） |

## 5. 对应测试映射

| 模块/机制 | 测试文件（tests/ 下） | 覆盖点 |
|---|---|---|
| `bible/store.py` | `test_bible_store.py` | YAML 往返、非法名拒绝、空库行为 |
| `bible/state_machine.py` | `test_state_machine.py` | 合法边全过、非法边三要素、新建非初始拒绝 |
| `bible/story_state.py` | `test_story_state.py` | 生命周期迁移、确认门前置条件 |
| `bible/timeline_check.py` | `test_timeline_check.py` | R0/R1/R2 规则命中、干净账本零误报 |
| `bible/stats.py` | `test_stats.py` | 章号解析/风险派生/全量统计 |
| `bible/audit.py` | `test_audit.py` | 三指标口径 + 四条检查规则 + 泄压阀 |
| `manuscript/store.py` | `test_manuscript_store.py` | .revisions 归档 + 主文件写、revision=归档数+1、原子替换 |
| `manuscript/diff.py` | `test_manuscript_diff.py` | 空行分段 + SequenceMatcher hunks |
| `manuscript/write_guard.py` | `test_manuscript_write_guard.py` | EditMark 注册表（holder/ts/幂等释放） |
| `manuscript/advance_guard.py` | `test_advance_guard.py` | 单回合 advance 限速、成功落盘才计数、rollback 不限次 |
| `manuscript/prose_guard.py` | `test_prose_guard.py` | 正文直出判定/同回合一次/AgentStart 重置 |
| `manuscript/guards.py` | （经 `test_cli.py`、`test_advance_guard.py` 间接） | 装配接线 | 
| `tools/bible_tools.py` | `test_tools_bible.py` | 五账本正常路径 + 每条约束非法输入 |
| outline 第六账本 | `test_tools_outline.py` | 状态机/章号唯一/泛化读写/确认门接线 |
| `tools/chapter_tools.py` | `test_tools_chapter.py`、`test_tools_chapter_precheck.py` | 落盘格式/参数约束/覆盖保护/读回；分级预检 + outline 自动推进 |
| `tools/method_tools.py` | `test_tools_method.py` | lock/unlock 全链路 + upsert 锁定拦截 + 审计断言 |
| `tools/story_tools.py` | `test_tools_story.py` | 推进/回退全链路 + 确认门派生 + 留痕 |
| `tools/timeline_tools.py` | `test_tools_timeline.py` | 空账本自洽、冲突进 details、章节范围过滤 |
| `tools/errors.py` | （各 tools 测试内嵌） | ToolInputError 回喂语义 |
| `settings.py` | `test_settings_store.py` | 默认值/往返/结构损坏 |
| `server/provider_config.py` | `test_provider_config.py` | env 回落/结构损坏/保存往返/脱敏 |
| `provider_catalog.py` | `test_provider_catalog.py` | 内建目录字段完整性 |
| `events_store.py` | `test_events_store.py` | 写/读/分页/分类/泄压阀 |
| `session_store.py` | `test_session_store.py` | 索引 CRUD + 旧单会话迁移 + 损坏重建 |
| `session_persist.py` | `test_session_persist.py` | Windows+asyncio 落盘、坏文件隔离、restore 接线 |
| `context_manager.py` | `test_context_manager.py` | 估算纯函数/剥离/摘要/持久化分层 |
| `server/session.py` + `hastings_session.py` | `test_server_session.py` | 协议实现、busy/压缩互斥、事件订阅 |
| `server/registry.py` | `test_server_registry.py` | 懒装配/缓存/rebuild/删除 |
| `server/reconcile.py` | `test_server_reconcile.py` | debounce 合并、用户优先、失败重跑、CP2 端到端 |
| `server/rest.py` | `test_server_rest.py` | REST 面全量（含受管写端点 revision 冲突） |
| `server/ws.py` | `test_server_ws.py` | 帧协议/事件桥/命令分发 |
| `server/app.py` | `test_server_app.py` | 工厂依赖注入 + health |
| `server/__main__.py` | `test_server_main.py` | 参数解析、env 指引、多档案引导 |
| `server/ollama.py` | `test_server_ollama.py`、`test_ollama_client.py` | REST 三态/流式 pull/删除；客户端帧解析 |
| `rag/corpus_store.py`（经 REST） | `test_corpus_api.py` | 语料端点泄压阀三情形 |
| `rag/style_profile.py` | `test_style_profile.py` | 分句/六桶/对话占比/分段/聚合 |
| `prompts.py` | `test_prompts.py` | system 组装 + 对账消息模板 |
| `cli.py` | `test_cli.py` | build_harness 与 Web 端同链路装配 |
| `hastings/__main__.py` | `test_entry.py` | 环境变量指引与 BASE_URL 归一化 |
| agent loop 集成 | `test_writing_loop.py`（FakeProvider 全剧本）、`test_hello_harness.py`（最小回放）、`test_renderer.py`（PlainRenderer） | 工具调用契约/事件渲染 |
| eval driver | `eval/driver.py`（无独立单测，待核对） | 走查产物生成 |

## 6. V2 目标模块与迁移阶段映射

V2 六层：作者工作台（interfaces）→ 用例编排（application）→ 命令/守卫/授权 → 仓库（domain/repository）→ 无状态 Agent（agents）→ 基础设施（infrastructure）；领域层 = domain/story + domain/manuscript。阶段依赖：P11→P12→{P13,P14}→P15→P16→P17（见 plans/README.md）。

| 现状组件 | V2 目标模块 | 迁移阶段 | 可删除旧代码的最终条件 |
|---|---|---|---|
| `bible/models.py`、`bible/state_machine.py` | domain/story | P13 | P13 领域命令 + Story Snapshot 契约测试通过；旧工具不再被任何装配点引用 |
| `bible/store.py`（模型与存储混杂） | 模型入 domain/story；读写端口入 domain/repository；YAML 实现入 infrastructure | P12（端口）+ P13（模型） | P13 完成 && 旧账本读路径全部改经 repository 查询面 |
| `bible/story_state.py`（六阶段） | 退出（改退出授权与写入控制） | P13/P15 | P15 影响分析替代阶段门后，story.yaml 写入口归零 |
| `tools/bible_tools.py`（bible_upsert） | application 领域命令（意图明确的命令替代泛化 upsert） | P13 | P13 命令通过契约测试 && bible_upsert 从 `create_writing_tools` 移除 |
| `tools/method_tools.py`、`tools/story_tools.py` | application 领域命令 | P13 | 同上 |
| `tools/chapter_tools.py`（write_chapter 写路径） | domain/manuscript Patch + application 提交命令（Proposal 化） | P14 | P14 人工/Agent 共享版本历史闭环 && write_chapter 不再直写盘 |
| `manuscript/store.py`（save_chapter/.revisions） | domain/manuscript 版本化 + 统一提交 | P14 | P14 Commit 历史覆盖 .revisions 语义（含导入迁移后） |
| `manuscript/diff.py` | domain/manuscript（Patch hunk） | P14 | 随 P14 收编 |
| `manuscript/write_guard.py`（EditMark/RunKind） | domain/repository Lease + 乐观版本 | P12（乐观版本）/P14（Lease 接线） | Lease 经 registry 重建不丢（P14 验收）&& EditMark 内存态退役 |
| `manuscript/advance_guard.py`、`prose_guard.py`、`guards.py` | application 守卫 / agents 工作流约束 | P16 | P16 四条工作流接管后旧守卫装配点移除 |
| `server/reconcile.py` | application Change/Impact/Revision 工作流 | P15 | P15 影响报告贯通 Canon→Plan→正文 && ReconcileQueue 不可达 |
| `context_manager.py` | application 任务化 Context Builder | P16 | P16 Context Package 接管 prompt 构建 && compact_context 不可达 |
| `session_persist.py`、`session_store.py` | infrastructure（会话文件）→ 逐步退出（会话不再是项目记忆） | P16 迁移、P17 收口 | P16 无状态 Agent + Job 持久化上线 && 会话文件只剩导入用途 |
| `events_store.py` | infrastructure Commit Sequence + Outbox | P12 | P12 Outbox 契约测试通过 && events.jsonl 只读回放、无新写 |
| `server/registry.py`、`server/session.py`、`server/hastings_session.py` | application 装配 + interfaces 会话面 | P16（会话语义）+ P17（接口收口） | P17 端到端验收 && 旧会话协议下线 |
| `server/rest.py`、`ws.py`、`app.py`、`cli.py`、`__main__.py`、`server/__main__.py` | interfaces | P17（期间分阶段复用，先换后端契约） | 新契约全覆盖 && 旧写端点返回不可达（P17 证明） |
| `server/provider_config.py`、`server/ollama.py`、`provider_catalog.py` | infrastructure 模型接入 | P12 起保留（tau_ai/tau_agent 同） | 接入端口定义后实现归位；无独立删除事件 |
| `rag/corpus_store.py`、`rag/style_profile.py` | Author Model 证据源（domain/application） | P14 | P14 偏好证据机制上线 && style-profile 端点改走新证据链 |
| `settings.py` | 域配置（application/infrastructure） | P13/P14 随迁 | 旧 settings.yaml 键被导入器映射后 |

## 7. 已知写入口完整清单（Phase 12 红线依据）

Phase 17「旧写入口不可达证明」的核对底表。分四组：A=工作区数据（ stories/），B=服务级数据，C=外部系统，D=周边产物。

**A. 工作区数据**

1. `bible.store.save_character` — 写 `bible/characters/<name>.yaml`（覆盖写）
2. `bible.store.save_ledger` — 整表覆盖 clues/foreshadows/method/timeline/outlines 五账本
3. `bible.store.save_story_doc` — 整文件覆盖 `story.yaml`
4. `bible.store.StoryWorkspace.ensure_dirs` — 创建 `bible/characters/`、`chapters/` 目录
5. `tools.bible_tools.upsert_execute`（bible_upsert）— 经 1/2 落盘
6. `tools.method_tools.lock_execute` / `unlock_execute` — 经 `save_ledger` 写 method.yaml
7. `tools.story_tools.create_story_tools.execute`（story_stage）— 经 `save_story_doc` 写 story.yaml
8. `tools.chapter_tools.execute_write`（write_chapter）— **直接 `path.write_text` 写 `chapters/chNN.md`**（无归档、非原子）+ `save_ledger` 推进 outline
9. `manuscript.store.save_chapter` — 归档 `.revisions/<id>/NNN.{md,json}` + `_replace_atomically` 原子替换主文件
10. `settings.save_settings` — 整表覆盖 `settings.yaml`
11. `session_store._write_index` — 原子替换写 `sessions.json`（`create_session`/`activate_session`/`rename_session`/`delete_session`/`touch_session`/`_init_or_migrate`/`load_index` 修复路径共用）
12. `session_store._init_or_migrate` — 建 `sessions/` 目录 + 旧 `session.jsonl` rename 归位
13. `session_store.delete_session` — `unlink` 会话文件 + 重写索引
14. `session_persist.attach_session_storage._persist` — 经 tau `JsonlSessionStorage.append` 追加 `sessions/<id>.jsonl`（message_end 触发）
15. `session_persist.quarantine_session_file` — 坏会话文件 rename 备份
16. `session_persist.archive_session_file` — copy2 归档副本 + unlink 原会话文件
17. `context_manager._persist_compacted` — **全量重写** `sessions/<id>.jsonl`（open("w")）+ 经 16 归档
18. `events_store.EventStore._write_line`（经 `append`/`append_event`）— 追加 `events.jsonl`
19. `server.registry.WorkspaceRegistry.create_workspace` / `get_or_create` / `rebuild` — ensure_dirs 建目录
20. `server.registry.WorkspaceRegistry.delete` — `shutil.rmtree` 整工作区目录
21. `server.hastings_session._on_agent_end` → `context_manager.record_token_usage` → 18（间接，token_usage 帧）
22. `server.hastings_session._persist_notice` → 18（间接，对账 notice 帧）
23. `session_persist.attach_session_index._touch` → `session_store.touch_session` → 11（间接，message_end 刷索引）

**B. 服务级数据**

24. `server.provider_config.save_server_config`（经 `server.rest._persist_book` ← PUT/DELETE `/api/providers/{name}`）— 写 `config.yaml`（api_key 明文）
25. `rag.corpus_store.CorpusStore.save`（POST `/api/corpus`）— 写 `corpus/<name>.txt|.md`
26. `rag.corpus_store.CorpusStore.delete`（DELETE `/api/corpus/{name}`）— unlink 语料文件

**C. 外部系统**

27. `server.rest.ollama_pull` / `ollama_delete` — 写 Ollama 服务端模型库（非本地文件）

**D. 周边产物（工作区外，不属 Phase 17 红线）**

28. `eval/driver.py` — 向 out_dir 写评测转录与正文产物

**纯内存写控制状态（不落盘，但影响数据一致性，P12 Lease 盘点对象）**：`EditMarkRegistry.acquire/release`、`RunKindGuard.begin/end`、`ProviderBook.update_config`、`ReconcileQueue.enqueue/resync/_run_one`、`HastingsSession.apply_model/apply_writing_language`、`context_manager` 的 harness.messages 替换。

### 注（发现的意外 / 待核对项）

1. **正文双写入口保护不对等**：agent 的 `write_chapter`（条目 8）直接覆盖主文件——无 `.revisions` 归档、无原子替换、无 base_revision 冲突检查；作者 PUT `/chapters`（条目 9）三者俱全。这正是 DESIGN §11「人工和 Agent 写入必须共享历史与守卫」的现状证据，P14 的首要收编对象。
2. **「待核对」疑似设置字段擦除**：`server.rest.update_settings` 落盘时重建 `StorySettings(writing_language, provider_profile, model)`，**未保留 `current.context_window_tokens`**——曾手工把窗口调小的旧工作区（如本地 32k 模型）一旦在设置页保存即被重置回默认 128000。与 DESIGN §11 对 bible_upsert「易擦除字段」的批评同型，建议在 P13/P14 命令化时修复。
3. `config.yaml` api_key 明文落盘为 `save_server_config` 文档声明的已知取舍（保密边界=文件系统权限），V2 需显式决定是否保留。
4. **events 序号是实例级而非存储级**：`EventStore` 挂在 `HastingsSession` 上，`registry.rebuild` 即新建实例，续号依赖 `_scan_last_seq` 扫文件末行——即 DESIGN §11 所指「多实例序号」问题的具体落点，P12 Outbox 需覆盖。
5. **读路径的隐式迁移写**：GET `/api/{slug}/sessions`、`session_persist.session_file` 等读入口经 `load_index` 可能触发 `sessions.json` 初始化/旧格式 rename（条目 11/12）——V2 迁移器需把这类「读时迁移」显式化。
6. `tests/` 各文件的覆盖点描述取自其模块 docstring；`manuscript/guards.py` 与 `tools/errors.py` 无独立测试文件（经其他测试间接覆盖）；`eval/driver.py` 无独立单测（待核对）。

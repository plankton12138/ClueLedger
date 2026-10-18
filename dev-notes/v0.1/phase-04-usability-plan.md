# Phase 4 开发计划：实用化（会话持久化 + 正文记忆 + 多工作区）

> **用户决策插入阶段**：暂缓 RAG 与审计/评测（原路线图 P4–P6 顺延为 P5–P7），优先把程序补到「日常可用状态」。
> 状态：**已完成并关闭**（2026-10-09，R5 验收通过，构建笔记见 [phase-04-usability.md](phase-04-usability.md)）。

## 0. 已核实的依赖事实（计划的输入）

| 结论 | 证据 | 对本阶段的影响 |
|---|---|---|
| `JsonlSessionStorage` 现成：append-only、跨进程文件锁、批量原子写（temp+fsync+replace）、崩溃恢复 | [storage.py](../../src/tau_agent/session/storage.py#L38-L134) | 持久化**零内核改动**，推翻 D5 时代「语义耦合需改内核」的担忧——线性用法完全绕开 |
| `MessageEntry(message=AgentMessage)` 公开 pydantic 模型，`parent_id` 可选 | [entries.py](../../src/tau_agent/session/entries.py#L35-L40) | 桥接只需「message_end 事件 → MessageEntry → append」，树分支（parent_id）本阶段不用（P5 审计员再评估） |
| `entries_from_json_lines` 遇坏行抛 `SessionJsonlError` | [jsonl.py](../../src/tau_agent/session/jsonl.py#L35) | 启动加载需泄压阀：坏文件不静默丢，改名备份 + 中文指引 |
| 工具清单确认无正文读回：仅 bible_upsert/bible_query/write_chapter/method_lock/method_unlock/timeline_check | grep `name="..."` | `read_chapter`/`list_chapters` 全部新建 |
| `write_chapter` 固定写 `chapters/chNN.md`（两位数） | [chapter_tools.py](../../src/hastings/tools/chapter_tools.py#L50) | read 工具按同一命名约定读，无歧义 |
| P3 服务端单工作区：`create_app(session, workspace)` 单实例绑定；WS 挂 `/ws`；REST 无 slug | [app.py](../../src/hastings/server/app.py) | 多工作区 = 注册表 + 路由参数化，REST 契约破坏性变更（声明无兼容层） |
| 前端 `loadSnapshot` 已是「重置后重放」语义（R3 修复） | [store.ts](../../frontend/src/state/store.ts#L30-L42) | 工作区切换 = 换 URL 重连，前端零额外重置逻辑 |
| harness 事件经 `subscribe` fan-out，`message_end` 携带定稿消息 | P3 已验证 | 持久化挂在 message_end 上，与前端消费同源不同消费者 |

## 1. 目标与验收标准

**目标**：补齐三块日常使用短板——①服务/进程重启后会话不丢；②agent 能读回自己写过的正文（长期记忆从「账本摘要」扩到「原文细节」）；③多故事并存，浏览器自由切换。

可检查的六条：

1. **会话持久化（Web）**：对话到一半杀掉服务进程 → 重启同 slug → 浏览器刷新 → 对话流完整恢复（JSONL 回放）；
2. **会话持久化（CLI）**：REPL 退出重进同 slug → `harness.messages` 与退出前一致，可继续对话；
3. **正文读回**：agent 能 `read_chapter` 读回任意已写章节原文、`list_chapters` 看全目录；prompt guidelines 引导「写新章前读前章结尾、回收伏笔前核对埋设原文」；
4. **多工作区**：两个 slug 同时存在于一个服务进程，浏览器切换互不串数据；可新建工作区；
5. **泄压阀**：损坏 session.jsonl → 备份改名 + 中文指引（不静默丢、不裸 500）；read 不存在的章节 → is_error + 可用章节列表；
6. **红线照旧**：tau 内核零改动、零 import tau_coding、REST 业务数据零写入口（新建工作区的 POST 是资源创建，不碰 Bible）；七道门全绿。

**非目标**：
- 不做 RAG / 语料上传 / style_search（顺延 P5）；
- 不做会话树分支/回滚（parent_id 留空，P5 审计员阶段评估）；
- 不做跨工作区全局搜索、工作区删除/归档（列表+新建+切换即够日常）；
- 不做鉴权与多用户（localhost 形态不变）；
- session.jsonl 无限增长不做 compaction（单故事量级小，留白记录）。

## 2. 设计决策

### D1 持久化接线：`attach_session_storage(harness, storage)` 订阅式桥，CLI 与 server 共用

- 新模块 `hastings/session_persist.py`：`load_messages(storage) -> tuple[AgentMessage, ...]` + `attach_session_storage(harness, storage) -> None`（内部 subscribe，`message_end` → `MessageEntry` → `storage.append`）；
- **CLI 与 server 都接线**：`__main__.py`（REPL）与 `server/__main__.py` 各加三行——长期记忆不是 Web 特性；
- 写入时机 = `message_end`（消息定稿才落盘）：流式中间态不写，abort/error 后 harness 的 repair 消息同样是 message_end 事件，天然被记录；
- 会话文件布局：`stories/<slug>/session.jsonl`——会话与故事同目录，整个 `stories/<slug>/` 就是可拷贝的完整档案。

### D2 启动加载与坏文件泄压阀

- 启动时 `read_all()` → 过滤 `type=="message"` → `replace_messages`；`model_change` 等 entry 类型本阶段忽略（记录但不消费）；
- `SessionJsonlError` → 把坏文件改名 `session.jsonl.bad-<ts>`，打印中文指引（哪里坏/为什么/怎样恢复），以空会话继续启动——**不阻塞、不静默**；
- 跨进程锁由 storage 自带（`.session.jsonl.lock`），同 slug 双开服务时后启动者收到文件锁保护，行为等于「只读竞速」，不在应用层再加锁（不过度设计）。

### D3 正文读回：`read_chapter` + `list_chapters` 两个只读工具

- `read_chapter(chapter: int)`：返回 `chapters/chNN.md` 全文（TextBlock）+ details{path, chars}；不存在 → is_error，错误消息附 `list_chapters` 结果摘要（泄压阀：指明下一步）；
- `list_chapters()`：`[{chapter, title, chars}]`——agent 规划「写到第几章、前文有什么」的依据；
- prompt guidelines 增补使用时机（写新章前读前章结尾 / 回收伏笔前核对埋设原文 / timeline_check 报矛盾后核对相关章节）；guidelines 单一事实源原则照旧（prompt 与工具描述同步改）。

### D4 多工作区：`WorkspaceRegistry` + slug 进路径的破坏性 REST 变更

- `hastings/server/registry.py`：`get_or_create(slug)` 懒加载（workspace + session + storage + tools 一次装配），常驻不驱逐（单用户个位数故事）；`list_slugs()` 扫 `stories/` 目录；
- 路由变更（**无兼容层**，项目无外部消费者）：`/ws/{slug}`；`/api/{slug}/state|bible/{type}|chapters[/{id}]`；`/api/workspaces`（GET 列表 / POST 新建——唯一的写 REST，属资源创建不碰业务数据）；`/api/health` 保留全局；
- 一个 slug 一个 harness：互不共享状态，P3 的每连接写锁语义不变；不存在跨工作区并发写竞争（不同故事物理上不同文件）；
- slug 校验沿用 P2 的穿越防护（拒绝 `..`、Windows 保留设备名），新建入口同样过闸。

### D5 前端：工作区切换 = 换 URL 重连，快照重置免费获得

- 头部加工作区下拉（`GET /api/workspaces`）+ 新建对话框（POST 后切换）；
- 切换 = `location` 不变、socket 重连 `ws/{newSlug}` + REST 前缀换 slug；`loadSnapshot` 的「重置后重放」语义（R3 修复）直接消化切换时的状态更替——**上一工作区的事件帧/条目不残留**；
- Composer/ChatStream/BiblePanel 零改动（数据源仍是 store，slug 只是 store 里的一个字段）。

### D6 CLI 契约不变

- `python -m hastings <slug>` 行为不变，仅内部多了加载/保存；REPL 可加 `/sessions` 之类的信息命令吗？——不做，非必要不加命令。

## 3. 任务分解

### T1 持久化桥（测试先行）

- `hastings/session_persist.py`：load/attach 两函数；
- 测试：FakeProvider 剧本跑一轮 → session.jsonl 行内容逐条断言（MessageEntry JSON 保真）；新 harness load 后 `messages` 与原 harness 一致；model_change/custom entry 不炸；坏行 → SessionJsonlError 路径 → 备份改名 + 指引文案断言。

### T2 CLI + serve 接线（测试先行）

- 两个 `__main__.py` 接线；入口测试补「启动时加载已有会话」断言；
- 手动冒烟：CLI 写一条 → 退出 → 重进 → 继续。

### T3 read_chapter + list_chapters（测试先行）→ CP1

- 工具实现 + 注册 + guidelines 增补；工具层测试（存在/不存在/泄压阀文案）；
- 集成剧本：agent 在 FakeProvider 下读回第 1 章再写第 2 章；
- **CP1**：记忆链路核对——session.jsonl、Bible、chapters 三类文件在同一工作区目录各就各位。

### T4 WorkspaceRegistry + REST slug 化（测试先行）

- registry 单元测试（懒加载/穿越防护/list）；REST 全端点迁移到 `/api/{slug}/...` + workspaces 端点；
- 既有 server 测试同步改路径（破坏性变更落地）。

### T5 WS `/ws/{slug}`（测试先行）

- ws.py 从 app.state 取会话改为按路径参数取 registry；既有 WS 测试改 URL + 新增「双工作区事件隔离」剧本（A 的事件不出现在 B 的连接里）。

### T6 前端切换器 + 新建工作区 → CP2

- store 加 `slug` 字段与 workspaces 列表切片；头部下拉 + 新建对话框；REST 客户端加 slug 前缀；
- vitest：切换重连 → 快照重置（复用 R3 语义断言）；

**CP2**：浏览器联调——双工作区切换 + 重启恢复冒烟。

### T7 七道门 + 红线 + 浏览器人工验收

- 六条验收标准逐条过（重点：杀进程重启恢复、双工作区隔离）；截图/transcript 存 `dev-notes/v0.1/artifacts/`。

### T8 构建笔记 + R5

- `phase-04-usability.md`：D1–D6 落锤、四透镜专节（预期重点：持久化即「事件流的第三个消费者」、泄压阀的坏文件处置、slug 即资源边界的 REST 语义）、七门与红线输出、R3/R5 结论；DESIGN.md 路线图标注顺延。

## 4. 评审方案

- **R1** 每笔提交前七道门（照旧）；
- **R2** 红线：内核 diff 为空 + 零 import tau_coding + REST 业务零写入口（grep 复核 POST 只许 /api/workspaces）；
- **R3** code-review skill 全量 diff，重点清单：①storage 锁与事件循环的兼容（async append 在 Windows 文件锁上）；②message_end 之外的定稿路径有没有漏记（repair 消息）；③registry 并发 get_or_create 的竞态（同 slug 双连接同时首访）；④REST slug 注入面（穿越/保留名）；⑤前端切换时的订阅泄漏（旧 socket 是否确已 close）；
- **R4** CP1（T3 记忆链路）/ CP2（T6 浏览器联调）；
- **R5** DoD 逐条打勾，通过 = 本阶段关闭。

## 5. DoD

- [x] Web：杀进程重启 + 刷新，对话流完整恢复
- [x] CLI：重进同 slug 会话续上
- [x] agent 可 read_chapter / list_chapters；guidelines 已引导使用时机
- [x] 双工作区并存、浏览器切换互不串数据、可新建
- [x] 坏 session.jsonl → 备份 + 指引；read 不存在章节 → is_error + 目录摘要
- [x] REST 契约变更已在构建笔记声明（无兼容层）
- [x] 七道门全绿；红线：内核 diff 为空、零 import tau_coding
- [x] 浏览器人工验收存档；构建笔记发布；R3 blocker 清零

## 6. 风险与对策

| 风险 | 概率 | 对策 |
|---|---|---|
| `JsonlSessionStorage` 的 fcntl/msvcrt 锁在 Windows + asyncio 下行为 | 中 | T1 第一用例先探路（append + read_all 最小闭环）；不行则 hastings 内自写薄存储（实现同一 Protocol），记录选型 |
| message_end 之外的定稿路径漏记（如 error 后 repair） | 中 | T1 测试剧本显式覆盖 abort 场景，断言 repair 消息在 jsonl 里 |
| registry 同 slug 并发首访竞态 | 低 | 单线程事件循环内 get_or_create 无 await 点即原子；代码评审专查（R3-③） |
| REST slug 化动全量测试 | 确定 | 接受——破坏性变更一次到位，测试路径批量替换属机械修改 |
| 前端切换时旧 socket 未关导致双连接 | 低 | reconnect 函数已先 close 再连（P3 实现），T6 测试断言单连接 |

## 7. 执行顺序

```text
T1 持久化桥（测试先行）
→ T2 CLI/serve 接线
→ T3 read/list 工具 + guidelines → CP1
→ T4 Registry + REST slug 化
→ T5 WS /ws/{slug}
→ T6 前端切换 + 新建 → CP2
→ T7 七道门 + 红线 + 浏览器验收
→ R3 代码评审
→ T8 构建笔记 + R5 关闭
```

提交策略照旧：原子提交、测试先行；每笔提交前经用户确认。

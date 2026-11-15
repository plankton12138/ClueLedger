# Phase 4 构建笔记：实用化（会话持久化 + 正文读回 + 多工作区）

> 对应 [phase-04-usability-plan.md](phase-04-usability-plan.md)，2026-10-09 完成并关闭。
> 结论：**R3 评审 blocker 清零，R5 验收通过，P4 正式关闭**；原路线图 P4–P6（RAG/审计/评测）顺延为 P5–P7。
> 用户决策背景：暂缓 RAG，优先把程序补到「日常可用状态」——重启不丢稿、agent 读得回自己写的正文、多故事并存。
> 关键词：会话持久化、read_chapter、多工作区、WorkspaceRegistry、坏文件检疫

## 1. 交付内容

提交：`0aa7a08`（P4 计划，D1–D6 决策 + T1–T8 / R1–R5）→ `f1239c2`（T1 持久化桥 `session_persist.py`，坏文件检疫改名 + 中文指引）→ `9797b65`（T2 入口接线 `restore_and_attach`，CLI 与 server 共用）→ `8fc0591`（T3 `read_chapter`/`list_chapters` 只读工具 + guidelines 引导时机 → **CP1**）→ `7ff66f0`（T4 `WorkspaceRegistry` 懒装配 + REST/WS 路由 slug 化，破坏性无兼容层）→ `e52b735`（T5 WS 双工作区事件隔离 + 坏 slug accept-then-1008 拒连）→ `38c3c4c`（T6 前端工作区下拉/新建/`?w=` 切换 → **CP2**）→ `d36fafe`、`19a53b4`（R3 修复×2：repair 落盘回归钉；落盘 IO 故障告警不杀写作流）→ `19a53b4`（验收存档 [artifacts/p4-acceptance.md](artifacts/p4-acceptance.md)）。

## 2. 关键决策（D1–D6 落锤）

### D1 持久化 = 事件流的第三个消费者

`attach_session_storage(harness, storage)` 是一个约 20 行的订阅式桥：`message_end` → `MessageEntry` → `append`。
渲染器（P1 终端）、前端（P3 事件流）、持久化（P4 jsonl）是同一事件流的三个平行消费者——**零内核改动**，
推翻了 D5 时代「持久化语义耦合需改内核」的担忧。写入时机选 `message_end`（定稿才落盘），
流式中间态不写；abort 后 harness 合成的 toolResult + repair 消息同样走 `message_end`，天然不漏记（R3-② 回归钉）。

### D2 坏文件泄压阀：检疫而非报错

启动加载遇 `SessionJsonlError`：坏文件改名 `session.jsonl.bad-<ts>`（原路径让位）+ 中文指引，空会话继续——
不阻塞启动、不静默丢数据。跨进程锁由 storage 自带（`.session.jsonl.lock`），应用层不再加锁（不过度设计）。

### D3 正文读回：只读工具 + guidelines 引导时机

`read_chapter(chapter)` 不存在时 `is_error` + 附 `list_chapters` 目录摘要（泄压阀：指明下一步）；
guidelines 增补三条使用时机（写新章前读上章结尾 / 回收伏笔前核对埋设原文 / timeline 报矛盾后核对相关章节），
prompt 与工具描述单一事实源照旧。真实端点验收：CLI 重进后 agent **零工具调用**答出「波洛（比利时侦探）」——
历史来自 jsonl 回放而非账本直读，持久化生效的直接证据。

### D4 slug 即资源边界：WorkspaceRegistry + 破坏性 REST 变更

- `get_or_create(slug)` 懒装配（workspace + harness + storage + tools 一次装配），per-slug `asyncio.Lock` + 双重检查；
  setdefault 与检查之间无 await 点，事件循环内原子——并发首访测试 `asyncio.gather` 断言 `a is b`；
- REST 全线 slug 化：`/api/{slug}/state|bible/{type}|chapters[/{id}]`、`/ws/{slug}`、`GET/POST /api/workspaces`
  （POST 为**唯一写 REST**，资源创建不碰业务数据）；`/api/health` 收敛为全局探活；
- slug 校验沿用 P2 `validate_slug`（穿越 + Windows 保留名），且在 `StoryWorkspace.__post_init__` 构造即校验——
  registry / REST / WS / 新建入口全部自动过闸，无一处旁路。

### D5 前端切换 = 换 slug 重连，快照重置免费获得

store 加 `slug` 字段；`useEffect` 依赖变更先 close 旧 socket 再连新 slug；
`loadSnapshot` 的「重置后重放」语义（P3 R3 修复）直接消化切换时的状态更替，上一工作区条目零残留。
`?w=slug` 经 `history.replaceState` 保持，刷新不丢。Composer/ChatStream/BiblePanel 零改动。

### D6 CLI 契约不变

`python -m hastings <slug>` 行为不变，仅内部多了加载/保存；不加 `/sessions` 之类信息命令（非必要不加命令）。

## 3. Agent 设计经验（四透镜）

### 3.1 错误消息是喂给模型的 API——读回工具的「目录摘要」回喂

`read_chapter` 读不存在章节时，错误消息附上 `list_chapters` 结果摘要：**哪里错**（第 N 章不存在）、
**为什么**（目录里只有这些）、**怎样算对**（先 `write_chapter` 再读，或换已有章节）。
模型拿到这条消息不会陷入「重试同一个章节号」的循环——它知道下一步该干什么。
本阶段还出现了一类新读者：**面向人的 stderr 告警**（落盘失败）同样套三要素——
哪里错（会话落盘）、为什么（disk full 原文透传）、怎样恢复（检查磁盘/文件占用后重启）。
同一配方，按读者调整措辞：给模型的指下一步动作，给人的指运维动作。

### 3.2 硬约束放工具层/内核层，prompt 只负责解释世界

持久化这条「硬约束」没有写进 prompt 求模型配合，而是挂在 `message_end`——**内核保证的定稿点**，
连 abort 后的 repair 消息都 100% 落盘（R3-② 用 cancel-on-tool-start 剧本钉死）。
同理 slug 安全：校验在 `StoryWorkspace.__post_init__` 构造即强制，四个入口（REST/WS/registry/新建对话框）**
没有一个需要「记得校验」**。prompt（guidelines）只解释世界：什么时候该读回正文、为什么回收伏笔前要核对原文。

### 3.3 泄压阀：三情形在持久化与多工作区各有落点

| 情形 | P4 落点 | 行为 |
|---|---|---|
| 违规（参数/输入不合法） | read 不存在章节；slug 穿越/保留名；WS 坏 slug | `is_error` + 目录摘要；REST 422 三要素；accept 后 close(1008) |
| 合法但空结果 | 新工作区 0 消息；空目录 list_chapters | 正常返回空列表 + 引导（「新建对话框」/「先写第 1 章」），不是错误 |
| 环境故障 | 坏 session.jsonl；落盘 OSError | 检疫改名 + 空会话继续；stderr 三要素告警，**写作流不中断**（R3-⑥） |

环境故障行的第二条是 R3 评审的产出：最初 `_persist` 裸奔，磁盘满会杀死整轮写作流——
但「历史不完整」远好于「正在写的这一章丢失」。故障隔离的粒度应该匹配故障的影响面。

### 3.4 可观测性：jsonl 是档案流，事件是直播流，warning 有显式传递链

- `session.jsonl` 让同一事件流有了**第三个读者**（继渲染器、前端之后）：未来审计员（P6）、
  回放调试、用户手工排障都读同一份档案，不需要新埋点；
- warning 不静默：`load_session_messages` 返回 `warning` 字段 → `restore_and_attach` 的 `on_warning` 回调上屏；
  单元测试钉住文案（「备份」「怎样算对」），验收时管道缓冲没刷出也不影响契约存在；
- registry 对非法 slug 的拒绝路径全程可见：REST 422 带 `loc` 语义、WS 1008 带关闭码——每一层都「说话」而不是吞。

## 4. 评审记录（R3 独立子代理全量 diff）

重点清单①–⑤全部通过：① storage 文件锁与 asyncio 兼容（Windows 探路用例先行）② message_end 外无漏记路径
（repair 消息补回归钉）③ registry 并发首访原子（gather 断言同实例）④ slug 注入面全防护（构造即校验 + CON 钉）
⑤ 前端切换无订阅泄漏（先 close 再连，单连接断言）。

评审产出 2 个非阻塞发现，均已处置：

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| ⑥ | 中 | `_persist` 裸奔：磁盘满/锁超时抛 OSError 会杀死整轮写作流 | try/except + stderr 三要素告警，写作继续但历史不完整；泄压阀测试钉住（`19a53b4`） |
| — | 低 | GET 惰性装配建目录与「零写入口」注释出入 | 设计取舍（计划 D4 明确），记入留白 §8 |

## 5. 踩过的坑

1. **httpx 客户端归一化穿越路径**：想用 `/api/../escape/state` 打应用闸门验证 422，httpx 把它（含 `%2E%2E`）归一化后发出 → 404。防护测试改用 Windows 保留名 `CON` 钉应用层；注释注明「客户端归一化本身也是一层防护」——测的不是 httpx，是 FastAPI 路由匹配不到时我们的闸门还挡不挡。
2. **`git add -A` 误提交**：`.vscode/settings.json` 和 `submission/*.png` 混进 T4 提交 → `reset --soft` 后按文件名重提。结论照旧：原子提交按文件 add，不用 `-A`。
3. **WS 隔离测试 `KeyError: 'delta'`**：`assistantMessageEvent` 是多类型联合（text_delta 只是其一），过滤时按 `.get("type") == "text_delta"` 判别，不能假定字段总在。
4. **tsc TS2493**：`vi.fn(async () => ...)` 的空签名让 `mock.calls[0]?.[0]` 推导为空元组 → mock 函数显式标注参数类型。
5. **告警没出现在服务日志**：`print` 经服务进程管道缓冲未即时刷出——非缺陷，文案由单元测试钉住，验收记录注明（后续可考虑 stderr + flush，本阶段不动）。
6. **浏览器自动化工具不可用**：本会话环境无 browser_* 工具 → 按 P3 T10 同款许可路径降级为 REST/WS 客户端 transcript + 落盘文件 + 服务日志三重证据。

## 6. 验证输出存档（R3-⑥ 修复后复跑，全绿）

七道门全绿：pytest 258 通过（P3 基线 212 + 新增 46）、vitest 43 通过（基线 31 + 新增 12）、ruff / format / mypy（62 文件零豁免）/ tsc+build 全过。T7 红线（逐 .py SHA-256 对比上游 `C:\Projects\Agent\tau`）零违例：tau_ai / tau_agent 36 个 .py 逐字节一致；hastings 对 tau_coding 零 import（仅 2 处 docstring 提及设计出处）；REST 写入口仅 POST /api/workspaces（资源创建）。

## 7. DoD 对照（计划 §5）

| DoD | 证据 |
|---|---|
| Web：杀进程重启 + 刷新，对话流完整恢复 | [p4-acceptance.md §1](artifacts/p4-acceptance.md)：6 条消息回放，roles 序列一致 |
| CLI：重进同 slug 会话续上 | §2：零工具答出「波洛（比利时侦探）」 |
| read_chapter / list_chapters + guidelines | §3 + 集成剧本（读回第 1 章再写第 2 章） |
| 双工作区并存、隔离、可新建 | §4：b 为 0 消息；落盘目录物理隔离 |
| 坏文件检疫 + read 泄压阀 | §5：`session.jsonl.bad-1791511553719844700`；is_error + 目录摘要（测试钉） |
| REST 契约变更声明 | acceptance 末节逐条列出（无兼容层） |
| 七道门全绿；红线 | 本笔记 §6 存档 |
| 验收存档；构建笔记；R3 blocker 清零 | p4-acceptance.md + 本笔记 + R3 blocker 空 |

R5 验收（2026-10-09）：计划 §5 八条全部通过（证据见上表与 [p4-acceptance.md](artifacts/p4-acceptance.md)），
七道门复跑全绿（本笔记 §6），红线三项零违例。**P4 正式关闭，可进入 P5（RAG 语料库）**；
开工时先写 phase-05-rag-plan.md，核实 Chroma + BGE-M3 在 Windows 上的安装性。

## 8. 已知留白（转入后续阶段）

- **session.jsonl 无限增长**：无 compaction（单故事量级小，P5+ 评估）；
- **GET 惰性装配建目录**：`get_or_create` 会 `ensure_dirs()`，与「REST 业务数据零写入口」的字面表述有出入——
  业务数据（Bible/正文/会话）仍零写入口，建目录是资源装配副作用，计划 D4 的明确取舍（R3 评审提出，记为设计决策）；
- `model_change` entry 记录但不消费；`parent_id` 树分支留空（P6 审计员阶段评估）；
- 无鉴权/多用户（localhost 形态）；工作区无删除/归档。

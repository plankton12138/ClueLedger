# 1.1 接口与前端契约

> 2026-10-27 版本调整：原 1.0 修订学习计划顺延为 1.1 预留；优先完成 [1.0 悬疑线索设计与写作联动](../../product-design/v1.0/README.md)。本计划尚未实施，实施前按 1.0 实际交付重新核对基线。

1.1面板需与1.0的对话、本章线索共同使用辅助区，保留各自输入、位置与焦点；来源定位和精确线索引用沿用1.0。
本文其余接口为原方案预留，不能用于覆盖1.0的章界、引用或正文修改权限。

初稿：2026-10-26；更新：2026-10-27。状态：**全部待实施**。
领域和恢复以[数据契约](data-and-recovery.md)为准，本文负责 HTTP、界面动作和异步归属对齐。
STYLE.md 统一维护、生效与外部修改见[文档契约](style-document.md)。

## 1. 接口总表

基础路径沿用 `/api/file-workspaces/{workspace_id}`，产品 1.1 不改成 /api/v1。
写接口均验证当前登记和稳定作品 UID。列表采用有界 limit 与 cursor，未知字段拒绝。

| 方法与路径 | 请求要点 | 返回/前端动作 |
|---|---|---|
| GET /style-document | 无；固定作品根目录，不接任意路径 | 文件状态、完整文本、digest、解析问题、块引用和同步状态；缺失为空视图 |
| PUT /style-document | expected_digest（首次创建为 null）、text、client_sequence、request_key | 保存操作、文件/Git/索引独立结果、change_seq；整份约定保存 |
| GET /style-document/draft | editor_id | 当前编辑客户端的基线、草稿版本、序号；刷新恢复 |
| PUT /style-document/draft | editor_id、base_digest、base_text、expected_draft_revision、client_sequence、text、request_key | 持久化草稿版本/时间；初次核验基线，后续不偷换基线 |
| POST /style-document/draft/actions | editor_id、discard、expected_draft_revision、request_key | 明确放弃文档草稿；关闭面板不隐式丢弃 |
| GET /style-document/operations | request_key?、cursor、limit | 响应丢失后的文档保存或决策操作查询，不返回已忘记正文 |
| POST /style-document/operations/{operation_id}/reconcile | request_key | 显式本地核对并补索引/决策阶段，不覆盖文件、不自动创建 Git 提交、不调用模型 |
| POST /style-document/operations/{operation_id}/recording | request_key | 核对/补记 STYLE.md 历史，不重写文件或调用模型 |
| POST /file-edit-sessions | path、expected_digest、request_key | 201，会话、基线、完整原文、稳定 UID；进入编辑 |
| GET /file-edit-sessions | path?、status?、cursor、limit | 编辑/草稿摘要；恢复入口 |
| GET /file-edit-sessions/{session_id} | 无 | 基线、草稿、最新操作状态；刷新恢复 |
| PUT /file-edit-sessions/{session_id}/draft | expected_draft_revision、client_sequence、text、request_key | 新草稿版本与持久化时间 |
| POST /file-edit-sessions/{session_id}/actions | close/discard、expected_revision、request_key | 关闭已完成批次或明确放弃草稿 |
| POST /file-edits | session_id、base_id、expected_digest、text、client_sequence、request_key | 201/重放200，保存/版本/学习独立结果 |
| GET /file-edits | path?、request_key?、cursor、limit | 历史或响应丢失后的操作定位 |
| GET /file-edits/{edit_id} | 无 | 保存阶段、快照、来源、提交、恢复状态 |
| GET /file-edits/{edit_id}/diff | detail=paragraph/inline/raw | 有版本的结构化差异或原始 patch |
| POST /file-edits/{edit_id}/recording | request_key | 核对/补记；不重写正文、不调用模型 |
| GET /revision-learning | after_change_seq? | 设置、队列、用量完整度、最新 change_seq |
| PUT /revision-learning/settings | expected_revision、enabled、daily_request_limit、request_key | 设置视图，无立即模型调用 |
| POST /revision-learning/operations/{job_id}/actions | retry/cancel、expected_revision、request_key | 操作当前版本和状态 |
| GET /writing-preferences | status?、document_uid?、cursor、limit | 待确认/启用/停用列表 |
| GET /writing-preferences/{preference_id} | 无 | 精确版本、来源、条件、例外、冲突、清理状态 |
| POST /writing-preferences/{preference_id}/decisions | expected_revision、expected_style_digest、action、rule?、conditions?、exceptions?、scope?、request_key | 决策回执、新版本、文档保存/索引结果；会改文件时共用文档保存服务 |
| GET /writing-preferences/usage | turn_id/request_id、cursor、limit | 请求实际选中及排除明细；路由在动态 ID 前声明 |

现有 /file GET、Git 状态/历史/撤销、活动订阅不删除。人工补记操作和一般 git/record 的语义不同：
前者定位已知 edit_id，后者不能把所有未知文件自动认定为该 edit 的人工来源。
所有现有写入口改用同一 claim，不仅给新接口加锁。
约定文件不经过仅支持 chapter 的 /file-edit-sessions；文档专用路由仍复用保存与冲突基础能力。
通用文件、导入、Git 恢复等入口遇到 STYLE.md 时必须重新核对索引和撤销过滤，不能留下旧生效缓存。

## 2. 类型与示意响应

创建编辑会话不改变正文，也不要求前台任务停止；基线来自一次完整读取。
expected_digest 是作者当前看到的原始字节指纹，过期就返回冲突，不偷偷换成新基线。

```json
{
  "session_id": "es_001",
  "workspace_uid": "wu_001",
  "document_uid": "doc_001",
  "batch_id": "batch_001",
  "base": {"base_id": "base_001", "digest": "sha256:raw", "text": "完整原文件"},
  "draft": {"revision": 0, "client_sequence": 0, "text": "完整原文件"},
  "session_revision": 1
}
```

以下 hash 和 ID 仅示意，不规定实际前缀算法。

```json
{
  "edit_id": "edit_001",
  "request_key": "save_request_001",
  "session_id": "es_001",
  "session_revision": 2,
  "client_sequence": 12,
  "file": {"status": "saved", "digest": "sha256:after", "path": "正文/0007 暗潮.md"},
  "recording": {"status": "committed", "commit": "actual_sha", "reason": null},
  "learning": {"eligibility": "eligible", "job_id": "job_001", "status": "pending"},
  "next_base": {"base_id": "base_002", "digest": "sha256:after"},
  "recovery": "not_needed",
  "change_seq": 23
}
```

Git 失败返回 file.saved、recording.unrecorded、learning.awaiting_record，并附结构化问题。
文件已经改变的结果通过 200/201 操作视图返回，不用一般 500 抹去保存事实。
未写正文的验证失败使用统一错误信封。操作结果响应丢失时按相同 request_key 查回/重放。
同键同载荷重放返回原结果，不重新 claim 写入；同键不同载荷 409。
请求仍处理中返回 202 和操作引用；任何 retry 仍指向原操作，而不是再次写正文。

偏好决策 action：
`confirm / revise / dismiss / pause / resume / forget`。
confirm 带作者看到的最终 rule、conditions、exceptions 和 scope，保存确认快照；
revise 允许已启用规则改写，产生新版本并立即视作作者明确声明。
仅本次使用 dismiss，不生成相反偏好。
扩大作品外范围在 1.1 明确拒绝，不提供个人推广 UI。
决策幂等缓存只保留无正文回执；重放返回原决策身份和提交时状态，偏好详情按当前版本和忘记状态读取，
不能从历史确认响应重新返回已经忘记的规则正文。

scope 示例：

```json
{
  "workspace_uid": "wu_001",
  "kind": "character",
  "character_ref": {"binding_id": "binding_001", "entry_id": "character_001"},
  "scene_ref": null,
  "chapter_from": 7,
  "chapter_to": null
}
```

完整人物版本和来源在详情另存，不让客户端用 display_name 建身份。
条件与例外作为可编辑内容保存，场景锚点由服务器核对现有文档及版本。

文档读取与保存视图至少包含：

```json
{
  "path": "STYLE.md",
  "file": {"status": "ready", "digest": "sha256:style", "text": "# 写作约定\n\n## 叙述\n使用第三人称限知视角。\n"},
  "index": {"status": "synchronized", "digest": "sha256:style"},
  "issues": [],
  "change_seq": 24
}
```

missing 时 digest 为 null、text 为空；invalid/needs_review 保留可读原文和具体问题，不回传旧规则冒充当前有效内容。
保存另带 operation_id、request_key、client_sequence、文件 saved/no_change/not_saved、
recording 和 recovery；读取的 ready 与操作的 saved 分开，不混用状态。
file.saved + index.synchronized 可用于后续相关请求，Git 失败单列 unrecorded；
index_pending/recovery_needed 不报告已生效。202 返回操作引用，原请求键用于查询或重放。
草稿归属为 (workspace_uid, editor_id)，两窗口独立保留；同一客户端仍用序号和版本防迟到覆盖。
直接声明无需候选或证据 ID；候选确认同时核验候选 revision 和当前文件 digest。

## 3. 状态、错误与并发

错误沿用 `{"error":{"code","where","why","fix","details"}}`。
界面依 code 分支，不解析中文错误句子。

| 错误码 | 条件 | 界面行为 |
|---|---|---|
| STYLE_DOCUMENT_INVALID | 编码/结构、重复 ID、大小或元信息非法 | 保留原文，显示问题位置和修正入口，写作可跳过该文件 |
| STYLE_SCOPE_REVIEW_REQUIRED | 范围丢失或人物/场景身份无法核对 | 排除相关块，保留内容供作者明确范围 |
| STYLE_DOCUMENT_STALE_CONFLICT | 约定文件与预期 digest 不符 | 基础/草稿/磁盘对照，不覆盖外部更新 |
| STYLE_INDEX_PENDING | 文件已保存但索引未完成 | 显示已保存与尚未生效，提供操作状态及本地恢复入口 |
| WORKSPACE_BUSY_CONFLICT | 正文或管理操作已占用 | 保留草稿，等待或停止原任务后重试 |
| FILE_STALE_CONFLICT | 磁盘字节与基线不符 | 基础/草稿/最新正文对照，不能强覆盖 |
| EDIT_SESSION_CONFLICT | 会话、base 或文档身份不一致 | 保留输入，重新核对会话 |
| DRAFT_REVISION_CONFLICT | 草稿版本已变化 | 保留本地输入和服务端副本 |
| REQUEST_KEY_CONFLICT | 同键不同载荷 | 不重发新正文，定位原请求 |
| PREFERENCE_REVISION_CONFLICT | 作者看到的版本已变化 | 展示新旧理解，再次确认 |
| PREFERENCE_SCOPE_INVALID | 跨作品、人物不明或无效锚点 | 保留编辑内容，修正范围 |
| REVISION_STORE_INVALID | 记录库损坏/格式不支持 | 正文可读，新增编辑保存暂停 |
| REVISION_RECOVERY_REQUIRED | 保存阶段不能确定 | 显示真实状态与核对入口 |
| REVISION_ANALYSIS_FAILED | 在线分析失败 | 保存结果保留，允许显式重试 |
| LEARNING_BUDGET_EXCEEDED | 新请求将超预算 | 展示待处理/预算状态，不阻塞正文 |

可读文案遵循：

```text
[FILE_STALE_CONFLICT] 正文保存：文件已在其他位置更新。建议：对照最新内容后重新保存，当前草稿已保留。
[REVISION_ANALYSIS_FAILED] 修订学习：本次分析未完成。建议：稍后重试，正文和版本记录已保留。
[STYLE_DOCUMENT_STALE_CONFLICT] 写作约定：STYLE.md 已在其他位置更新。建议：对照最新文件后合并保存，当前草稿已保留。
```

不存在「忽略指纹直接覆盖」端点。确认人工合并后创建基于最新内容的新编辑基线，再保存明确合并的文本。
不会为了同步风格自动修改原请求范围外章节。

## 4. 前端状态归属

编辑状态以 (workspace_uid, document_uid, session_id) 为键，路由另保存当前 workspace_id。
请求令牌含 workspaceEpoch、document/session、client_sequence 和 request_key。
切作品、切章、切模块或切候选后，旧响应可以更新其原归属记录，不能接管当前界面。

正文状态至少包含：

```text
base_id / base_digest / base_text
draft_text / client_sequence / persisted_sequence / draft_revision
save_snapshot / save_request_key / saving
file_status / recording_status / learning_status / conflict / recovery
```

保存前冻结提交文本和 sequence。用户在请求期间继续输入：
成功后 base 改为提交文本，较新 draft_text 保留；只有未继续输入时才清理脏标记。
未知响应用原请求键查询，不因点击一次重试就创建新保存。
草稿自动保存顺序串行并合并待发送文本，收到旧 sequence 的响应不倒退 persisted_sequence。

## 5. 编辑、冲突和差异界面

- 阅读入口沿用 Paper，编辑组件不把 Markdown 展示文本当原文件。
- 草稿恢复显示基于哪个版本；删除草稿是独立明确动作，不因刷新自动放弃。
- 当前任务运行中可编辑，保存时显示忙碌原因；停止任务由作者主动选择。
- 冲突对照保留三份文本；先支持手工合并和复制，不承诺自动三方合并。
- 差异默认段落内红绿与增删标识，可查看完整段落和原始 patch。
- 大差异返回 coarse 时明确显示整段范围，不做看似精确的字符标记。
- 元信息、中文、emoji、空段和移动/拆并按服务端坐标与片段渲染。
- 保存后分别呈现正文、版本和学习结果；Git 补记不再次运行写作模型。

## 6. 偏好面板与异步更新

默认按需打开；宽屏与现有对话共用辅助区域，用本地辅助栏标签切换，不增加第四列。
临时收起不改长期字号/栏宽偏好；关闭恢复先前对话可见性。
窄屏沿用单栏和可关闭覆盖面板，Escape/外部点击/返回及焦点恢复一致。
未保存规则编辑保留于候选 ID 和 revision 下，不被后台刷新覆盖。

作者可以查看来源、改规则/条件/例外、改作品内范围、确认、仅本次、停用、恢复和忘记。
面板内提供「写作约定」整份编辑与「待确认」入口，约定编辑和详情操作均读取同一文件 digest。
应用补稳定 ID 只修改必要元信息，不在只读打开时格式化整份文档；未知范围不按标题猜测。
文档草稿保留基础文本、digest、客户端序号和未保存状态，外部变化不覆盖草稿；冲突沿用三份对照。
外部修改通知显示可更新状态，实际受理与每次请求仍由服务端核对磁盘。
候选后台增加证据或规则版本变化，不自动勾选、确认或清空正在修改的内容。
如果已经忘记，旧请求或旧面板必须显示失效，不能重新激活。

变更通知复用作品活动流，新增类型可聚合；约定文本权威在 STYLE.md，候选与生命周期权威在记录库。
每次 payload 携带 workspace_uid、entity_id、revision、change_seq、operation_id。
断线后从 GET /revision-learning 和对应列表重建，不能依赖短期活动附件。
隐藏或未打开面板不持续轮询；打开且存在 pending/running 时用有界刷新，终态停止，
活动事件可触发重新读取但不直接拼接权威状态。

## 7. 作品生命周期

改名前暂停该作品分析发布、关闭存储连接，再移整个状态目录；
成功后更新当前路由映射，稳定 UID 和草稿关联不变。
失败恢复原登记与服务状态，不部分启用新身份。
删除先取消/等待本地发布并封住 generation，再删目录与状态。
迟到响应不得重建已删状态目录。未登记旧路径草稿提供核对，不按相似作品名自动恢复。

## 8. 任务实际使用详情

偏好使用按 request_id 展示，轮次摘要只聚合已发送请求。
同时显示被选择与被排除的原因，例如范围不符、目标未知、章界不符、证据失效、预算不足和已撤销。
同时显示 style_digest 与规则块版本；文件缺失/非法、文件变化、索引待恢复也是实际排除原因。
无 Usage 显示未上报，不能显示零；分析按作品操作统计，不累加到当前随意打开的会话。
输入捕获关闭仍有数值与来源回执；不为该功能强制开启完整输入捕获。

## 9. 对齐检查

G0 建 OpenAPI/前端 DTO 契约检查；领域枚举只保留一个权威定义，变更同步消费端。
E1–W1 每个接口有对应页面动作、恢复路径和错误分支。
X1 用真实接口验证：保存、补记、候选、决策、忘记、重启、改名/删除、任务详情。
S1–X1 增加文档读写、独立草稿、外部变化、候选写回同一文件、文档补记、操作查询与本地恢复，均有对应页面动作与错误分支。
不把演示动画和静态候选计为接线成功。

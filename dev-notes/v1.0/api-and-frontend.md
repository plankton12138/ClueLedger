# 1.0 接口与前端契约

日期：2026-10-27。状态：**待实施**。本页约定目标接口，不代表当前已存在。
数据状态见[数据与恢复](data-and-recovery.md)，阶段和文件见[开发计划](development-plan.md)。

## 1. 统一入口和兼容

基础路径继续为 `/api/file-workspaces/{workspace_id}`，不因产品 1.0 改 HTTP 主版本。
新增路由放 `clue_api.py`；复用现有资料任务、会话事件订阅、停止和真实请求用量。
所有数组有界、分页游标绑定查询和账本 revision；换时点或源改变后旧游标返回冲突。

| 前端动作 | 接口／字段 | 返回与完成条件 |
|---|---|---|
| 本章或全书列表 | 新 `GET /clues?chapter_path&as_of_chapter&q&limit&cursor` | 统一列表、覆盖、有效性和 next_cursor；limit 默认20、最多50 |
| 打开详情 | 新 `GET /clues/{clue_id}?as_of_chapter` | 详情、计划、推理、可见检查与 evidence_id |
| 老资料页／旧链接 | 现有 `GET /story-materials/clues` 及条目详情 | 从同一读模型投影；旧条目 ID 经 alias 解析，返回 canonical clue_id |
| 定位依据 | 新 `GET /source-locations/{evidence_id}` | file 或 module 的判别响应、原文版本和有效区间 |
| 整理本章／多章 | 现有 `POST /material-tasks`，domain=clues | 202 受理，沿用任务查询、SSE 和停止；不是立即整理成功 |
| 关联／更换作者计划 | 新 `PUT /clues/{clue_id}/design` | request_key、expected_revision、精确来源；返回新详情 |
| 移除计划关联 | 新 `POST /clues/{clue_id}/design/unlink` | 同样带请求键与版本；不删除模块原文 |
| 保存一个推理目标 | 新 `POST /clue-inferences` | 创建或按 inference_id 更新，关联1—10条线索、目标与意图 |
| 核对推理 | 现有创建／续聊入口，purpose=review，加 clue_review | 受理轮次和冻结范围；只读工具与报告发布 |
| 查看报告 | 新 `GET /clue-reviews/{report_id}?as_of_chapter` | 完整报告、来源状态、覆盖与可定位依据 |
| 讨论设计／修改 | 现有 UI 草稿 PATCH，加 draft_clue_references | 仅准备引用和消息草稿，不发模型请求 |
| 发送引用消息 | 现有首次／续聊 context.clue_references | 服务端解析、版本核验、幂等受理，引用不扩大写权限 |

读取无记录返回真实空视图和覆盖，不能把损坏文件返回为空；GET 不初始化新账本或调用模型。
正文无内容时整理和正文核对不可启动；作者仍可在对话构思、保存计划并关联无正文的线索设计。

## 2. 共用详情

字段在后端响应模型与 `frontend/src/api/clues.ts` 同名定义；下列为边界样例，数组内容为示例。

```json
{
  "clue_id": "clue_opaque",
  "clue_revision": "rev_opaque",
  "title": "遗书背面的字",
  "as_of_chapter": 2,
  "lifecycle": {"status": "open", "planted_chapter": 2, "due_chapter": 8},
  "coverage": {"outcome": "found", "freshness": "valid", "chapter_paths": ["正文/0002 遗书.md"]},
  "observations": [{"observation_id": "obs_opaque", "chapter": 2, "mode": "testimony", "text": "林岚提到背面的句子", "evidence_ids": ["ev_opaque"]}],
  "reader_seen": [{"text": "林岚提到背面的内容，来源尚未核实", "basis_ids": ["obs_opaque"]}],
  "author_designs": [],
  "inferences": [],
  "reviews": [],
  "warnings": []
}
```

`reader_seen` 是有依据的正文呈现摘要，不能直接拼作者计划或把证词变成真相。
空 author_designs 表示尚未关联，不表示作者没有真相设想。旧 supports 以 legacy_unverified 显示，
不自动转为已验证推理目标。计划、目标和报告有独立 freshness，不能用一个“有效”标记覆盖全部状态。
`clue_revision` 为与当前条目相关的内容版本，`ledger_revision` 用于写事务；无关条目变化不使选中引用自动过期。

## 3. 来源位置

SourceLocation 是 `kind=file|module` 的判别联合：

- 共用：evidence_id、freshness、保存的 quote、offset_unit、start、end。
- file：受管 path、digest、chapter、当前文件是否可读。
- module：library_id/module_id/revision_id/entry_id、content_digest、当前绑定是否仍引用该修订。

服务端从账本和当前装配解析位置。有效来源才返回可应用高亮区间；stale/source_missing/detached 时，
返回保存引文及可用的当前打开动作，不要求前端沿用旧位置。
前端不从 query 参数中的任意 path 或 quote 读取本机文件。

写作 hash 增加安全参数 `clue`、`source`、`as_of`，只接收已有标识和合法章号；原 writing 路由继续可用。
浏览资料的 chapter、当前正文文件和请求 state_chapter 分别保存，浏览器返回恢复原来时点与焦点。

## 4. 作者计划与推理关系

设计关联请求：request_key、expected_revision、source（file 或完整模块引用）、start/end/quote、关系说明。
服务端读取真实版本并校验区间；模块必须属于本作品的当前装配，不接受名字或库 head 代替精确引用。
source=file 时仅允许既有受管设定或作者显式指定的创作资料，不把普通章节中的人物发言当作者计划。

推理目标请求：request_key、expected_revision、可选 inference_id、clue_ids、goal、intent、design_link_ids。
goal 为作者明确要求或明确保存的构思；intent 为 deduce/suspect/keep_open/undecided。
正文支持、待补前提和其他解释由检查形成带来源结果，不能让客户端用一个布尔值认证推理成立。
关联线索范围最多10条；较大推断先拆成有明确目标的步骤。

UI 的「关联计划／更换关联／移除关联」与同义 Agent 操作共用服务。Agent 增加
`link_clue_design`、`save_clue_inference` 工具，并在 set_task_scope 增加精确 clue_design_grants。
grant 只允许改变关系和目标，不授予模块或正文写入；改作者正文仍需原 module_content_grants。
纯讨论不调用关系写工具；作者明确要求创建或保存构思后才登记相应操作。

## 5. 核对请求与结果

在线核对沿用两种真实入口：首次创建会话与已有会话追加轮次；两者扩展相同字段。

```json
{
  "text": "核对这组线索是否足以让侦探认定林岚提前接触过原件。",
  "request_key": "request_opaque",
  "purpose": "review",
  "review_paths": ["正文/0002 遗书.md"],
  "context": {
    "selected_path": "正文/0002 遗书.md",
    "reference_paths": [],
    "observed_digests": {},
    "state_chapter": 2,
    "clue_references": [{"workspace_lineage": "ws_opaque", "clue_id": "clue_opaque", "clue_revision": "rev_opaque", "browse_as_of_chapter": 2}]
  },
  "clue_review": {"inference_id": "inference_opaque", "inference_revision": "rev_goal", "clue_ids": ["clue_opaque"], "as_of_chapter": 2}
}
```

review_paths 为明确核对目标，初版上限10个非空正文章节，均不得超出 as_of；必要参考也需真实读取并记录。
as_of 必须与本次核对的 state_chapter 一致；普通聊天不得夹带 clue_review，通用 review 缺省无该字段仍兼容。
服务端固定目标意图、来源版本和目录清单；模型不能扩大章界。未建立目标时提供构思入口，不创建空核对任务。

扩展 `save_review_report` 的 clue_analysis：inference_id、goal_snapshot、premises、alternatives、findings、uncovered、actual_coverage。
核对模式额外只开放该范围的报告发布，不开放线索、模块、关系或正文写入工具。
运行状态沿用 accepted/running/completed/partial/failed/stopped/interrupted，报告状态另为 findings/no_findings/incomplete。
结果 UI 同时说明范围和来源状态，实际覆盖由服务核验，不信任模型一句“已检查全文”。

沿用现有 `review_finished` 事件，增加可选 clue_report_id 和 inference_id；旧报告无字段时照旧显示。
线索报告先持久化再发布完成事件，事件投递中断可由账本查询恢复；不得仅凭事件说成功。
新 GET 按报告ID读取，列表与详情只投影当前章界可见部分；越界内容不先送到浏览器再折叠。

## 6. 对话草稿和请求身份

ContextInput 新增 `clue_references`，最多10条；包括 lineage、clue_id、clue_revision、browse_as_of_chapter，
可选 inference_id/inference_revision/report_id。客户端不随引用提交可信正文、真相或核对结论。
服务端从当前作品与指定版本形成快照，按 actual writer target 选择观察；计划另区渲染并标明信息归属。

ConversationUiPatch 增加 `draft_clue_references`，DraftContextView 返回相同结构；
现有 draft_target、revision 与 set_draft_context 规则继续使用，加入引用前核对当前草稿基线并合并，不整份覆盖其他会话。
重复点击对同一版本去重，有新版本时显示可选择替换，不能偷偷升级或清空已有输入。

首次／续聊的幂等负载、ContextRequest/ContextSnapshot、受理恢复记录、实际请求投影和 0.9 压缩都登记新字段。
压缩保留服务端结构化引用和未解决事项；历史摘要对线索的解释不是原文依据。
过期引用发送被拒绝时仍保留草稿与旧引文，作者可重新读取更新后用新请求键发送。
已经受理的同键重试返回原 turn_id，即使其后来源变化，也不能重新付费启动。

## 7. 前端状态与布局

- `state/clues.ts` 按 workspace_id、截止章、chapter_path、clue_id 和请求序号保存；切作品后的迟到响应丢弃。
- `state/sourceLocation.ts` 保存当前证据、阅读返回位置与焦点；不修改任务截止章。
- WritingLayout 共用辅助区；面板切换不重建或卸载对话草稿，宽度计算沿用字号与窄屏策略。
- ClueDetail 被本章入口与资料页共同使用；仅选中时突出原文，旧证据标清来源变化。
- ChapterClues 按实际提及列记录；已回收、已废弃可折叠，不让三个空分组占满默认视图。
- 检查结果默认展示具体问题和范围，原文依据可定位，构思与修改引用在草稿中可删除或返回。
- 尚未整理、已整理无发现、材料不足、失效、运行中、部分完成分别呈现；来源读取失败不显示“没有线索”。
- 窄屏按需切换正文与详情，Escape 返回与焦点恢复；保留100%/150%字号、键盘和触屏能力。

## 8. 错误与前后端对齐

沿用 WorkspaceError 的 code/message/details 信封。下列是拟议新增错误，不改自然语言解析状态码。

| 错误码 | HTTP | 处理 |
|---|---|---|
| CLUE_NOT_FOUND / CLUE_REVIEW_NOT_FOUND / SOURCE_LOCATION_NOT_FOUND | 404 | 说明记录或来源不存在，提供返回列表 |
| CLUE_ACCESS_DENIED | 403 | 在现有 forbidden 码表显式登记；拒绝跨作品或未装模块引用 |
| CLUE_REVISION_CONFLICT / CLUE_REFERENCE_STALE_CONFLICT | 409 | 保留草稿，重新读取实际版本 |
| CLUE_IDENTITY_CONFLICT | 409 | 不按同名合并，保留待处理目标 |
| CLUE_SOURCE_STALE_CONFLICT / CLUE_CURSOR_STALE_CONFLICT | 409 | 重读来源或从第一页重新查询 |
| CLUE_SCHEMA_UNSUPPORTED_CONFLICT | 409 | 保留高版本记录，使用匹配程序或显式恢复 |
| CLUE_SCOPE_INVALID / CLUE_REVIEW_TARGET_REQUIRED | 400 | 给出范围或目标的具体缺项 |
| CLUE_LEDGER_READ_FAILED / CLUE_LEDGER_WRITE_FAILED / CLUE_MIGRATION_FAILED | 500 | 保留原记录与正文，恢复后重试同一操作 |

错误文案示例：

```text
[CLUE_REFERENCE_STALE_CONFLICT] 线索引用：所选原文已变化。建议：重新读取线索，确认后继续发送；消息草稿已保留。
```

每个新增路由、前端按钮、草稿字段、模型工具和事件在 S0 建对齐清单，S7 逐项验证。
不把“后端可调用”“前端显示示例”和“端到端实际完成”混为同一状态。

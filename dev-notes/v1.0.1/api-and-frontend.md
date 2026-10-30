# 接口、事件与页面契约

日期：2026-10-29。状态：**当前实现契约**；当前进度与验收状态见[开发入口](README.md)。

2026-10-30 创建入口补充：首次与续聊请求均可携带 `story_action: create`，必须同时提供 `context.story` 的方案、修订和 `purpose=design`。该动作明确要求结构成果，资料名称不开放模块内容写入；缺少构思目标时返回 `STORY_SCOPE_INVALID`。空白构思通过创建表单提交，请求受理后在原对话跟随进度；失败保留要求和普通草稿，完成依据结构收据。详见[创建流程](notes/2026-10-30-story-empty-entry.md)。

2026-10-30 实施补充：构思页普通对话按受理时确定的任务目标区分讨论、结构修改、资料模块与待澄清。`task_intent` 事件记录目标、作者原话、方案与修订，以及作者确认时绑定的上一轮候选；候选只扩展可修改对象，不授予真相或约定修改。结构任务与讨论轮不开放模块内容写入，资料任务与讨论轮不开放结构写入。`story_changes_committed` 增加基础修订、改动对象、作者可读标签、操作类型与代次；新增 `story_change_noop` 记录经核验的无需改动。任务卡按这些事件显示本轮目标、已保存结构与未完成说明；`GET /api/operations/{id}/result` 同时返回 `intent` 与结构／模块收据。

2026-10-29 实施补充：构思变更接口已接受 `prose_patches` 与 `read_dependencies`，与前端请求类型对齐；正文／结构均为空时拒绝。读取依赖覆盖方案、正文、约定、模块与会话；依赖入口缺失时明确拒绝。收据的 `result.prose_paths` 和 `changed` 中的 `prose:路径` 表示正文目标。对象级授权与候选携带结构变更已接通，详见[候选流程记录](notes/P4-candidate-workflow.md)；备选正文受管副本与方案正文读取见[联合提交记录](notes/P1-joint-commit.md)。

方案正文读取：`GET W/story/variants/{variant_id}/prose?path=...` 返回文件视图加 `variant_id` 与 `inherited`；副本被标记删除时返回 404 `STORY_PROSE_NOT_FOUND`。前端方案节点定位走该接口，正文预览不写阅读位置。

候选流程补充：带构思语境的 `write/revise` 已接入候选提交、隔离核对、一次局部修正与复核；构思页可通过 `GET /story/candidates` 和 `GET /story/candidates/{candidate_id}` 查看保留的全文与报告。新正文补丁用 `base_digest=absent` 表示创建，`delete` 用于受版本保护的删除／撤销。当前边界见[候选流程记录](notes/P4-candidate-workflow.md)。

## 1. 统一规则

作品 API 前缀 `W=/api/file-workspaces/{workspace_id}`。结构请求都绑定 variant_id，写入携带 expected_revision 和 request_key，后端验证作品归属。同键不同负载返回 409；无对象 404，字段错误 422，来源／修订冲突 409。错误统一为 `[错误码] 位置：现象。建议：…`。

请求只传目标身份与作者要求，实际结构和正文由服务端读取。禁止客户端传一份“已核对真相”替代持久数据。变更权限以原任务及应用确认的作用域为准。

## 2. 页面与接口对应

| 页面动作 | 接口 | 结果及调用方 |
|---|---|---|
| 打开构思／方案切换 | `GET W/story/variants`；`GET W/story/variants/{id}?group_id=...` | 版本化结构、片段摘要与当前状态；story store |
| 点节点或线索 | `GET W/story/variants/{id}/nodes/{node_id}` | 安排、相关线索、真实作用、来源与正文入口；NodeDetail |
| 看正文关联 | `GET W/story/bindings/{binding_id}` | 校验后的位置、修订与失效状态；现有 Paper／SourceHighlight |
| 看方案正文 | `GET W/story/variants/{id}/prose?path=...` | 该方案实际可见的正文、摘要与是否沿用主方案；Paper 以方案模式呈现 |
| 确定性结构操作 | `POST W/story/variants/{id}/changes` | 完整 ChangeSet 与新修订；只接受当前 UI 明确支持的动作，不接受伪造授权 |
| 从某版建备选 | `POST W/story/variants` | 名称、source_variant_id、source_revision、request_key；只复制确定基线，无模型 |
| 看差异 | `GET W/story/comparison?base=...&candidate=...` | 双方明确修订、对象差异与已记录依赖，不宣称全书语义完整检查 |
| 采用方案／片段 | `POST W/story/adoptions` | source/target 修订与选中 ID；无模型的完整既定替换返回收据，需语义整理则受理可见 operation；不得隐含调用 |
| 对话构思、写作、核对、试读 | 扩展现有 `POST W/conversations` 和 `POST W/conversations/{id}/turns` | 返回 conversation_id、turn_id、operation_id；ConversationPanel 共用 |
| 暂停／继续／插话 | `POST /api/operations/{id}/pause|continue|interject` | 原子控制、代次、状态与收据；共享 OperationControls |
| 回看与重连 | `GET /api/operations/{id}`；`GET /api/operations/{id}/events?after=...` | 操作快照和游标事件，无执行副作用 |
| 查看本次依据 | `GET /api/operations/{id}/context` | 各阶段实际输入与来源，按需读取 |
| 查看结果／草稿 | `GET /api/operations/{id}/result` | 已提交收据、候选、未完成项与变更位置 |
| 查看改动／撤销 | `GET W/story/changes/{change_id}`；`POST W/story/changes/{change_id}/undo` | 精确变更与反向提交；冲突时返回受影响目标 |

采用流程用显式 `mode: exact|assist` 区分确定性应用与需要模型的调整。exact 无法安全完成时返回冲突和可选 assist 动作；选择 assist 时结构仍按同一计划确定性提交，随后受理一次可见 operation 调整正文，响应带 `operation_id`。两种路径均由 UI 呈现实际结果，不让“点比较”偷偷生成。

可提议的语义操作包括 add/update/remove node、add/update/remove clue、connect/disconnect、update constraint、bind/unbind prose。模型通过专用工具提出 ChangeSet，应用补齐范围与元数据；不暴露无限制覆盖整个状态文件的工具。

## 3. 任务请求

在现有 context 中增加 story 字段，首次与续聊、草稿、提问回答使用同一结构：

```json
{
  "text": "保留结局，把这处误导改得更自然，正文也一起调整。",
  "request_key": "request-unique-id",
  "context": {
    "story": {
      "variant_id": "main",
      "revision": "r12",
      "node_ids": ["lin-reaction"],
      "purpose": "revise",
      "reader_cutoff": null
    }
  }
}
```

purpose 为 discuss、design、write、revise、review 或 reader_preview。客户端 purpose 只表达意图，不能单独授予写权限；正文范围与授权仍由现有受理流程和本轮作者指令确定。构思页的 design 仍表示工作语境：讨论、比较与明确暂不保存保持只读；明确建立或修改结构时才形成结构目标，作者确认候选时只采用最近一条可定位的候选。试读必须有明确截止与呈现来源。

试读使用独立入口 `POST W/reader-previews`：请求只有 `request_key / variant_id / revision / cutoff`，不接收文字、选中材料、检查目标或回答。它建立 `origin=reader` 的独立会话，问题由应用固定；执行时没有工具、目录、摘要与作者历史。写作对话列表不列读者会话。

continue 提交 request_key、expected_generation；interject 再带 text。后端一次完成旧代次撤销和新指令受理，返回新的 generation。不能先 stop 再 send，避免中途丢要求或重复继续。

超容量任务的后续组使用 `POST W/story-batches/{operation_id}/continue`，无模型提供的范围参数。后端从父操作读取剩余组、原要求与当前有效方案，返回同一个父任务对应的唯一后继；重复点击不重跑。`Operation.pending_units` 和说明提供未完成范围。

候选使用 `GET W/story/candidates` 与 `GET W/story/candidates/{id}` 回看；图响应的 `latest_change_id` 指向当前方案最近提交，页面据此读取变更并恢复撤销入口。`context.story.structure_readonly` 只能收紧结构权限，assist 由应用固定为真；正文关联仍可随候选提交。

## 4. 操作与现有轮次的接入

统一 Operation 是已有轮次和非会话模型任务的共同生命周期。作品操作复用现有会话事件存储，operation_id 与 turn_id 建立服务端映射；新操作接口读取同一事件源，不维护第二份独立执行记录。压缩没有轮次，事件在写入时带 `compaction_operation_id`，按它归属；连接测试不写会话事件，实际增量存于操作事件中，结果以操作收据（`kind=probe`）返回。模型测试没有作品时使用独立 owner。

现有会话 `/events` 保留对话回放用途；工作区任务控件订阅 operation 流，不能同时对同一任务重复归并两个流。原 `/turns/{id}/stop` 的内部行为统一到 pause；前端迁移完成后删除已无调用方的旧控制路由。压缩原专用停止接口同样处理，不保留两套状态机：现在只是同一暂停语义的薄别名。

原会话流以 run_finished 结束，新的 operation 流不能把一次模型阶段或旧代次的 run_finished 当成整个操作完成。以当前操作快照为准，paused 结束本次跟随但可从游标恢复。

## 5. 事件契约

事件包包含 `seq, operation_id, generation, phase, type, payload`。seq 在该底层事件源中单调递增，允许其他事件造成间隔。已处理 seq 不重复使用；服务端先过滤失效代次，前端再防御性校验。

| 事件 | 页面含义 |
|---|---|
| turn_started / attempt_started | 固定任务归属，开启新代次；保留前代已显示内容 |
| run_phase / model_request_started | 正在构思、写作、核对、压缩等明确目的 |
| story_context | 本次实际装入的结构分区、覆盖节点与来源、省略原因与拆组结果；试读为 `reader=true` |
| assistant_block_delta / text_delta | 当前 block_id 的真实输出增量；不把转圈或计数当流式 |
| tool_started / tool_finished | 读取、检查或提交的实际进展；不暴露凭据与内部思考 |
| story_candidate_saved | 完整候选可预览，尚未表示提交 |
| task_intent | 本轮目标（讨论／结构／资料／混合／待澄清）、作者原话、方案与修订、可选候选来源；只用于说明与判定，不授予权限 |
| story_changes_committed / story_candidate_committed / version_finished | 正式变更与收据已完成，刷新对应结构／正文；结构收据带改动对象、标签与代次 |
| story_change_noop | 经核验的无需改动：对象在本轮实际读过、当前存在且在授权范围内，附理由 |
| author_input_requested | 原对话中出现具体问题，保留自由回答 |
| stop_requested / error | 保留草稿与已提交单元，显示继续或处理入口 |
| run_finished + 操作快照 | 记录完成、未完成范围与结果，不依据助手文字猜成功 |

断线重连携带 after。无法覆盖的旧游标返回需要读取快照的信号，客户端先恢复快照再接流，不丢事件也不从头重跑任务。

## 6. 全部模型入口

| 入口 | 接入方式 |
|---|---|
| 构思、写作、对话修改、模块生成、灵感的模型操作 | 原会话受理返回 operation_id，同一任务控件 |
| 写后核对、语义关联、模型摘要 | 作为已声明创作任务的可见阶段；不得夹带未授权的新任务 |
| 手动／自动会话压缩 | 独立可见操作或当前操作的阶段，流式显示摘要候选，可暂停与插话；中断不启用半成品摘要 |
| 试读 | 已实施：独立会话与固定问题，空工具与空历史，系统提示只含读者说明和截止前缀，发送前自检隐藏内容；构思页试读面板读取独立会话结果 |
| 模型连接的生成／工具能力测试 | 已实施：probe 返回 202 与 operation_id；设置页跟随操作快照并在结束后读取 probe 收据，可暂停；不伪造通过结果 |
| 模型列表发现、打开页面、普通预览、移动选择、读文件 | 非模型请求，不伪装为生成，也不隐含启动模型 |

全部调用必须先登记操作再请求模型。供应商无法提供真实流式时显示能力限制并让作者更换适合的连接，不能用完成后的打字动画冒充实时输出。

## 7. 前端状态与页面

结构缓存键包含作品、方案、修订和片段；选择位置、草稿和活动任务分别保存。切换作品后旧请求返回不得污染当前页。活动任务目标固定，图中浏览只改变阅读位置。

构思主区显示节点与连接，详情就近展开；正文沿用现有渲染与源定位。未知位置或过期关联保留原文入口并说明需要核对，不静默高亮相似句。

对话顶部显示实际任务对象，生成时输入可用。窄屏在构思或正文中保留暂停／继续入口。语义问题与失败在同一对话处理，不新增复杂管理页。任务卡按 `task_intent`、结构收据与 `story_change_noop` 显示本轮目标、已保存的具体对象或「无需改动」；结构与模块各自说明结果，未完成时同时显示原因，不把模块保存当成结构完成。

前端不显示哈希、代次和权限配置等内部字段。查看依据、详细核对和历史按需展开。页面更新只能由实际结果和版本驱动，不能解析助手最后一句“完成”来刷新状态。

## 8. 对齐检查

P0 建立共享 JSON 样例与接口覆盖表；后端响应校验、前端类型及反序列化使用同一批样例。每次新增操作同步登记请求、结果、失败和 UI 归属。删除资料接口时同时删除客户端、工具注册和对应旧测试；核心能力改写为新流程测试，不能仅删测试消除失败。

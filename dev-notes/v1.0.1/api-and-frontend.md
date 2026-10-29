# 接口、事件与页面契约

日期：2026-10-28。状态：**拟实施接口，当前尚不存在新增能力**。

## 1. 统一规则

作品 API 前缀 `W=/api/file-workspaces/{workspace_id}`。结构请求都绑定 variant_id，写入携带 expected_revision 和 request_key，后端验证作品归属。同键不同负载返回 409；无对象 404，字段错误 422，来源／修订冲突 409。错误统一为 `[错误码] 位置：现象。建议：…`。

请求只传目标身份与作者要求，实际结构和正文由服务端读取。禁止客户端传一份“已核对真相”替代持久数据。变更权限以原任务及应用确认的作用域为准。

## 2. 页面与接口对应

| 页面动作 | 接口 | 结果及调用方 |
|---|---|---|
| 打开构思／方案切换 | `GET W/story/variants`；`GET W/story/variants/{id}?group_id=...` | 版本化结构、片段摘要与当前状态；story store |
| 点节点或线索 | `GET W/story/variants/{id}/nodes/{node_id}` | 安排、相关线索、真实作用、来源与正文入口；NodeDetail |
| 看正文关联 | `GET W/story/bindings/{binding_id}` | 校验后的位置、修订与失效状态；现有 Paper／SourceHighlight |
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

采用流程用显式 `mode: exact|assist` 区分确定性应用与需要模型的调整。exact 无法安全完成时返回冲突和可选 assist 动作；选择 assist 或明确要求同步调整才启动模型。两种路径均由 UI 呈现实际结果，不让“点比较”偷偷生成。

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

purpose 为 discuss、design、write、revise、review 或 reader_preview。客户端 purpose 只表达意图，不能单独授予写权限；正文范围与授权仍由现有受理流程和本轮作者指令确定。试读必须有明确截止与呈现来源。

continue 提交 request_key、expected_generation；interject 再带 text。后端一次完成旧代次撤销和新指令受理，返回新的 generation。不能先 stop 再 send，避免中途丢要求或重复继续。

## 4. 操作与现有轮次的接入

统一 Operation 是已有轮次和非会话模型任务的共同生命周期。作品操作复用现有会话事件存储，operation_id 与 turn_id 建立服务端映射；新操作接口读取同一事件源，不维护第二份独立执行记录。模型测试没有作品时使用独立 owner，仍使用相同事件契约。

现有会话 `/events` 保留对话回放用途；工作区任务控件订阅 operation 流，不能同时对同一任务重复归并两个流。原 `/turns/{id}/stop` 的内部行为统一到 pause；前端迁移完成后删除已无调用方的旧控制路由。压缩原专用停止接口同样处理，不保留两套状态机。

原会话流以 run_finished 结束，新的 operation 流不能把一次模型阶段或旧代次的 run_finished 当成整个操作完成。以当前操作状态和 operation_finished 为准，paused 结束本次跟随但可从游标恢复。

## 5. 事件契约

事件包包含 `seq, operation_id, generation, phase, type, payload`。seq 在该底层事件源中单调递增，允许其他事件造成间隔。已处理 seq 不重复使用；服务端先过滤失效代次，前端再防御性校验。

| 事件 | 页面含义 |
|---|---|
| operation_accepted / attempt_started | 固定任务归属，开启新代次；保留前代已显示内容 |
| phase_started | 正在构思、写作、核对、压缩等明确目的 |
| text_delta | 当前 block_id 的真实输出增量；不把转圈或计数当流式 |
| tool_progress | 读取、检查或提交的实际进展；不暴露凭据与内部思考 |
| candidate_ready | 完整候选可预览，尚未表示提交 |
| changes_committed | 正式变更与收据已完成，刷新对应结构／正文 |
| question_asked | 原对话中出现具体问题，保留自由回答 |
| operation_paused / operation_failed | 保留草稿与已提交单元，显示继续或处理入口 |
| operation_finished | 记录完成、未完成范围与结果，不依据助手文字猜成功 |

断线重连携带 after。无法覆盖的旧游标返回需要读取快照的信号，客户端先恢复快照再接流，不丢事件也不从头重跑任务。

## 6. 全部模型入口

| 入口 | 接入方式 |
|---|---|
| 构思、写作、对话修改、模块生成、灵感的模型操作 | 原会话受理返回 operation_id，同一任务控件 |
| 写后核对、语义关联、模型摘要 | 作为已声明创作任务的可见阶段；不得夹带未授权的新任务 |
| 手动／自动会话压缩 | 独立可见操作或当前操作的阶段，流式显示摘要候选，可暂停与插话；中断不启用半成品摘要 |
| 试读 | 独立输入范围，同一流式与控制方式 |
| 模型连接的生成／工具能力测试 | probe 返回 202 与 operation_id；设置内嵌同一控件，支持调整测试目标 |
| 模型列表发现、打开页面、普通预览、移动选择、读文件 | 非模型请求，不伪装为生成，也不隐含启动模型 |

全部调用必须先登记操作再请求模型。供应商无法提供真实流式时显示能力限制并让作者更换适合的连接，不能用完成后的打字动画冒充实时输出。

## 7. 前端状态与页面

结构缓存键包含作品、方案、修订和片段；选择位置、草稿和活动任务分别保存。切换作品后旧请求返回不得污染当前页。活动任务目标固定，图中浏览只改变阅读位置。

构思主区显示节点与连接，详情就近展开；正文沿用现有渲染与源定位。未知位置或过期关联保留原文入口并说明需要核对，不静默高亮相似句。

对话顶部显示实际任务对象，生成时输入可用。窄屏在构思或正文中保留暂停／继续入口。语义问题与失败在同一对话处理，不新增复杂管理页。

前端不显示哈希、代次和权限配置等内部字段。查看依据、详细核对和历史按需展开。页面更新只能由实际结果和版本驱动，不能解析助手最后一句“完成”来刷新状态。

## 8. 对齐检查

P0 建立共享 JSON 样例与接口覆盖表；后端响应校验、前端类型及反序列化使用同一批样例。每次新增操作同步登记请求、结果、失败和 UI 归属。删除资料接口时同时删除客户端、工具注册和对应旧测试；核心能力改写为新流程测试，不能仅删测试消除失败。

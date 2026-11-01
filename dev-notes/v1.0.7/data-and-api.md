# v1.0.7 数据与接口契约

状态：拟实施契约。基线为 `d1f8238`。阅读背景见[产品方案](../../product-design/v1.0.7/README.md)，推进顺序见[开发计划](development-plan.md)。下文新字段和端点都不是当前可直接调用的能力。

## 1. 数据职责与唯一来源

| 信息 | 唯一写入位置 | 读取方式与约束 |
|---|---|---|
| 线索身份、标题 | 现有 `ClueLedger.clues` | 同名不等于同一身份；沿用账本修订 |
| 线索主用途、作者事实、读者理解 | `StoryVariant.clue_plans` | 当前方案中的 `kind`、`author_truth`、`intention`；UI 将 intention 显示为“读者可能的理解” |
| 构思内容、计划章节、精确时间 | `StoryVariant.nodes` | 扩充现有节点，不另建 TimeNode 存储 |
| 先后关系 | 现有 `StoryVariant.event_times` | 只能证明相对顺序，不自动产生数值时刻 |
| 作者明确登记的正文关联 | `StoryVariant.bindings` | 扩展现有 ProseBinding，允许节点关联、线索关联或同时关联 |
| 从正文核对得到的观察与证据 | 现有 `ClueLedger.contributions` | 继续表达观察；作者分类不改写正文事实 |
| 线索之间的关系 | 新增 `StoryVariant.clue_relations` | 方案内关系，与节点推进边 `edges` 分开 |
| 稍后处理记录 | 新增 `StoryVariant.attention_receipts` | 只记录具体提醒指纹，不存一份可手改的同步状态 |
| 当前三状态、出现位置、时间轴 | 只读投影 | 根据当前来源计算，不单独维护镜像表 |

作者绑定和正文观察有不同来源。API 将两者归一为 `ClueOccurrenceView`，按当前 `clue_id + path + start + end` 合并相同位置，并保留来源引用列表；不因此新增第二套可编辑的 occurrence 存储。不同位置不合并，同一个位置的不同线索也不合并。

当前账本限制同一线索每章一条贡献。本版同章多次出现统一由多个显式 binding 保存；手动标记与模型核对后登记的每一个原文位置共用此入口，不用重复贡献绕过唯一约束。账本贡献继续提供既有正文观察，不再额外存一份完整标记列表。

`StoryDocument.schema_version` 从 1 升至 2；账本结构未变，`ClueLedger.schema_version` 保持 3。本版只实现新的构思格式，不转换旧构思状态，不为保持旧枚举增加兼容分支。

## 2. 核心类型

### 2.1 线索主用途

```python
ClueKind = Literal["truth", "mislead"]

class StoryCluePlan(StrictRecord):
    clue_id: StoryId
    kind: ClueKind | None = None
    author_truth: str | None = None
    intention: str = Field(default="", max_length=2000)
    resolution_intent: ResolutionIntent = "undecided"
```

用途属于方案，标题属于身份。修改用途通过 `upsert_clue_plan`，与已有计划字段一起提交；不得通过前端文本颜色或关键词推断。

`null` 表示尚未决定，不是第三种分类。模型未获授权确定用途时可留空；手动“标记为真相 / 误导”必须写入明确用途。`ClueUse.roles` 与账本中出现过程信息暂留给现有写作与核对消费者，撤除默认角色编辑界面；不能执行 `plant → truth` 这样的机械转换。

本版唯一枚举为 `truth / mislead`；讨论中的 `truth_clue / red_herring` 按同义设计用语理解，不另加接口别名。

### 2.2 直接正文关联

`ProseBinding.node_id` 改为可空，`clue_id` 继续可空；二者至少有一个。存在的目标必须属于当前作品与方案可见范围。

| 字段 | 规则 |
|---|---|
| `id` | 稳定关联身份，由服务端分配或校验既有目标 |
| `node_id / clue_id` | 至少一个；线索标记不要求创建节点 |
| `document_id / path` | 由受管章节目录解析，不能接受任意本机路径 |
| `digest` | 规范化正文的绑定时版本；由服务端校验并填充 |
| `start / end / quote` | 规范化 Markdown 源文本的 Unicode 码点半开区间 `[start,end)` |
| `span_digest` | 引文片段摘要；不因同章其他段落变化而失效 |
| `contract_digest` | 节点最近确认的语义契约；新格式无核对基准用 null，仅线索关联时也为 null，不参与节点状态 |
| `source` | 服务端标记来源，不能由 UI 冒充模型核对或作者确认 |
| `assessment_id / observation` | 可选核对依据与说明，不作为作者必填字段 |

UI 或模型输入使用轻量 `BindProseInput`，共同解析为内部完整 `ProseBinding`。前端提供当前摘要和准确源区间；模型可以只给引文和当前摘要，由服务端做唯一定位。两种输入使用同一校验器。

关联解析顺序：

1. 在同一工作区提交锁中读取当前章节，先校验请求的摘要与读取依赖。
2. 若提供源区间，必须满足 `text[start:end] == quote`，不能不匹配后再偷偷选择其他位置。
3. 未提供区间时，仅接受原文中唯一出现的引文；重叠出现也计入多次匹配。
4. 正文显示来源时，摘要未变可验证旧区间；摘要变化后仅允许唯一引文重定位。引文多处出现时返回歧义，不能因为旧位置恰好仍有同句就当作确定来源。
5. 重新定位是读结果，不在 GET 中改写绑定；确认或重新关联时才更新正式锚点。

正文含格式、实体和转义时，前端利用解析器源位置映射选区。无法证明连续源区间时提示改选片段或使用原文视图，不按渲染文本 `indexOf` 猜测。显示端沿用 `sourceRange` 的码点转 UTF-16 校验。

### 2.3 时间与披露位置

节点增加可选字段：

```python
class StoryEventSpan(StrictRecord):
    start_minute: int
    end_minute: int
    approximate: bool = False

class StoryNode(StrictRecord):
    # 其余既有字段沿用。
    planned_chapter_number: int | None = Field(default=None, ge=1)
    event_span: StoryEventSpan | None = None
```

`event_span` 的分钟数相对于当前方案唯一的故事时间基准，例如“故事第1天 00:00”，不采用运行主机时间或时区。方案增加 `time_origin_label`。结束不得早于开始；相等表示瞬时事件。跨午夜使用连续分钟数，不把次日零点排在前一天之前。

本版选择相对时间作为唯一数值输入基准。以“案发当天 00:00”为零点时，前一天 23:50 为 -10，当天 00:10 为 10。`time_origin_label` 是显示说明，不能解析标签中的日期来暗中重算节点。绝对日期输入、日历控件与日期自动换算不列入本版接口。

`time_note` 继续保存“案发前两天”“晚餐后”这样的作者描述。没有可比较数值时 `event_span = null`；相对先后关系只用于未定位组的合理排序，不生成假数值。需转换基准时整体校验受影响节点。

披露章节由有效绑定解析的真实章序决定；无有效绑定时使用 `planned_chapter_number`，并标识为计划。多章节点在最早披露章显示主行，详情列出全部出处；同章多节点按 `reveal_order` 和稳定 ID 排序。这里的 `reveal_order` 是显示次序，不代替实际时间与节点因果关系。

### 2.4 线索关系

```python
ClueRelationKind = Literal["supports", "contradicts", "co_misdirects"]

class ClueRelation(StrictRecord):
    id: StoryId
    from_clue_id: StoryId
    to_clue_id: StoryId
    kind: ClueRelationKind
    reason: str = Field(min_length=1, max_length=1000)
    basis_source_ids: list[str] = Field(default_factory=list)
```

| 类型 | 语义与说明要求 |
|---|---|
| `supports` | `from_clue_id` 增加 `to_clue_id` 所表达陈述或解释的可信度；`reason` 说明方向与依据，共同指向第三个结论本身不充分 |
| `contradicts` | 相同人物身份、时间与必要前提下，两个陈述不能同时成立；`reason` 指明比较的解释及前提，不判定哪条是真相 |
| `co_misdirects` | 两条信息共同强化同一个错误理解；`reason` 必须说明该理解，不根据两个端点的 `kind` 自动生成 |

端点类型不限制可登记的关系类别。服务端校验结构、引用和明确说明；作者或核对讨论承担语义判断，不能把数据校验通过表述为逻辑已经得到证明。

- `supports` 为有方向的支撑；`contradicts`、`co_misdirects` 为无方向关系，存储前按端点 ID 排序去重。
- 不允许指向自己。两个端点均需存在；同端点同类型关系不重复创建。
- 来源 ID 由服务端解析为当前绑定或账本证据，不接受浏览器拼接的“证据事实”。无正文依据时可作为作者计划关系保存，详情明确显示该性质。
- 关系图允许成环；不能把构思推进关系的无环限制套到线索关系上。
- 删除线索前必须明确处理其关系、用途与绑定。默认返回引用清单；作者可在一个变更中先解绑再删除，不做隐式全书级联删除。
- 类型改动不自动修改作者登记的关系；若它改变了关系说明所依赖的作者意图，详情提示重新核对，不静默重画成另一种关系。

主动核对中的“可能矛盾”只是一条带依据的对话建议，明确采用后通过现有联合变更写入。GET 不调用模型，不新增候选关系存储、自动采纳接口或后台检测任务。

## 3. 三状态与提醒契约

### 3.1 投影规则

优先级为 `attention > draft > ready`，但只在同一个节点的事实范围内聚合：

| 情况 | 状态 | 说明 |
|---|---|---|
| 没有关联、无该节点的未解决核对问题 | draft | 不根据其他章节是否有正文推断本节点已写 |
| 有未解决且仍适用的核对问题 | attention | 即使当前没有有效绑定，也展示具体问题 |
| 关联位置缺失、歧义或不可读 | attention | 停止高亮，保留原引用 |
| 关联尚无核对基准 | attention | 能定位不代表已确认符合构思 |
| 节点语义契约或对应片段发生变化 | attention | 原因分为构思变化、正文变化或两侧变化 |
| 所有关联有效，核对基准与当前内容一致 | ready | 不作全书语义正确性的承诺 |

多绑定节点有一处需核对则节点需核对；“部分正文尚未归属”作为定位说明，不新增第四种状态，也不强迫每个叙述段落必须对应构思。

章节状态只聚合该章涉及的节点；无节点关联的章节不制造“冲突”，显示“尚未关联构思”的普通说明。

### 3.2 契约摘要与指纹

节点语义契约包括动作、关键变化、时间约定、相关线索用途与作者事实、呈现意图、适用约定和必要知情变化。标题、页面排序、布局和无关节点不进入摘要。现有 `contract_projection` 需补齐引用线索计划中的适用字段。

提醒指纹由作品与方案身份、目标节点/绑定、当前语义契约、当前片段摘要、位置解析结果及适用核对问题组成。不能直接使用整份方案修订号，否则登记“稍后处理”本身就会使提醒再次出现。

生成方式为对字段排序后的规范 JSON 计算 SHA-256。绑定按 ID 排序，核对问题使用身份与内容摘要；不使用时间戳，也不采用“节点修订号 + 操作类型”作为替代。

指纹中的定位结果将 `exact / relocated` 统一为 `valid`，并区分缺失、歧义、不可读。不包含全文摘要、绝对字符偏移、标题、页面位置或无关方案修订，避免同章其他段落改动、插入导致的位置平移使已暂缓的同一差异重新突出。基准、当前片段内容、关联目标或适用核对依据变化时应改变指纹。

暂缓记录保存目标与当前指纹，可另存操作时间用于追溯。刷新、重启和暂缓本身引起的存储修订都不影响比较结果。

新增 `AcknowledgeAttention` 操作保存本次指纹；只影响提醒是否突出展示，**不改变 attention 状态**。同样指纹重复操作幂等，任一相关内容变化后重新提醒。

新格式移除 binding 上的 `waived_digest / waived_note`，由 `attention_receipts` 统一保存暂缓记录，避免同时存在两份提醒决定。读取投影不再输出 waived 状态。

新增 `ConfirmAlignment` 操作只用于作者明确确认，或与真实核对证据及修改一起提交。服务端检查操作者依据与当前指纹，逐个更新目标绑定基准。模型不能通过自己写一段“作者已确认”绕过依据检查。单纯选择“按构思”或“按正文”不自动调用此操作，必须先应用实际变更或明确确认保留差异。

### 3.3 创作差异与技术失败

- 创作差异：正文和意图可能不同。允许保存作者明确要求的修改，返回 `attention`。
- 技术失败：旧修订覆盖、原文区间不匹配、目标不存在、权限或修改范围不符。拒绝写入，返回事实和处理建议。
- 只读核对请求继续没有写权限。放宽创作差异提醒不扩大模型权限，也不取消候选质量检查与必要约定。

## 4. 页面动作与接口矩阵

以下路径省略 `/api/file-workspaces/{workspace_id}`。新增、调整与保留必须同时覆盖 UI、工具和测试消费者。

| 页面动作 | 接口 | 变化 | 后端负责 | 前端负责 |
|---|---|---|---|---|
| 读取方案与节点基础信息 | `GET /story/variants`、`GET /story/variants/{v}` | 保留并扩充 | 当前主方案、修订、节点与分类 | 选中对象与空状态 |
| 展示时间轴 | `GET /story/variants/{v}/timeline` | 新增 | 时间基准、章节分组、时间位置、三状态、出处引用 | 布局、可访问的点击、窗口化 |
| 查看当前提醒 | `GET /story/variants/{v}/sync` | 调整 | 三态、原因、指纹与当前来源版本 | 状态点、提醒及详情 |
| 正文显示构思出处 | `GET /story/variants/{v}/prose-marks?chapter_path=...` | 调整 | 仅有 node_id 的绑定、当前可定位区间与状态 | 定位与选中节点 |
| 正文显示线索 | `GET /clue-marks?chapter_path=...&variant_id=...` | 调整 | 精确来源、kind、去重和失效说明 | 统一标记、重叠选择 |
| 列出全书线索 | `GET /clues?scope=story&variant_id=...` | 调整 | 分类、分页、无节点合法空结果 | 列表、选择及可选文本查找 |
| 查看线索 | `GET /clues/{id}?scope=story&variant_id=...` | 调整 | 计划、出处、相关关系、修订 | 唯一详情组件 |
| 查看线索关系 | `GET /clue-relations?variant_id=...&focus_clue_id=...` | 新增 | 有界关系子图、未展示数量与修订 | 图/列表展示和键盘导航 |
| 打开账本证据 | `GET /source-locations/{evidence_id}` | 保留 | 来源状态与当前定位 | 复用现有来源阅读 |
| 创建、改类型、绑定、关系编辑 | `POST /story/changes` | 扩充操作 | 规范化、依赖校验、联合提交 | 一次动作一个请求键、等待回执 |
| 稍后处理、明确确认 | 同一 `POST /story/changes` | 扩充操作 | 指纹校验、确认依据、基准更新 | 重新读取状态 |
| 开启或继续讨论 | 现有会话创建 / 回合接口 | 扩充 context | 版本核验、引用冻结、review 用途 | 主动按钮、稳定请求键、恢复会话 |
| 撤销 | `POST /story/changes/{change_id}/undo` | 扩充逆操作 | 保留后续修改，冲突时拒绝 | 依据真实回执刷新 |

新默认工作区不再调用 `/story/variants/{v}/mystery`。P6 删除其唯一前端消费者后撤除端点；内部诊断函数按真实调用继续使用。`POST /clues` 的现有纯身份创建消费者在 P0 列清：UI 新创建统一改用 `/story/changes`，若不再有真实消费者则连同专用 DTO 删除，不保留两套创建入口。

## 5. 读响应约定

所有组合读模型返回 `variant_revision`、`ledger_revision` 和该投影的 `view_revision`；正文定位另带 `path` 与 `current_digest`。前端不能把不同作品或不同修订的时间轴、详情和标记直接拼接。

`ClueOccurrenceView` 提供 `origin`、来源引用列表、`binding_ids` 或 `evidence_ids`、当前定位状态、路径、引文与可选当前区间。作者绑定来源通过当前 `/clue-marks` 或 `/prose-marks` 结果核验和定位；账本证据继续走 `/source-locations/{evidence_id}`。不能将 binding ID 冒充现有 evidence ID 传入旧解析器。

### 时间轴响应轮廓

```typescript
interface StoryTimelineView {
  variant_id: string;
  variant_revision: string;
  ledger_revision: string;
  view_revision: string;
  time_origin_label: string;
  domain: { min: number; max: number } | null;
  groups: Array<{
    chapter_number: number | null;
    chapter_kind: "actual" | "planned" | "unassigned";
    nodes: Array<{
      node_id: string;
      title: string;
      time_note: string;
      start_minute: number | null;
      end_minute: number | null;
      approximate: boolean;
      status: "ready" | "draft" | "attention";
      binding_ids: string[];
    }>;
  }>;
}
```

`domain = null` 表示尚无数值时间。未定位节点仍在对应章节分组内显示，不能被筛掉。`binding_ids` 仅是应用内部定位依据，不作为产品标签。

### 线索关系响应

返回当前子图的节点摘要、关系、`remaining_count` 和 `view_revision`。超过预算时完整线索列表继续分页可达；图中缺少连线不能解释为已证实两条线索无关。分页游标包含投影修订，变化后重新读取，不串接不同修订页面。

无构思、无标记、无线索和无关系都返回合法空结果。GET 不为解决空状态而建立主方案；首次明确写入时才创建 `main`。空修订由服务端返回统一 `empty`，前端不自行推测版本。

路由沿用现有 `#/workspaces/{id}/writing`：正文无 mode，构思为 `?mode=story`，线索为 `?mode=clues`。新增 `node`、`binding` 查询参数用于恢复选中与定位，按现有身份规则校验并在读取时检查作品归属；既有 `source` 参数仍用于 evidence ID。`#/workspaces/{id}/ideas` 保持灵感语义，`#/library` 保持模块库语义。

## 6. 写入契约与示例

### 6.1 创建线索并登记当前选段

以下为拟议请求，摘要值为示例。引文“钟停在23:00。”共 9 个码点，区间与引文长度一致。

```json
{
  "variant_id": "main",
  "expected_revision": "story-revision-12",
  "expected_ledger_revision": "ledger-revision-8",
  "request_key": "mark-selection-01",
  "operations": [
    {"op": "create_clue_identity", "temp_id": "new-clock", "title": "停住的座钟"},
    {
      "op": "upsert_clue_plan",
      "clue_id": "new-clock",
      "plan": {
        "kind": "truth",
        "author_truth": null,
        "intention": "读者开始怀疑现场时间",
        "resolution_intent": "undecided"
      }
    },
    {
      "op": "bind_prose",
      "node_id": null,
      "clue_id": "new-clock",
      "path": "正文/0003 停住的钟.md",
      "expected_digest": "digest-of-current-chapter",
      "start": 128,
      "end": 137,
      "quote": "钟停在23:00。"
    }
  ],
  "prose_patches": [],
  "read_dependencies": [],
  "note": "作者将所选正文标记为真相线索"
}
```

绑定的正文读取依赖由服务端加入，不能因为客户端传了空列表就省略。三个操作在同一提交中分配身份、解析临时 ID、验证来源、写入并生成逆操作。只存在账本身份但缺少类型或正文关联不算这个动作完成。

### 6.2 允许的操作与结果

| 操作 | 关键字段 | 需要的校验 |
|---|---|---|
| `upsert_clue_plan` | clue_id、完整计划 | 目标存在、方案修订、用途合法 |
| `bind_prose` / `unbind_prose` | 目标、当前正文依据 / binding_id | 引文与摘要、引用完整性 |
| `update_node` | 完整节点，含可选时间与计划章节 | 不丢失未显示的现有字段；时间合法 |
| `upsert_clue_relation` / `remove_clue_relation` | 关系 / relation_id | 端点、对称去重、来源引用 |
| `acknowledge_attention` | node_id、当前 fingerprint | 指纹仍适用，状态不变 |
| `confirm_alignment` | 明确 binding_ids、fingerprint、确认依据 | 真实作者指令或可用核对记录，逐项更新 |

回执沿用 `change_id`、修订、账本修订与撤销能力，增加受影响的节点、线索、关系和路径列表及提醒摘要。UI 收到回执后失效相应缓存并回读；失败时保留输入，不乐观删除提醒或改为就绪。

同一 `request_key` 和同一请求返回原回执；同一键配不同请求返回冲突。正文、结构和账本按现有协调器形成一个恢复单元，不使用多个 HTTP 请求模拟原子提交。

## 7. 对话引用与实际调整

扩展既有 `ContextInput`，新增：

```typescript
interface StoryReconcileReference {
  variant_id: string;
  variant_revision: string;
  node_id: string;
  binding_ids: string[];
  warning_fingerprint: string;
}
```

前端把它放在 `context.story_reconcile`，并同时携带现有 `context.story`，设置 `purpose: "review"`、目标文件的 `review_paths` 和观察到的正文摘要。服务端解析真实材料，引用字段不能充当写权限或信任客户端提供的完整事实。

点击前不创建会话。点击后使用既有 `POST /conversations` 创建带首轮请求的核对讨论，保存提醒与会话的对应关系；同一版本提醒再点击打开该讨论。已有运行中的其他对话不被中断或混入材料。

作者在核对讨论中明确要求改正文或构思后，新回合按实际指令重新判定用途与范围。保留 v1.0.6 的用途修复：构思页可见不代表只能设计，用户说“只解释”也不能因为当前正文被选中就获得写权限。

提交时再次检查方案、账本、目标文件与提醒指纹。讨论期间资料变化则先回读说明，不能使用旧建议覆盖新稿。修改后重新计算关联与状态；模型说“已修复”但没有成功回执时，页面继续显示待处理事实。

## 8. 错误与失效处理

统一使用现有结构化错误字段 `code / where / why / fix`，产品显示格式为 `[错误码] 位置：现象。建议：…`。以下新码为计划，已有等价错误码可在 P0 统一命名后复用。

| 错误码 | 触发事实 | 处理建议 |
|---|---|---|
| `PROSE_SOURCE_STALE` | 选段时的正文版本已变化 | 刷新章节后重新选择片段 |
| `PROSE_ANCHOR_MISMATCH` | 请求区间与引文不一致 | 重新选取连续原文 |
| `PROSE_ANCHOR_AMBIGUOUS` | 引文在当前正文出现多次且无可信区间 | 指定准确位置或扩大引文 |
| `PROSE_ANCHOR_MISSING` | 当前正文已无对应原句 | 查看原引用并重新关联 |
| `STORY_ATTENTION_STALE` | 提醒在确认或讨论受理前已变化 | 读取当前差异后继续 |
| `CLUE_RELATION_INVALID` | 端点不存在、自关联或关系字段无效 | 选择有效的不同线索并填写说明 |
| `CLUE_IN_USE` | 删除目标仍有用途、关系或正文引用 | 查看引用并明确解除后删除 |

例如：`[PROSE_SOURCE_STALE] 第三章：正文在选段后已修改。建议：刷新章节并重新选择片段。`

无结果与失败分开：未登记线索是空列表；来源不可读是带原因的失败或失效项；读取失败不能伪装成“没有线索”。

## 9. 前后端收口检查

- 每个可点击写操作都登记请求、回执、失败和刷新路径。
- 每个新增端点都有实际组件或工具消费者；无消费者不发布。
- HTTP、工具输入和候选流程共享规范化服务，覆盖临时 ID 和来源校验。
- 新增关系和提醒操作都有逆操作、依赖校验和恢复测试。
- `kind`、三状态与原因枚举在 Python、前端、工具 Schema、提示词和夹具中一致。
- 移除旧 `/mystery` 前确认没有保留的 UI 消费者；保留内部领域函数不意味着保留无用 HTTP 入口。
- 新格式打开旧状态时给出明确格式错误和新建/重新导入建议，不增加隐式迁移或自动删除用户文件的逻辑。

# 1.0.3 接口与页面契约

更新：2026-10-31。图读取、固定列布局、真实阶段折叠、完整节点读取与任务草稿已接入，复验见[改造记录](notes/2026-10-31-story-remediation-implementation.md)。内部 `variant_id` 路径沿用，产品仍只有一份构思。下文其余目标字段以实际响应和验收记录为准；图响应中的账本修订参数尚未全部对齐。

## 1. 接口与使用方

所有路径以 `/api/file-workspaces/{workspace_id}` 为前缀，所有读取都不调用模型。

| 接口 | 变更 | 使用方 |
|---|---|---|
| `GET /story/variants` | 保留当前身份读取，仅返回当前构思 | `state/story.ts` 初始化 |
| `GET /story/variants/{variant_id}` | 返回 stages、完整 threads、宏观节点、边、线索使用引用和修订；不再按 `group_id` 丢掉其他阶段 | `StorySurface`、`StoryCanvas` |
| `GET /story/nodes/{node_id}?variant_id=...` | 补阶段、关键变化、统一线索摘要、关联有效性与正文入口 | 节点详情 |
| `GET /clues?scope=story&node_id=...` | 全书作者范围，包括纯计划线索；node_id 为空时列全部身份，存在时按节点使用过滤 | 新 `StoryClues` |
| `GET /clues/{clue_id}?scope=story` | 组合作者计划、全书使用过程和正文观察，提供反向节点引用 | 共用 `ClueDetail` |
| 现有 `GET /clues`、`GET /clues/{clue_id}` | scope 缺省为 chapter，保留章界读取；不自动加入作者未来计划 | `ChapterClues` |
| `POST /clues` | 保留新建身份，经相同提交和恢复边界处理 | 现有新建线索调用方 |
| `POST /story/changes` | 增加阶段、身份和计划操作，相关账本修订与回执映射 | Agent `update_story`、确定性页面动作 |
| `GET /story/drafts/{operation_id}` | 读取未整体提交草稿的必需约定和精确摘要 | `StoryDraft` |
| `POST /story/drafts/{operation_id}/confirm-constraints` | 作者确认屏幕中显示的来源约定，核验冻结版本；不直接提交故事 | `StoryDraft` |
| 现有改动读取、撤销、原文定位和任务接口 | 覆盖新增对象与账本依赖，保持既有交互 | 当前调用方 |

不添加备选、试读、独立候选正文或重复资料接口。扩展 scope 是读取范围，不是授权凭据。node_id 必须与当前作品和构思匹配，仅在 story 范围接受；非法组合返回明确错误。

## 2. 图响应

```typescript
interface StoryGraph {
  variant_id: string;
  revision: string;
  ledger_revision: string;
  stages: { id: string; title: string; order: number; summary: string | null }[];
  threads: {
    id: string; title: string; order: number;
    goal: string | null; obstacle: string | null;
    intended_resolution: string | null;
  }[];
  nodes: {
    id: string; title: string; action: string; change: string | null;
    stage_id: string | null; thread_ids: string[]; clue_ids: string[];
    primary_thread_id: string | null; is_convergence: boolean; reveal_order: number | null;
  }[];
  edges: {
    id: string; from_id: string; to_id: string;
    kind: "next" | "reframe" | "answer"; reason: string;
  }[];
  // 保留既有约定、实际时间与最新变更等必要字段。
}
```

统一采用 `thread_ids / clue_ids` 表示引用，标题单独从身份表查找，不能将同名标题放进引用字段。图只需轻量引用，完整呈现和正文证据按详情读取。阶段折叠是前端状态，折叠摘要与边是可重建的显示对象，不参与结构提交。

## 3. 线索响应与范围

story 范围的详情应包括：scope、稳定 ID 与标题、结构／账本／计划／组合视图修订、可空计划、使用位置、已有 observations 和 evidence。使用位置包含 node_id、stage_id、thread_ids、roles、呈现与预期理解；回收位置由这些使用导出。

chapter 范围维持既有截止规则，不暴露未来作者计划或以构思扩大 reader_seen。story 范围可以看到未来计划，正文观察默认截至最新实际章节；显式传入 as_of_chapter 时仅限制观察，不改变作者计划的性质。

无正文时，计划可见、observations 和 evidence 为空，实际生命周期为 unknown／暂无正文依据。不能以 roles=plant 或 resolve 改写实际状态。

列表沿用分页方式。分页游标绑定读取范围、过滤条件和组合视图修订；期间内容变化按当前项目冲突规则重新读取，不能将两个修订的分页结果混合。图或模型输入若需拆分，明确返回覆盖和省略对象，不静默丢掉后半部。

## 4. 变更与回执

请求保留 `variant_id`、`expected_revision`、`request_key`、`operations`、必要 prose_patches 和 read_dependencies。新增 `expected_ledger_revision`：操作引用／更新身份、计划或依赖正文线索时必需；仅改不涉及线索的阶段内容可不提供。

operations 纳入[数据契约](data-and-runtime.md)中的阶段、身份与计划操作。临时线索引用只允许在本请求中解析，保存前不得残留。回执包含结构修订、相关账本修订、change_id、changed 和 id_map。没有改变账本内容时不伪造新账本修订。

新增错误沿用 `[错误码] 位置：现象。建议：…`，例如：

```text
[STORY_STAGE_REFERENCE_INVALID] 构思保存：节点引用的阶段不存在。建议：重新读取阶段后调整节点归属。
[STORY_CLUE_REFERENCE_INVALID] 构思保存：线索身份不存在或临时引用未解析。建议：重新读取线索，并在同一提交中创建新身份。
[STORY_CLUE_REVISION_CONFLICT] 构思保存：相关线索在受理后发生变化。建议：保留当前草稿，重新读取后核对修改。
```

这些是拟议错误码；实施时统一错误映射与 HTTP 状态，不在前端临时拼另一份文案。已有错误码适用时继续使用，不为同一现象重复增加代码。

## 5. 页面与状态

| 内容 | 保存位置 | 生命周期 |
|---|---|---|
| 阶段、线目标、节点、关系、计划和实际观察 | 服务端当前格式 | 受版本、授权、提交和撤销保护 |
| 选中节点／线索、展开阶段、视口、返回位置 | 当前作品浏览状态 | 浏览、定位与返回时恢复 |
| 章界和当前正文路径 | 现有正文／线索状态 | 不因查看未来构思而扩大事实范围 |
| 本轮任务节点、线索与版本 | 受理快照 | 不随浏览变化 |

构思右侧使用 StoryClues：未选节点时看全书，选中后看相关线索。选择节点只更新浏览状态；点击查看相关线索才主动切换右侧标签。正文继续使用 ChapterClues。

ClueDetail 共用标题、正文依据和原文定位，story 范围增加计划及使用过程。作者真相与创作意图按需展开，不在每张图卡重复展示。

## 6. 布局与定位

2026-10-31 补充：故事线表头固定在画布顶部，横向平移、列宽和缩放与节点同步；目标与阻力可展开。内部保留隐藏测量占位，不增加结构数据或写接口。无故事线时不占用冻结区域，详见[冻结表头记录](notes/2026-10-31-story-frozen-headings.md)。

布局输入为阶段、故事线、节点尺寸和真实关系。横向固定线顺序，纵向按阶段及阶段内推进排列。计算高度采用实际文字尺寸，不能靠缩字维持固定卡高。无归属片段有明确区域，空列合法。

共同节点采用公共行且唯一绘制，列明参与故事线；不得为每条线复制一个节点。阶段折叠聚合边只表达原有连接，不补造不存在的因果，展开后恢复真实节点和边。

反向定位顺序：核对当前作品 → 从当前图确认节点 → 展开阶段 → 选中节点 → 等待本轮布局完成 → 按当前可读缩放移到目标。无效或已删除节点显示事实与处理建议。快速切换作品、重复点击和旧请求返回使用请求序号／取消机制隔离。

保留现有双向滚动、全貌和 1:1。窄屏按视图切换并保留对象，不能将三列文字无限压窄。返回正文来源时恢复展开阶段、节点和视口。

## 7. 模型引用与接口对齐

read_story 读取当前阶段、线程目标、节点、线索计划和边 reason；update_story 接受同一操作定义，不允许绕过联合提交。引用冻结对象与版本，作者计划分区不混入有效正文分区。

任务受理、继续、暂停、插话和上下文预算沿用现有入口。结构新增后实际发送内容必须包含必要关系，容量不足采用已有预算约束和有覆盖记录的拆分。

P0 固定空白、未定、多线、纯计划、已有正文、冲突和临时身份的 JSON 样例。后端响应模型、前端类型、工具 schema 和样例同时验证；每个新按钮登记使用的读取或写入能力，清理已经不再被调用的旧 group 参数与客户端方法。

## 8. 2026-10-31 已接入的草稿与布局补充

约定确认补充：confirm-constraints 现在支持作者审阅无模块来源的新增必需约定；非空模块来源继续核验原文。确认绑定当前任务草稿摘要，内容变更后失效，不扩大已有约定修改权限。前端不再以全部 source_ref 非空作为按钮条件。草稿读取与恢复输入带 author_confirmed_constraint_count。详见[创建构思确认修复](notes/2026-10-31-story-required-review-fix.md)。

后续修复：画布单卡禁止拖动及键盘移动，保留点击详情与整图导航；坐标不作为写入接口。结构任务工具结果统一为单个 JSON，包含执行预算和机器诊断；工具事件 summary 仅取作者摘要，result_detail 保留完整反馈。追加校验的问题列表同时返回 operation_index、batch_index 和 object_id。实现与验证见[交互约束与工具反馈](notes/2026-10-31-story-drag-and-feedback-implementation.md)。

普通对话明确要求创建构思时，受理层补入结构目标，不依赖前端是否打开构思面。结构工具的草稿读取支持 `source="draft", target_id="节点或其他对象 ID"`，返回匹配操作及索引；准备支持 `node_patches=[{node_id, fields}]`，仅修改唯一匹配的既有节点。两者是模型工具参数，不新增另一套 HTTP 写入入口。HTTP 草稿展示与确认仍使用任务草稿接口，确认只读取候选最终有效约定。

准备返回 `prepared`、`draft_revision`、`operations`（操作数）、`issues`、`pending_dependencies` 和 `next_step`。字段或操作应用错误保留失败标记，机器问题列表同时进入模型可见文本；准备中的尚缺引用列入 pending_dependencies，正式提交继续做完整验证。工具返回另带剩余模型回合和预留信息。

有未提交草稿的预算停止落为需处理，可继续同一任务；来源确认停止显示草稿及确认入口；failed 中已有草稿也可读取，但不因此授予所有旧 failed 操作继续能力。恢复输入带草稿检查点，服务重启后允许冻结合法空白 `empty` 修订。

画布采用左阶段栏 88、列间 20、行间 32，列宽按可用区域在 240—380 内计算。同行顶部对齐，跨列汇合唯一绘制；阶段摘要在提示或阶段详情中显示。初次视口为 100%，尺寸变化不将浏览器自动截断滚动范围写回视口。当前验证与规划边界见[后续实施记录](notes/2026-10-31-story-followup-implementation.md)。

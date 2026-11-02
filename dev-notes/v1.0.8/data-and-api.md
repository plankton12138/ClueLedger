# v1.0.8 数据、接口与上下文契约

日期：2026-11-02。状态：P1—P4 已实施，P5 验收完成；`/story/chapter-context` 与 `ContextInput.discussion_focus` 已接入。既有接口以当前代码为准。

## 1. 唯一来源

| 信息 | 来源 | 约束 |
|---|---|---|
| 情节安排、计划章节、时间和作者意图 | 当前 `StoryVariant` | 无信息可以留空，不能为完成度编造 |
| 线索稳定身份、正文观察和证据 | 现有线索账本 | 证词和假设仍是正文呈现，不自动成为作者真相 |
| 作者登记的节点 / 线索正文关联 | 当前方案 bindings | 与账本来源归一展示，保留各自身份 |
| 当前详情、随章列表、时间轴和关系图 | 只读投影 | 不建立副本或新的可编辑章节存储 |
| 探索建议 | 会话及既有任务草稿 | 未采用不进入正式方案；不新增建议管理中心 |
| 确定的修改 | 现有提交和逆操作记录 | 回执与版本决定页面结果 |

构思和线索仍是不同对象。两种布局改变读取范围，不改变对象身份。默认详情使用已存内容，GET 不调用模型生成叙述摘要。

## 2. 随章聚合读取（拟新增）

完整前缀为 `/api/file-workspaces/{workspace_id}`。新增：

`GET /story/chapter-context?chapter_path=...&variant_id=...&cursor=...&limit=20`

`chapter_path` 必填，路径必须属于该作品的正文；`variant_id` 可省略，读取当前主方案。无主方案时仍返回账本可验证出处，构思列表为空，不创建主方案。`limit` 范围 1—50。采用单一分页项目列表，客户端按 placement 分组；游标绑定作品、方案、文件摘要与读取修订，过期后重新从首屏读取。

建议响应：

```typescript
interface ChapterContextView {
  chapter_path: string;
  chapter_digest: string;
  variant_id: string | null;
  story_revision: string | null;
  ledger_revision: string;
  view_revision: string;
  items: Array<{
    object: { kind: "node" | "clue"; id: string };
    title: string;
    placement: "observed" | "planned" | "unresolved";
    occurrences: Array<{
      path: string;
      quote: string;
      start: number | null;
      end: number | null;
      state: "exact" | "relocated" | "missing" | "ambiguous" | "unavailable";
      binding_ids: string[];
      evidence_ids: string[];
    }>;
  }>;
  next_cursor: string | null;
  truncated: boolean;
}
```

- `observed` 只接收当前章有效来源，按源位置排序。若同一对象同时有有效和失效引用，以 observed 为主项并保留失效出处。
- `planned` 必须有当前章的明确计划依据；不得据此制造引文或源位置。
- `unresolved` 保留原来指向本章但现在失效的引用；不在正文高亮。
- 没有正文出处且没有本章计划依据的对象只出现在全书总览。
- 线索关联可以独立于节点存在；去重按线索身份、路径、实际位置，合并相同位置的来源 ID，多次出现保留。
- 三类列表为空是合法结果；文件不可读、服务失败不得伪装为空。
- 此接口是作者阅读视图，禁止作为试读模型的直接输入。

## 3. 讨论焦点与发送快照（拟扩充）

在现有 `ContextInput` 增加 `discussion_focus`，不替代 `story`、`clue_references` 等已有引用。由服务端规范化为一次材料快照，重复目标去重；新旧引用互相矛盾时拒绝受理并要求重新选择，不任意选择一方。

```typescript
type DiscussionFocus =
  | { kind: "object"; object_kind: "node" | "clue"; object_id: string;
      variant_id: string | null; story_revision: string | null;
      clue_revision?: string; view_revision: string }
  | { kind: "chapter"; path: string; digest: string }
  | { kind: "selection"; path: string; digest: string;
      start: number; end: number; quote: string };
```

作品身份以请求路径和服务端归属验证为准。选段区间使用 Unicode 码点、左闭右开，必须核验规范化源文件；模型不负责计算偏移或摘要。焦点用于找材料，不构成写权限。

点击讨论仅进入输入区。发送时冻结焦点与请求键，服务端按身份读取真实材料并记录修订。续聊按新指令重新判定用途，保存已受理材料；刷新、恢复及问题回答不能丢失引用。页面换对象只影响后续发送，不改变已运行回合。

作者讨论不强制使用提醒指纹；只有针对某条提醒核对时才复用 `story_reconcile`。无提醒、无正文、只有账本线索、无主方案等情况都应能讨论。

## 4. 前后端对应关系

以下路径均省略作品前缀。

| 用户行为 | HTTP / 工具 | 前端消费者与改动 |
|---|---|---|
| 全书构思列表、详情 | 既有 `/story/variants/{v}`、`/nodes/{id}` | StorySurface、ObjectReadDetail；只读 |
| 全书线索及详情 | 既有 `/clues?scope=story&variant_id=...`、`/clues/{id}` | ClueSurface、ObjectReadDetail；保留分页 |
| 随章相关 | 新增 `/story/chapter-context` | ChapterContext；新增 api 包装函数和类型 |
| 正文标记与构思出处 | 既有 `/clue-marks`、`/story/variants/{v}/prose-marks` | Paper、ClueMarks；源位置和身份同源 |
| 时间轴、关系图 | 既有 `/story/variants/{v}/timeline`、`/clue-relations` | 辅助视图；按需读取 |
| 定位出处 | 账本 `/source-locations/{evidence_id}`；绑定复用 marks 与正文定位 | 不把 binding ID 传成 evidence ID |
| 发起 / 继续讨论 | 既有会话首次发送与续聊接口，扩充 ContextInput | ConversationPanel、workspace API、discussionContext |
| 提议、采用与明确修改 | 既有任务 / 候选服务、`read_story`、`update_story` | 对话回执；补齐操作覆盖，不增加表单 REST 写入 |
| 查看与撤销变更 | 既有 `/story/changes/{id}`、对应 undo 路由与提交服务 | 对话回执；保持依赖检查 |

M3 必须检查 `update_story` 对提醒处理、线索身份创建和删除的实际覆盖；现有 HTTP 写接口不等于模型已能操作。缺失时在同一领域服务增加工具操作或适配，不能伪造写入完成。P0 逐条登记调用方，P5 删除确实无消费者的表单专用接口；不能直接删除被工具、候选或恢复流程使用的服务。

## 5. 写入协议

1. 由本轮明确文字和已有授权决定可修改对象与文件；查看或讨论引用不产生写权限。
2. 探索先给建议；明确指令直接执行范围内修改。采用的建议绑定具体内容及版本，不能仅凭「第二个」从新的列表猜测。
3. 服务端回读完整对象、规范化目标改动，保留未涉及字段，校验语义约束、引用依赖与范围。
4. 提交校验方案修订、账本修订、正文读取依赖和幂等键；版本变化先回读，不能用旧建议覆盖新稿。
5. 统一提交正式内容、关联和逆操作；若当前流程使用任务草稿，则须完成既有采用 / 发布流程，草稿更新不显示正式修改成功。
6. 成功回执区分正式已提交、候选待采用、无需修改和失败。复用现有回执字段并在前端归一，不凭回复正文判断结果。
7. 正式成功后失效受影响的全书、详情、随章、标记和提醒缓存；活动页面重新读取。撤销成功执行相同失效。

普通创作差异可以保留为提醒，不阻止继续写作。改构思不自动改正文；明确请求正文修改后才扩展写入范围。删除有依赖的线索时拒绝不完整修改，说明仍关联哪些对象；不能把删除线索默认解释成删除相关原文。

## 6. 材料装配与试读

请求按照本轮焦点加载当前内容、直接来源、适用作者约定，再按需要检索扩展。原文、作者计划与未采用提议分区装配，证词的说话人和原文来源保留。没有读取到的部分明确为未读取，不生成否定结论。

关系扩展受预算约束，不因关系图连通就自动取全图。预算内优先保存显式选段与定位依据；超过容量时采取既有补读 / 压缩机制并记录省略材料，不把缺失证据判为无矛盾。

试读从允许的正文及其截止范围内的观察重新装配，不复用作者视图的上下文快照。作者分类、隐藏真相、未来关系、带未来结论的摘要均排除。仅隐藏页面上的标签不能完成此隔离。

## 7. 状态与错误

内部沿用 ready / draft / attention，不新增面向作者的完整度状态。提醒只陈述可验证事实；原句失效保留历史引用并停止高亮。发送或提交失败保留用户输入，错误遵循 `[错误码] 位置：现象。建议：…`。

新增接口错误码在 P0 与既有 `WorkspaceError` 清单对齐；已有来源失效、旧修订、范围错误优先复用。新读接口须区分空数据、无主方案、权限 / 路径错误和来源不可读。

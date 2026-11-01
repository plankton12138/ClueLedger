# 1.0.6 构思总览与正文一致性：详细开发计划

> 状态：P1—P5 已实施，2026-11-01 收口到 **1.0.6**；完整在线语义冲突解决仍待验收。
> 本文件保留阶段范围与接口依据，实际交付以 [开发入口](README.md) 为准。
> 旧数据不迁移；新增字段带默认值，旧记录按「未核对」处理。

本版把构思从「一份结构图」推进为「全书纵览与行文大纲」，并让正文与构思之间形成可追踪、可确认的双向联动。上一轮确定的「取消本章线索、正文线索直接下划线」一并纳入，作为联动的正文侧入口。

## 1. 目标

1. 构思页能看清楚全书覆盖情况：哪些节点已经写成正文、哪些还没写、哪些章节没有纳入构思、哪些位置两边已经不一致。
2. 正文中与线索相关的已登记披露句直接显示下划线，点击打开左栏线索详情；取消「本章线索」独立板块。
3. 修改构思或正文时，应用和模型都先判断另一边是否受影响；有冲突时给出选项，作者确认后才正式修改。
4. 只有构思、还没有正文时，仍然直接从构思写正文，不增加确认步骤。
5. 解决冲突时，结构和正文可以原子提交，并沿用现有的撤销记录。

## 2. 不在本版范围

- 不做静默自动同步：不允许模型未经确认直接改写正文或改写构思。
- 不在每次保存后自动跑全书模型审计；只分析本次受影响的节点和章节。
- 不重新引入结构视图、本章线索面板或第二个大纲页面。
- 不为旧数据写迁移脚本；没有 `contract_digest` 的旧绑定显示为「未核对」。
- 不改变根目录 README。

## 3. 核心设计决定

### 3.1 构思是正文契约，正文是已发生事实

同一节点的 `action`、`change`、`clue_uses`、`knowledge_changes` 和实际时间，是正文必须兑现的契约；正文是已经写成的事实。双向联动比较的是这两层，不拿作者真相去和正文对照。叙述者说谎、侦探判断错误，只要符合线索计划和预期误读，就不是冲突。

### 3.2 联动是提案式，不是自动改写

应用先做确定性检查，模型只在确实影响另一边时做语义核对。冲突结果必须带着证据和选项交给作者；作者确认前不写正式结构或正文。

### 3.3 每次只分析受影响范围

- 构思变化：只找绑定到被改节点及其下游节点的正文。
- 正文变化：只找同一章、同一绑定区间附近的节点。
- 没有绑定正文时直接通过，不调用模型。
- 纯标题、展示顺序等不影响正文的字段变化直接通过。

### 3.4 绑定状态按节点计算，按章聚合显示

节点契约按节点摘要计算。正文改动以章为单位聚合，避免同一章里改一句旁白就让多个节点各自报警；应用内提交可以再按补丁区间重锚未受影响的绑定。外部编辑无法判断区间，按章标记「正文已变化，待核对」。

## 4. 数据模型

### 4.1 `ProseBinding` 增加两个同步指纹

现有 [`ProseBinding`](../../src/hastings/workspace/story_models.py) 已保存节点、路径、区间、引文和正文摘要。新增：

```python
class ProseBinding(StrictRecord):
    id: StoryId
    node_id: StoryId
    clue_id: str | None = None
    document_id: StoryId
    path: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    observation: str = Field(default="", max_length=2000)
    assessment_id: str | None = None
    #: 最近一次核对通过的节点正文契约；空串表示旧记录或尚未核对。
    contract_digest: str = Field(default="", max_length=128)
    #: 作者明确选择暂不处理的冲突摘要；任一侧再变化即失效。
    waived_digest: str = Field(default="", max_length=128)
    waived_note: str = Field(default="", max_length=1000)
```

`digest` 继续表示最近一次同步时的正文文件摘要；`contract_digest` 表示最近一次同步时该节点的契约摘要。两个都有值且分别与当前值一致，才视为 `aligned`。

### 4.2 节点正文契约投影

新增 `src/hastings/workspace/story_sync.py`。契约不是整份 `variant.revision`，而是按节点投影：

```python
def contract_projection(
    variant: StoryVariant,
    node: StoryNode,
    *,
    catalog: StoryClueCatalog,
) -> dict[str, object]:
    """正文必须兑现的节点内容；标题、阶段顺序等展示字段不进入契约。"""
    constraints = [
        item.model_dump(mode="json")
        for item in variant.constraints.values()
        if applies_to_node(item, node)
    ]
    event_times = [
        relation.model_dump(mode="json")
        for relation in variant.event_times
        if node.id in (relation.before_node_id, relation.after_node_id)
    ]
    knowledge = [
        change.model_dump(mode="json") for change in effective_knowledge(variant, [node.id])
    ]
    return {
        "action": node.action,
        "change": node.change,
        "time_note": node.time_note,
        "clue_uses": [
            {
                "clue_id": use.clue_id,
                "roles": use.roles,
                "presentation": use.presentation,
                "mode": use.mode,
                "speaker_id": use.speaker_id,
                "expected_interpretation": use.expected_interpretation,
            }
            for use in node.clue_uses
        ],
        "knowledge": knowledge,
        "event_times": event_times,
        "constraints": constraints,
    }


def contract_digest(
    variant: StoryVariant,
    node: StoryNode,
    *,
    catalog: StoryClueCatalog,
) -> str:
    payload = contract_projection(variant, node, catalog=catalog)
    return sha256_json(payload)
```

注意：

- `effective_knowledge` 让上游节点的知情变化也能影响下游正文的契约，避免只比较节点自身字段。
- 约束只取作用在全书、该节点或相关人物上的项。
- 实际时间只取与节点直接相关的关系，不把整条时间线塞进每个节点。

### 4.3 同步状态

```python
SyncState = Literal[
    "aligned",  # 两边一致
    "outline_ahead",  # 构思契约变了，正文未跟上
    "prose_ahead",  # 正文变化，构思未核对
    "diverged",  # 两边都变了
    "unchecked",  # 旧绑定或还没有做过核对
    "waived",  # 作者确认暂不处理，摘要仍匹配
]

ChapterSyncState = Literal[
    "aligned",
    "outline_ahead",
    "prose_ahead",
    "diverged",
    "unchecked",
    "unbound",  # 有正文，但没有绑定到构思
]
```

计算规则：

```python
def binding_state(
    binding: ProseBinding,
    node: StoryNode,
    current_contract: str,
    current_prose: str,
) -> SyncState:
    outline_changed = bool(binding.contract_digest) and binding.contract_digest != current_contract
    prose_changed = binding.digest != current_prose
    current_digest = conflict_digest_for(binding, node, current_contract, current_prose)
    if binding.waived_digest and binding.waived_digest == current_digest:
        return "waived"
    if not binding.contract_digest:
        return "unchecked"
    if outline_changed and prose_changed:
        return "diverged"
    if outline_changed:
        return "outline_ahead"
    if prose_changed:
        return "prose_ahead"
    return "aligned"
```

章节状态取本章绑定的最严重状态：`diverged > outline_ahead > prose_ahead > unchecked > aligned`。没有任何绑定的非空章节为 `unbound`。

### 4.4 冲突、决定与忽略

新增模型，放在 `story_sync.py` 或 `story_models.py`：

```python
ConflictChoice = Literal["follow_outline", "follow_prose", "defer"]


class SyncConflict(StrictRecord):
    conflict_id: str = Field(min_length=1, max_length=128)
    kind: Literal[
        "outline_ahead",
        "prose_ahead",
        "missing_presentation",
        "unbound_prose",
        "semantic",
    ]
    node_id: str = ""
    path: str = ""
    structure_quote: str = ""
    prose_quote: str = ""
    message: str
    options: list[ConflictChoice] = Field(default_factory=list)


class SyncDecision(StrictRecord):
    """作者对一次一致性冲突的选择；和结构/正文一起原子提交。"""

    conflict_digest: str = Field(min_length=1, max_length=128)
    choice: ConflictChoice
    #: 如果通过 ask_author 询问，记录问题标识；提交前核对已回答。
    question_id: str = Field(default="", max_length=64)
    note: str = Field(default="", max_length=1000)
```

`StoryChangeSet` 增加可选字段：

```python
class StoryChangeSet(StrictRecord):
    # ...现有字段不变
    sync: SyncDecision | None = None
```

`StoryChangeRecord` 也保存本次决定，便于回看和撤销。

冲突摘要 `conflict_digest` 由冲突 ID、当前节点契约摘要、当前正文摘要组成。决定里的摘要与提交时重算的不一致，返回 `STORY_SYNC_CONFLICT_STALE`，不能拿过期选择提交。

## 5. 后端实现

### 5.1 同步读模型

在 `story_api.py` 增加：

```http
GET /api/file-workspaces/{workspace_id}/story/variants/{variant_id}/sync
GET /api/file-workspaces/{workspace_id}/story/variants/{variant_id}/sync?chapter_path=...
```

返回：

```json
{
  "variant_id": "variant_...",
  "revision": "rev_...",
  "counts": {
    "aligned": 6,
    "outline_ahead": 2,
    "prose_ahead": 1,
    "diverged": 0,
    "outline_only": 3,
    "unbound_prose": 1
  },
  "nodes": [
    {
      "node_id": "node-board",
      "title": "发现异样",
      "state": "aligned",
      "chapter_paths": ["正文/0001.md"],
      "binding_ids": ["binding_..."],
      "contract_digest": "..."
    }
  ],
  "chapters": [
    {
      "path": "正文/0001.md",
      "chapter": 1,
      "title": "第一章",
      "state": "aligned",
      "node_ids": ["node-board"],
      "prose_digest": "...",
      "checked_digest": "..."
    }
  ],
  "conflicts": [
    {
      "conflict_id": "sync_...",
      "kind": "outline_ahead",
      "node_id": "node-board",
      "path": "正文/0001.md",
      "message": "节点呈现句已修改，正文还是旧句。",
      "options": ["follow_outline", "follow_prose", "defer"]
    }
  ]
}
```

实现要点：

- `StorySyncReader` 一次读取 variant、catalog、账本与章节目录，章节文件只读一次。
- `nodes` 只返回状态和绑定摘要，不返回大段正文；需要引文时由详情或模型读取。
- `unbound_prose` 只统计有内容、没有绑定的章节，不自动创建节点。
- `conflicts` 先返回应用能判断的机械冲突；语义冲突由模型核对后通过 `Assessment` 记录，不在这里假装已经判断。

### 5.2 提交前的同步闸门

在 `story_tools.update_story` 和界面变更提交路径中，写完结构操作后、真正提交前调用：

```python
def require_sync_decision(
    workspace: Workspace,
    root: Path,
    variant: StoryVariant,
    change: StoryChangeSet,
) -> None:
    conflicts = detect_sync_conflicts(workspace, root, variant, change)
    if not conflicts:
        return
    digest = sync_conflict_digest(conflicts)
    decision = change.sync
    if decision is None:
        raise WorkspaceError(
            code="STORY_SYNC_CONFIRM_REQUIRED",
            where="story.sync",
            why=f"这次修改影响 {len(conflicts)} 处已有正文，需要作者选择处理方式",
            fix="先向作者说明差异与选项；确认后带 sync 决定重新提交",
            details={"conflicts": [row.model_dump(mode="json") for row in conflicts]},
        )
    if decision.conflict_digest != digest:
        raise WorkspaceError(
            code="STORY_SYNC_CONFLICT_STALE",
            where="story.sync",
            why="冲突内容在作者确认后已经变化",
            fix="重新核对当前构思与正文，再请作者确认",
        )
    if decision.question_id:
        question = QuestionStore(root).get(decision.question_id)
        if question is None or not question.answered:
            raise WorkspaceError(
                code="STORY_SYNC_CONFIRM_REQUIRED",
                where="story.sync",
                why="没有找到作者对这次冲突的回答",
                fix="使用 ask_author 记录选项，等作者回答后再提交",
            )
```

检测范围：

- 结构变更影响的节点：被 `add/update/remove_node` 指向的节点，加上沿 `next` 关系可达的下游节点；只保留有绑定的节点。
- 正文变更影响的绑定：同一章内、补丁区间与绑定区间相交的绑定；同章其他绑定按补丁偏移重锚。
- 纯字段变化：`title`、`stage_id`、`reveal_order`、`thread_ids` 等不进入契约的变化不产生冲突。
- 删除节点：如果节点有绑定正文，必须选择「保留正文为未绑定」「同时删除/改写正文」「取消」，不能静默丢正文。

### 5.3 原子解决

沿用现有 `StoryChangeSet.operations` + `prose_patches` 联合提交：

- `follow_outline`：模型生成正文补丁；`prose_patches` + `bind_prose`（更新摘要、区间、`contract_digest`）一起提交。
- `follow_prose`：模型生成结构操作；节点契约随之更新，绑定更新 `contract_digest`，正文不动。
- `defer`：只提交作者本轮明确要求的一侧；绑定写入 `waived_digest` 和 `waived_note`。任一侧再变化，摘要不匹配，状态重新变成待处理。

提交成功后写入一条 `Assessment(kind="link_check", decision_source="author")`，记录本次核对范围、结构修订和正文摘要。撤销沿用现有 `StoryStore.undo`，反向操作和反向正文补丁一起恢复；绑定更新也在反向操作里。

### 5.4 线索内联标注接口

新增 `src/hastings/workspace/clue_marks.py`，在 `clue_api.py` 增加：

```http
GET /api/file-workspaces/{workspace_id}/clue-marks?chapter_path=...&as_of_chapter=...
```

返回：

```json
{
  "path": "正文/0001.md",
  "digest": "sha256...",
  "state": "ready",
  "marks": [
    {
      "clue_id": "clue-board",
      "title": "没有响的木板",
      "start": 183,
      "end": 195,
      "quote": "有人经过时木板没有响",
      "role": "plant",
      "mode": "action",
      "chapter": 1
    }
  ],
  "ledger_revision": "..."
}
```

`state`：

- `ready`：至少一条有效标注
- `empty`：本章已核对，没有标注
- `stale`：正文摘要与账本记录不一致，旧偏移不可用
- `unrecorded`：本章还没有线索记录

实现要点：

- 从已有账本 contributions 读取，不新增观察存储。
- 只取当前章路径、当前正文摘要一致的记录；再校验 `file.text[start:end] == quote`。
- 优先取 `mention_evidence`，其次取 `content.evidence` / `resolution_evidence` 中路径等于本章的记录。
- 同一线索同一区间去重；同一线索多个区间按出现顺序返回。
- 偏移保持 Python 码点坐标，前端用现有 `sourceRange` 转 UTF-16。

## 6. 前端实现

### 6.1 构思总览

- `api/story.ts` 增加 `StorySync`、`StorySyncNode`、`StorySyncChapter` 类型和 `readStorySync()`。
- `state/story.ts` 增加 `sync` 字段；`load` / `refresh` 时并列读取同步视图，失败不阻断结构图。
- 新增 `StorySyncStrip.tsx`，放在 [`StorySurface`](../../frontend/src/components/story/StorySurface.tsx) 工具栏下方：

```text
已对齐 6 · 待写 3 · 待同步 2 · 冲突 1 · 未纳入构思 1
```

数字是按钮：点击高亮对应节点或打开首条未纳入构思的章节。没有异常时只保留一行轻量数字，不新增大面板。

- [`StoryMysteryView`](../../frontend/src/components/story/StoryMysteryView.tsx) 的“实际发生”单元增加：
  - 章节标签，例如“第 1 章”
  - 一个状态点：未写 / 已对齐 / 待同步 / 冲突
  - `title` 和无障碍文本说明状态，不新增常驻长文案
- 目录侧栏的章节行显示「未纳入构思」或「待同步」，与构思页状态一致。
- 不新增第二个节点详情面板；状态详情通过 `<details>` 或紧凑浮层展示。

### 6.2 正文线索下划线

- 新增 `frontend/src/api/clueMarks.ts`。
- 新增 `frontend/src/components/workspace/ClueMarks.tsx`，实现多区间 rehype 插件；复用 [`SourceHighlight.tsx`](../../frontend/src/components/clues/SourceHighlight.tsx) 的源码位置思路，不搜索渲染后的 DOM 文本。
- [`Paper`](../../frontend/src/components/workspace/Paper.tsx)：
  - 章节正文读取后并行读取 `clue-marks`。
  - 插件把标注段包成可聚焦的 `span`，带 `data-clue-id`。
  - 点击或 Enter 打开左栏线索详情；已有文字选区时不触发，避免和 `SelectionToChat` 冲突。
  - 工具栏增加「线索标注」开关和数量；关闭后是纯阅读。
  - `state=stale` 时只显示一行“正文已修改，线索标注暂不可用”，不画旧线。
- [`WritingLayout`](../../frontend/src/components/workspace/WritingLayout.tsx)：
  - 删除 `chapter` 侧栏标签和 `ChapterClues` 引用；正文与构思都只保留「目录 / 全书线索」。
  - 增加 `openClueFromProse(clueId)`：清空节点过滤、选中线索、切到「全书线索」、打开左栏；右栏对话保持。
- `index.css` 增加低调下划线样式和 hover/focus 状态；不使用彩色正文。

### 6.3 冲突确认

不新建确认页面。复用现有 `ask_author` 与 [`AuthorQuestionCard`](../../frontend/src/components/chat/AuthorQuestionCard.tsx)：

- 模型发现冲突后，先说明：
  - 哪一处不一致
  - 构思要求与正文原文
  - 影响哪些节点和章节
  - 推荐哪一种处理
- 然后调用 `ask_author`，`options` 固定为：
  - `以构思为准，改写正文`
  - `以正文为准，更新构思`
  - `暂不处理，保留现状`
- 作者选择后，模型在下一轮带 `sync` 决定提交；提交前后端重算冲突摘要并核对问题已回答。
- 冲突卡的内容属于对话，不新增全局同步中心。

## 7. 模型与工具

### 7.1 新增只读工具

`read_story_sync`：

- 参数：`node_ids`、`chapter_paths`、`include_prose`（默认 `false`）。
- 返回：同步计数、节点状态、绑定路径/区间/引文、机械冲突。
- 最多少量节点；超出时按 `node_ids` 分页，避免把全书正文塞进上下文。
- 描述明确写：修改构思或正文前，先读取受影响节点的绑定与一致性状态。

### 7.2 修改工具

- `update_story` 增加可选 `sync` 参数（`conflict_digest`、`choice`、`question_id`、`note`）。
- `propose_story_structure` 增加可选 `sync` 参数；候选同时改结构和正文时，由候选保存该决定。
- `propose_story_scene` 增加可选 `sync` 参数；只在候选需要解决已有冲突时使用。
- 没有绑定正文、没有契约变化时，`sync` 不需要填写，流程保持轻量。

### 7.3 提示词

在 `prompts/authoring.py` 和 `prompts/tools.py` 增加：

- 改结构前先看受影响节点的绑定；可能影响正文时不要直接提交，先说明影响并询问。
- 写正文前先看节点契约；呈现句必须原样出现，线索标题不能写进正文。
- 正文写出新事实、线索或知情变化时，先登记结构变更，再做正文。
- 发现冲突时先思考并给推荐，再用 `ask_author` 给选项；不能替作者决定。
- 只有构思时直接从构思写，不要求确认。
- 不把作者真相当成正文核对标准；计划和叙述者谎言分开。

### 7.4 候选核对

- [`build_story_review`](../../src/hastings/workspace/story_review.py) 增加 `link:<node_id>:<path>` 要求：
  - 已绑定节点必须有 `presentation` 原句。
  - 正文变化必须能引用至少一句相关原文。
- `submit_story_candidate_review` 对 `link:` 项也要求 `quote` 存在于候选正文。
- 缺失呈现句继续作为 blocking finding；候选只能修复、补结构或请作者决定。
- 语义冲突由候选核对报告承担：模型必须逐项说明是否符合节点动作、变化、知情和时间；通过要有原文依据。

## 8. 阶段计划

### P0 基线与准备

- 冻结 1.0.5 测试基线，阅读现有 `ProseBinding`、候选提交、`update_story`、线索观察和左栏布局。
- 准备测试夹具：一个只有构思的作品、一个构思+正文已绑定的作品、一个正文未纳入构思的作品。
- 退出条件：基线测试通过，不改运行版本。

### P1 数据层与同步读模型

- 新增 `story_sync.py`：契约投影、摘要、状态、冲突摘要、下游影响范围。
- `ProseBinding` 增加 `contract_digest`、`waived_digest`、`waived_note`。
- `story_api.py` 增加 `/sync` 读接口。
- 测试：标题变化不改变契约；动作/线索/知情/时间变化会改变契约；状态六种转换；章节聚合；未绑定正文。
- 退出条件：接口能独立返回全书同步视图，不涉及写入。

### P2 构思总览界面

- `api/story.ts`、`state/story.ts` 接同步视图。
- `StorySyncStrip`、节点状态点、章节标签、目录未纳入构思标记。
- 测试：状态计数、点击高亮、无构思空状态、窄屏布局。
- 退出条件：构思页能一眼看出已写、未写、待同步、未纳入构思。

### P3 正文线索内联标注

- 后端 `clue_marks.py` + `/clue-marks` 接口。
- 前端多区间插件、`Paper` 读取与点击、工具栏开关。
- 删除 `ChapterClues` 使用与「本章线索」标签；左栏统一为「目录 / 全书线索」。
- 测试：plant/mention/resolve 标注、偏移、摘要失效、未登记、重叠、点击打开详情、关掉标注。
- 退出条件：正文可读、可标注、可跳转线索；没有重复的本章线索面板。

### P4 构思 → 正文确认

- `detect_sync_conflicts`：结构变更影响绑定正文。
- `require_sync_decision` 接入 `update_story` 与界面变更提交。
- `SyncDecision` 写入变更集与变更记录；`ask_author` 选项。
- 模型提示与 `read_story_sync` 工具。
- 测试：无正文直接通过；纯标题变化通过；动作/线索变化触发确认；过期决定拒绝；选择后原子提交；撤销同时恢复结构和正文。
- 退出条件：改构思不会在作者不知情时破坏已有正文。

### P5 正文 → 构思确认

- 候选核对增加 `link` 要求；缺失呈现句 blocking。
- 正文变化检测：新线索、新事件、知情变化、时间变化。
- 模型在候选里补结构，或在冲突后询问；`propose_story_structure` 支持同步决定。
- 外部编辑检测：摘要/引文失配时标记待核对，不自动改写。
- 测试：正文新增线索触发结构建议；正文违背知情触发冲突；以正文为准更新节点；以构思为准改正文；暂不处理后再变化会重新出现。
- 退出条件：正文改动能反向推动构思，且必须经过确认。

### P6 收口与发布

- 前端单测、`tsc --noEmit`、`npm run build`。
- 后端定向测试与一次扩展测试，不做无必要的全量重复。
- 浏览器：1680×1050 与 820×900 两档；三模式切换、深链刷新、左栏线索、冲突确认、撤销。
- 在线模型 E2E：使用 `.env` 在线模型，至少跑通「只有构思写一章」和「一处冲突的确认解决」。
- 文档：`product-design/v1.0.6/README.md`、`dev-notes/v1.0.6/README.md`、接口/验收记录。
- 版本号最后统一改：`pyproject.toml`、`src/hastings/__init__.py`、`frontend/package.json`、`frontend/package-lock.json`、`uv.lock`。
- 退出条件：验收清单通过，未完成项明确记录，不把计划当已实现。

## 9. 测试与验收

### 9.1 自动化测试

后端：

```bash
uv run pytest tests/workspace/test_story_sync.py -q
uv run pytest tests/workspace/test_clue_marks.py -q
uv run pytest tests/workspace/test_story_api.py tests/workspace/test_story_routing.py -q
uv run pytest tests/workspace/test_story_clue_observations.py tests/workspace/test_story_candidate.py -q
```

前端：

```bash
cd frontend
npm test -- --run
npx tsc --noEmit
npm run build
```

浏览器专项：使用真实工作区，覆盖桌面与窄屏，检查控制台无 error。

### 9.2 验收场景

1. **只有构思**：选中节点写正文，一次完成；写完后节点变为已对齐，正文出现线索下划线。
2. **改构思动作**：已有正文的节点修改动作；应用返回冲突，不写正文；作者选「以构思为准」后只改受影响段落，选「以正文为准」后只改构思。
3. **改构思线索呈现句**：旧正文中的原句缺失，必须确认；不能出现“构思已改、旧下划线还在”的情况。
4. **正文新增线索**：正文写入不在构思里的线索；模型提出新增线索使用点或要求作者确认，不静默丢失。
5. **正文改动知情/时间**：正文让角色提前知道，模型指出与节点知情冲突，给出提前知情或改正文两个方向。
6. **暂不处理**：作者选择保留差异，状态变为已确认差异；后续任一侧再变，重新提示。
7. **外部编辑**：直接改正文文件后打开，章节显示待核对；旧下划线不按旧偏移显示。
8. **撤销**：解决冲突后撤销，正文和结构同时恢复，绑定状态回到撤销前。
9. **未纳入构思**：正文有章节但无绑定，构思总览可见，不自动创建节点。
10. **界面回归**：没有「本章线索」tab，没有底部线索区；点击下划线打开左栏详情，右侧对话仍在。

## 10. 风险与处理

- **提示过多**：先由应用判断是否有绑定、是否触及契约；纯文字润色、标题、排序不打断。
- **摘要误报**：节点按契约、正文按章聚合；应用内补丁按区间重锚，外部编辑明确标待核对。
- **模型误判**：程序检查线索原句和摘要，语义判断必须引用原文；冲突最终由作者决定。
- **确认被绕过**：`sync` 决定绑定冲突摘要和问题标识；提交时重算，过期或未回答直接拒绝。
- **上下文成本**：`read_story_sync` 只读选中节点和受影响章节，正文按需读取；不扫全书。
- **删除节点丢正文**：删除有绑定的节点必须明确选择保留、改写或取消，不能静默删除。
- **撤销不完整**：同步决定、绑定更新、正文补丁都进入同一次变更记录；撤销前检查后续编辑冲突。

## 11. 文件清单

后端：

- 新增 `src/hastings/workspace/story_sync.py`
- 新增 `src/hastings/workspace/clue_marks.py`
- 修改 `src/hastings/workspace/story_models.py`
- 修改 `src/hastings/workspace/story_store.py`
- 修改 `src/hastings/workspace/story_api.py`
- 修改 `src/hastings/workspace/story_tools.py`
- 修改 `src/hastings/workspace/story_candidate.py`
- 修改 `src/hastings/workspace/story_review.py`
- 修改 `src/hastings/workspace/story_context.py`
- 修改 `src/hastings/prompts/authoring.py`
- 修改 `src/hastings/prompts/tools.py`
- 修改 `src/hastings/workspace/clue_api.py`
- 修改 `src/hastings/workspace/clue_views.py`
- 新增/修改对应测试文件

前端：

- 新增 `frontend/src/components/story/StorySyncStrip.tsx`
- 新增 `frontend/src/components/workspace/ClueMarks.tsx`
- 新增 `frontend/src/api/clueMarks.ts`
- 修改 `frontend/src/api/story.ts`
- 修改 `frontend/src/state/story.ts`
- 修改 `frontend/src/components/story/StorySurface.tsx`
- 修改 `frontend/src/components/story/StoryMysteryView.tsx`
- 修改 `frontend/src/components/workspace/Paper.tsx`
- 修改 `frontend/src/components/workspace/WritingLayout.tsx`
- 修改 `frontend/src/index.css`
- 新增/修改对应前端测试

文档：

- `product-design/v1.0.6/README.md`
- `dev-notes/v1.0.6/README.md`
- `dev-notes/v1.0.6/acceptance.md`
- 更新 `dev-notes/README.md`、`product-design/README.md`

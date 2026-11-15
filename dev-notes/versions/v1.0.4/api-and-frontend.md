# 1.0.4 接口与前端契约

## 1. 只读悬疑投影

### 路由

```text
GET /api/file-workspaces/{workspace_id}/story/variants/{variant_id}/mystery
```

返回当前方案的悬疑投影。只读，不产生修订，不调用模型。方案不存在时沿用 `STORY_VARIANT_NOT_FOUND`；作品目录不可用时沿用现有作品错误。

### 响应

```json
{
  "variant_id": "main",
  "revision": "rev_0123456789abcdef",
  "order": ["n-past", "n-letter", "n-arrive"],
  "time_status": {
    "state": "partial",
    "registered": 2,
    "unregistered": 1,
    "total": 3
  },
  "nodes": [
    {
      "id": "n-past",
      "time_status": "registered",
      "before_ids": [],
      "after_ids": ["n-letter"],
      "clue_uses": [
        {
          "clue_id": "c1",
          "title": "没有响的木板",
          "roles": ["plant"],
          "presentation": "走廊的木板没有响",
          "mode": "action",
          "speaker_id": "",
          "expected_interpretation": "读者会以为有人熟悉走廊"
        }
      ],
      "knowledge_changes": [
        {
          "subject_id": "detective",
          "claim": "有人熟悉走廊结构",
          "stance": "believes",
          "source_kind": "inference",
          "source_node_ids": ["n-arrive"],
          "effective_event_node_id": "n-past"
        }
      ]
    }
  ],
  "clues": [
    {
      "clue_id": "c1",
      "title": "没有响的木板",
      "roles": ["plant", "develop", "resolve"],
      "author_truth": "木板被人提前处理过",
      "intention": "先让读者怀疑熟悉走廊的人",
      "resolution_intent": "explain",
      "has_identity": true,
      "uses": [
        {
          "node_id": "n-past",
          "node_title": "十三年前的账",
          "roles": ["plant"],
          "presentation": "走廊的木板没有响",
          "mode": "action",
          "speaker_id": "",
          "expected_interpretation": "读者会以为有人熟悉走廊"
        }
      ]
    }
  ],
  "subjects": [
    {
      "subject_id": "detective",
      "claims": 1,
      "stances": ["believes"],
      "node_ids": ["n-past"]
    }
  ],
  "diagnostics": [
    {
      "code": "STORY_MAIN_CHAIN_LONG",
      "object_id": "main",
      "related_ids": ["n-past", "n-letter"],
      "message": "最长推进链覆盖 20/20 个节点，其余故事线没有形成独立推进",
      "suggestion": "先核对实际时间与推进依赖，再给各线安排自己的关键变化和汇合"
    }
  ],
  "counts": {
    "nodes": 3,
    "clues": 1,
    "event_times": 2,
    "knowledge_changes": 1,
    "subjects": 1
  }
}
```

### 排序规则

1. 以 `event_times` 构造有向图，拓扑排序得到实际时间顺序。
2. 只保留两端都存在的节点关系；重复关系忽略。
3. 没有出现在任何 `event_times` 中的节点，按阶段顺序、`reveal_order`（空值排后）、节点 ID 追加；没有阶段的节点排在最后。
4. 如果 `event_times` 出现循环，不返回 500：按同一回退规则追加剩余节点，并增加 `STORY_TIMELINE_CYCLE` 诊断。
5. `time_status.state`：
   - `complete`：所有节点至少出现在一条实际时间关系中；
   - `partial`：部分节点已登记；
   - `unregistered`：没有节点登记实际时间。
6. 单个节点的方案不产生“时间未登记”诊断。

### 线索聚合

- 来源：`StoryVariant.clue_plans`、节点 `clue_uses` 和 `StoryClueCatalog`。
- 只出现于计划、没有使用点的线索也返回，`uses` 为空。
- 只出现于使用点、没有计划的线索也返回，`author_truth` 为 `null`，`resolution_intent` 为 `undecided`。
- 标题来自线索账本；没有身份时回退为 `clue_id`，并保留 `has_identity=false`。
- `roles` 按 `plant → develop → misdirect → reinterpret → resolve` 去重排序。
- `uses` 按投影的实际时间顺序排序；同一节点内按线索 ID 排序。
- 不把 `presentation`、`expected_interpretation` 或角色说法复制成 `author_truth`。

### 知情与主体

- 每个节点的 `knowledge_changes` 原样返回，保留 `subject_id`、`stance`、`source_kind`、`source_node_ids`、`effective_event_node_id`。
- `subjects` 是视图用的聚合：按 `subject_id` 统计 claim 数、stance 和涉及的节点；本版直接显示 `subject_id`，不解析角色名。
- 不新增“读者”角色对象。读者层由 `clue_uses.expected_interpretation` 和 `misdirect/reinterpret` 角色投影出来；作者想写“读者实际相信什么”时，仍写进 `expected_interpretation`。

## 2. 诊断码

现有诊断保留：

- `STORY_THREAD_INCOMPLETE`
- `STORY_SINGLE_CHAIN`

本版新增：

| 诊断码 | 触发 | 建议 |
|---|---|---|
| `STORY_MAIN_CHAIN_LONG` | 最长 `next` 路径 ≥ `max(4, ceil(0.6 × 节点数))` | 核对实际时间与推进依赖，给各线安排独立关键变化和汇合；不要为图形添加虚构分支 |
| `STORY_TIMELINE_EMPTY` | 节点数 > 1 且没有任何 `event_times` | 为案发窗口和关键节点登记 `set_event_time` |
| `STORY_TIMELINE_PARTIAL` | 部分节点不在任何 `event_times` 中 | 补齐实际先后；未登记节点会在横轴末尾按叙述顺序追加 |
| `STORY_TIMELINE_CYCLE` | `event_times` 存在循环 | 检查实际时间关系，保留可解释的先后 |
| `STORY_CLUE_LIFECYCLE_INCOMPLETE` | 线索有使用点，但角色中没有 `resolve` 或 `reinterpret` | 为线索安排回收或重新解释；保留开放结局时写明原因 |
| `STORY_CLUE_READING_MISSING` | 线索含 `misdirect` 或 `reinterpret`，但没有任何非空 `expected_interpretation` | 补写这条线索想让读者先相信什么 |
| `STORY_CLUE_TRUTH_MISSING` | `resolution_intent=explain` 但 `author_truth` 为空 | 补作者真相，或把回收方式改为“保留开放/未定” |
| `STORY_CLUE_UNUSED` | 有计划但没有使用点 | 把线索接入至少一个节点，或删除未使用计划 |
| `STORY_KNOWLEDGE_MISSING` | 有线索使用点，但全书没有任何角色知情 | 在关键线索节点写 `knowledge_changes`，记录谁知道或相信什么 |

诊断只进入提交结果的 `structure_issues` 和悬疑视图顶部，不阻断提交。错误信息仍用现有格式；诊断不是错误，不显示错误码前缀。

## 3. 前端结构

### 视图切换

`StorySurface` 顶部增加：

```text
[悬疑] [结构]   全部 · 阶段 A · 阶段 B ...
```

- 默认“悬疑”。
- 阶段筛选继续作用于两个视图；悬疑视图按节点 `stage_id` 过滤列，诊断仍显示全局结果并标注“全局”。
- “结构”保留现有 `StoryCanvas`、节点详情、撤销和引用到对话。

### 悬疑视图

组件：`frontend/src/components/story/StoryMysteryView.tsx`。

布局：

1. **顶部**：标题、时间覆盖（已登记/总数）、线索数、诊断数；诊断条可展开。
2. **时间轴**：
   - 横向滚动；左侧 112px 固定泳道标签。
   - 列按投影 `order` 排列；每列头部显示时间文本（`time_note`）或“时间未登记”、阶段名和节点序号。
   - 三行：
     - **实际发生**：节点标题、动作、变化。
     - **侦探与助手**：该节点的 `knowledge_changes`，按 `subject_id` 分组；显示“知道/相信”和 claim。没有记录时显示“未登记”。
     - **读者**：该节点上非空的 `expected_interpretation`；带 `misdirect/reinterpret` 角色的线索优先显示。没有记录时显示“未登记”。
   - 最后一行 **线索**：该节点使用的线索 chips；点击选中线索。chip 显示标题和角色缩写，颜色区分埋设、误导、回收。
3. **下方**：左侧线索列表，右侧线索详情。
   - 列表：标题、角色摘要、使用点数。
   - 详情：角色、作者意图、回收方式、作者真相；表面呈现列表（节点、presentation、mode/speaker、expected_interpretation）；相关知情（聚合该线索使用节点的 knowledge changes）；生命周期（按角色列节点按钮）；相关诊断。
   - 每个节点按钮提供“在结构图中查看”，切换到结构视图并选中该节点。
4. **空/错/旧修订**：
   - 没有节点：显示“还没有构思结构”。
   - 没有线索：显示“还没有线索；先在设计里建立线索身份与使用点”。
   - 读取失败：沿用接口错误码；在悬疑视图内显示原因和“重新读取”按钮，不伪造空数据。
   - 图修订变化时重新读取；旧响应不得覆盖新修订。

### 前端类型

在 `frontend/src/api/story.ts` 增加：

```ts
export interface StoryMysteryUse {
  node_id: string;
  node_title: string;
  roles: string[];
  presentation: string;
  mode: string;
  speaker_id: string;
  expected_interpretation: string;
}

export interface StoryMysteryClue {
  clue_id: string;
  title: string;
  roles: string[];
  author_truth: string | null;
  intention: string;
  resolution_intent: "explain" | "leave_open" | "undecided";
  has_identity: boolean;
  uses: StoryMysteryUse[];
}

export interface StoryMysteryNode {
  id: string;
  time_status: "registered" | "unregistered";
  before_ids: string[];
  after_ids: string[];
  clue_uses: StoryMysteryUse[];
  knowledge_changes: Record<string, unknown>[];
}

export interface StoryMystery {
  variant_id: string;
  revision: string;
  order: string[];
  time_status: {
    state: "complete" | "partial" | "unregistered";
    registered: number;
    unregistered: number;
    total: number;
  };
  nodes: StoryMysteryNode[];
  clues: StoryMysteryClue[];
  subjects: { subject_id: string; claims: number; stances: string[]; node_ids: string[] }[];
  diagnostics: {
    code: string;
    object_id: string;
    related_ids: string[];
    message: string;
    suggestion: string;
  }[];
  counts: {
    nodes: number;
    clues: number;
    event_times: number;
    knowledge_changes: number;
    subjects: number;
  };
}
```

`readStoryMystery(workspaceId, variantId)` 只做请求和类型转换，不在客户端重排实际时间；排序由服务端投影决定。

### CSS

新增 `story-mystery-*` 与 `story-view-switch` 类。沿用现有纸面/边框/字号变量；实际发生用蓝色，侦探与助手用琥珀色，读者与误导用红色，线索用中性灰蓝。横向滚动容器必须支持键盘与触摸；固定标签列使用 `position: sticky; left: 0`，不遮住节点内容。移动端底部列表与详情改为上下堆叠，不隐藏诊断。

### 现有结构视图

- 不改 `StoryCanvas` 的布局、节点选择、冻结表头、滚动条和撤销。
- 节点详情里的“线索”展示保留；悬疑视图不替代它，只提供另一种读法。
- 阶段折叠只影响结构视图的节点可见性；悬疑视图用阶段过滤列，不折叠投影。

# V03-0.6 Beat 引用盘点与替换表

> 状态：盘点完成，待 V03-3.1 执行转换。生成：2026-10-17，基线 commit `aea2ed1`（v03-0.4 后）。
> 扫描范围：`Beat`、`at_beat_id`、`has_accepted_beats`、`TASK_NO_BEAT_TARGET`、`narrative.beat`、`plot_beat`、`beat_states` 全仓（src/hastings、tests、frontend/src）。
> 纪律：存量数据**暂不删除、暂不转换**——转换在 V03-3.1 按「新路径无引用」的证明之后执行（执行指导 §4.6）。

## 1. 引用分类总览

| 分类 | 文件数 | 处置 |
|---|---|---|
| 活动路径（0.3 运行链路仍在读/写 Beat） | 9 | V03-3.1 替换后删除活动类型 |
| 历史只读（旧数据/旧快照依赖） | 4 | 改到只读 legacy 模块，不创建新 Beat |
| 测试夹具（0.2 行为回归） | 若干 | 保留作历史回归；新旅程测试改 0.3 需求 |
| 前端 | 0 | 无 Beat 引用（chat.ts 的 "beat" 匹配为 heartbeat 注释） |

## 2. 活动路径引用（逐文件逐条）

| 文件:行 | 引用 | 当前作用 | V03-3.1 处置 |
|---|---|---|---|
| `domain/story/kinds.py:33,53` | `KIND_BEAT="narrative.beat"` 进入 `KNOWN_STORY_KINDS` | 活动 Schema 承认节拍为合法对象类型 | 从活动集合移除；legacy 只读集合保留 |
| `domain/story/narrative/models.py:132-216` | `class Beat`（beat_id/enter/exit/purpose…） | 节拍领域模型与校验 | 活动类型删除；模型移 legacy 只读模块 |
| `domain/story/narrative/commands.py:43-47,140-268` | `create_beat/move_beat/revise_beat_purpose` 及关系（sets_up/pays_off/constrains） | 节拍写命令 | 删除活动命令；有效信息按 §3 替换表转换 |
| `domain/story/narrative/state.py:9,54,65,82-97,114-147,160-188,218,266` | `KnowledgeState/Disclosure.at_beat_id`、`changed_at_beat_id`、按 beat 折叠状态 | 人物知情/披露按节拍位置折叠 | 锚点改「获知事件/正文位置」；折叠语义按 §3 三类新表示重建 |
| `domain/story/mystery/models.py:71-154` | `KnowledgeChange/ReaderDisclosure.at_beat_id`（含 wire 字段） | 谜题领域知情/披露记录 | 字段改新锚点；旧字段历史只读 |
| `domain/story/mystery/rules.py:36,56-57` | 状态去重键含 `at_beat_id`；披露按 beat 归类 | 公平性规则依赖节拍位置 | 改按获知事件/叙述位置 |
| `domain/story/canon/commands.py:357-401` | `RecordKnowledgeChange(at_beat_id=...)` 校验 beat 存在并建关系 | 知情变更命令 | 改收新锚点；旧入参历史只读 |
| `domain/story/views.py:211-331` | `story_state` 投影：披露/认知按 `at_beat_id` 定位，未绑定按起点记录并告警 | 查询层把节拍位置折叠成状态 | 投影改新锚点；「未绑定」告警保留（改文案） |
| `domain/conversation/task_drafts.py:43,86-96` | `ProjectView.has_accepted_beats`；`TASK_NO_BEAT_TARGET`/`TASK_NO_PLAN` 缺失项 | 起草前置：必须有已接受节拍 | 删缺失项；起草输入改为正文目标/作者要求/范围（空仓库合法） |
| `application/workflows/agent_workflows.py:88-96`（draft） | `DRAFT_BEAT_REQUIRED`：targets 必须全为 beat | 起草目标只能是节拍 | 改为正文范围/账本引用目标 |
| `application/contexts/__init__.py:73-128` | `ordered_beats` + `beat_states` 证据装配 | 任务上下文折叠节拍状态 | 改「约定→正式账本→可用摘要→当前任务」装配（M07，V03-4.2）；节拍折叠删除 |
| `application/story_proposals.py:45-70` | 规划候选创建 `narrative.beat`；beat 特判校验 | 规划功能产出节拍 | 规划候选改产出故事对象/事件（V03-3.2/3.3） |
| `domain/changes/planning.py:85-88` | "beat"/"plot_beat" 进入规划节点类型 | 变更规划识别节拍 | 活动集合移除 |
| `domain/changes/patch_guard.py:27` | `at_beat_id` 进补丁受护字段 | Patch 不得改节拍锚点 | 字段清单改新锚点名 |
| `domain/changes/scope.py:28` | `at_beat_id` 进范围识别字段 | 影响范围识别 | 同上 |
| `server/v2.py:341-360,407` | `project_view` 计算 `has_beats`；`has_accepted_beats` 外泄到任务卡 | 任务卡展示节拍前置 | 随 task_drafts 缺失项一并删除 |

## 3. 历史只读引用（保留数据，不创建新 Beat）

| 位置 | 内容 | 处置 |
|---|---|---|
| 既有仓库数据中的 `narrative.beat` / `story.plot_beat` Artifact 与 Revision 谱系 | 0.2 积累的节拍对象 | 原样保留；legacy 只读模块可读（V03-3.1） |
| 旧 Job payload 的 `context.evidence.beat_states` | 已完成任务的输入快照 | 只读回看；新任务不再产出 |
| `tests/v2/manuscript`、`tests/v2/story`、`tests/v2/agents` 中节拍夹具与断言（如 closed_loop 的 `beat_states[0].enter.at_beat_id`） | 0.2 行为回归 | 保留至 V03-3.1 替换证明后清理；closed_loop 已在 V03-0.3/0.4 改造中断开自动链，剩余节拍断言随 V03-3.1 更新 |
| `domain/story/mystery/*` 旧 wire 字段（`at_beat_id`） | 历史记录格式 | 兼容读取（识别旧字段），不可通过兼容路径创建新 Beat（计划 §8.2） |

## 4. 替换表（计划 §8.2 + 引用位置）

| 旧信息 | 目标去向 | 引用位置（§2 表） | 无法确定时 |
|---|---|---|---|
| 人物目标、关系或状态变化 | 对应人物/正式关系与已有故事事件 | state.py、mystery/models.py、canon/commands.py | 保留原文与来源，标待确认，不猜转换 |
| 伏笔、未解疑问、揭示安排 | 对应人物/线索/事件的叙事安排及正文位置 | views.py、state.py | 没有可靠对应则留历史待整理；不创建替代 Beat 对象 |
| 视角、写作目的、长度/范围 | 历史任务快照；作者明确延续时进入新任务要求 | contexts、task_drafts | 不自动变成本书约定 |
| 角色知情 `at_beat_id` | 获知事件/故事时间依据及来源 | mystery/models.py、canon/commands.py、state.py | 标未知/需核对，禁止按旧顺序强推人物已知 |
| 读者披露 `at_beat_id` | 正文位置/章节叙述顺序与来源 | mystery/models.py、views.py | 无正文依据只保留安排，不能视为已经实现 |
| Beat→正文 `renders` 等关系 | 有确定映射的正文证据关联 | narrative/commands.py | 保留旧引用，只读回看并标需核对 |
| 未完成节拍生成/审阅任务 | 历史任务与原输出；新流程按作者选择重新准备 | jobs（旧自动队列已 V03-0.4 隔离） | 不自动转换为可发布新任务，不重复调用模型 |

升级报告要求（计划 §8.2）：逐项说明自动转换、仅保留历史、需作者确认与无法映射的数量和 ID——在 V03-3.1 执行转换时产出。

## 5. 三类新表示草案（故事时间、获知事件、叙述顺序三者分开）

格式版本冻结：`story_position.v1` / `story_event.v1` / `disclosure_position.v1`（schema_version=1）。领域类型在 V03-3.1 落库；本草案冻结形状与版本号，后续阶段不得分歧造格式。

```text
story_position.v1        正文源位置（替代 at_beat_id 的「在哪里」锚点）
  project_id, commit_id, chapter_id, unit_id, start_offset, end_offset
  —— TextUnit 稳定 ID + 选区；分段/合段/删除不凭章节全文哈希冒充精确来源（计划 §4.1）

story_event.v1           故事事件（故事时间轴上的事实；获知事件是其 kind 之一）
  event_id, kind, participants[], location_ref?
  story_time = {kind: point, at} | {kind: interval, from, to} | {kind: unknown}
  —— 故事时间与叙述顺序无关；未知保留 unknown，不猜精确时间（M12 时间线）

disclosure_position.v1   披露位置（读者视角的叙述顺序 + 获知来源）
  fact_id, narrative_order{chapter_id, unit_id, position}, knowledge_sources[story_event ref]
  audience, mode(truth|testimony|belief)
  —— 读者披露按叙述顺序；人物获知按获知事件；两者不混用一个 chapter_index（M12）
```

不变量落点：I09（真相/证词/认知/披露/计划实现分别保存）由 `disclosure_position.mode` 与 `story_event.kind` 承载；I08（使用正文证据前检查有效性）由 `story_position` 的 commit_id/unit_id 版本锚点承载；I14（活动 Schema/工具/上下文无 Beat）由 §2 表的删除清单证明。

## 6. 移交 V03-3.1 的执行要点

1. 先证明新路径无 Beat 引用（活动契约扫描：Schema/工具/上下文三类入口），再删活动类型；旧快照依赖改到只读 legacy 模块。
2. `task_drafts` 缺失项与 `agent_workflows.draft` 目标校验先改（V03-3.3 无前置起草的前置），`contexts` 装配跟随 M07。
3. 存量转换按 §4 替换表逐项执行并产出升级报告（数量/分类/ID），转换创建新 revision，不原地改不可变记录。
4. 兼容读取器允许识别旧 `at_beat_id` 字段，但不可创建新 Beat（计划 §8.2 末条）。

## 验证与留白

- 扫描方式：grep 全仓（src/tests/frontend）+ 逐文件阅读上下文分类；基线 commit `aea2ed1`。
- 留白 1：三类新表示的领域类型（dataclass/wire）未落库，V03-3.1 定义并补往返序列化测试（对齐 V03-0.2 的 identities 手法）。
- 留白 2：`story.plot_beat`（mystery 侧节拍）与 `narrative.beat` 的合并转换规则（若两类存量并存）待 V03-3.1 用真实样本核对后定。

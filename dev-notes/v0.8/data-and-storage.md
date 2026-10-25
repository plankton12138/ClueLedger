# 0.8 数据与存储设计

2026-10-25：作品应用状态新增 `overview/`，保存带精确来源的模块派生对象、正文关联、信息不足尝试和无需更新回执。正文事件继续使用 `story-index.json`。派生 schema 1、recipe 2；作者模块与装配格式不变。详见 [O6 记录](notes/O6-story-overview.md)。

日期：2026-10-24。状态：待实施。产品行为见[产品方案](../../product-design/v0.8/README.md)，
调用方契约见[接口与前端](api-and-frontend.md)。下文结构是目标结构，不是现有文件格式。

## 1. 信息归属

| 信息 | 权威来源 | 修改与版本 | 是否可重建 |
|---|---|---|---|
| 正文 | 作品 `正文/*.md` | 现有文件指纹与作品 Git | 否 |
| 人物、世界设定、真相、伏笔计划、作者事件 | 全局模块库的不可变版本 | 库服务发布新修订；作品固定引用 | 否 |
| 本作品装入的模块及关联 | 作品 `.clueledger/assembly.json` | CAS 与作品 Git；无隐藏状态 | 否 |
| 灵感原文、修订与使用定位 | 作品 `灵感本/<id>.json` | 独立条目修订；本轮不改成全局灵感库 | 否 |
| 库条目索引 | 全局库缓存，键为模块精确版本 | 固定字段程序提取，摘要显式生成 | 是 |
| 作品装配索引 | 作品状态目录 | 由 assembly 及模块版本确定性解析 | 是 |
| 章节摘要、正文事件及覆盖 | 作品状态目录 `story-index.json` | 显式整理，正文指纹与规则版本 | 是 |
| 人物状态、披露、线索落实、专名发现 | 作品状态目录的观察域 | 按作品＋来源贡献保存，关联完整模块身份 | 是 |

正文观察不写回共享模块。作品 A 的人物死亡、伏笔回收、读者知情不改变作品 B。
作者计划不是缓存；修改正文只能影响相关证据，不得删除计划或升级全局模块版本。
“可重建”不表示打开页面会调用模型；没有摘要仍可读原文并写作。

## 2. 存储与路径边界

全局库、不可变版本和作品绑定的详细结构见[全局模块库](library-and-assembly.md)。
正文保持文本，模块条目仍使用 Markdown＋受控头部，但文件不再强制存在于每部作品的设定集。
新作品不创建空的《真相.md》《线索.md》，也不强制装入人物或时间线。

```text
全局库/modules/<module_id>/revisions/<revision_id>/entries/<entry_id>.md
作品/正文/*.md
作品/.clueledger/assembly.json
作品/灵感本/<id>.json
应用缓存/library-catalog.json
应用缓存/workspaces/<id>/story-index.json
```

作品 `files.py` 继续约束本地受管文件；全局内容由库服务解析 ID，不接受任意绝对路径。
普通 write_file/edit_file 不获得跨作品根写权限，也不能修改已发布模块版本。库服务、记录 HTTP 和
agent 模块工具共用发布与校验逻辑，不各自维护一套写入方式。
作品 Git 管理正文、assembly 与仍在兼容期的旧设定；不管理全局模块原文或灵感修订。
灵感目录的独立恢复、备份、损坏保护沿用下节；普通 agent 文件目录和搜索不得包含未授权灵感。
仅复制作品文件夹会缺少全局依赖；作品包导出导入已取消，因此不能承诺“目录即全部资料”，
缺少的精确模块版本由依赖诊断显式列出。

## 3. 预设模块与条目

类型与实例分离，允许多份世界、人物、故事线；同作品可装同类型的不同实例。
配置只保存确定版本和引用，没有可见性／隐藏开关。卸下退出后续默认检索；全局原文和其他作品不变。

```python
from dataclasses import dataclass
from typing import Literal

ModuleKind = Literal["characters", "world", "clues", "truth", "timeline", "plot", "reference"]

@dataclass(frozen=True)
class ModuleSpec:
    kind: ModuleKind
    label: str
    source_kinds: tuple[str, ...]
    organize_domain: str | None

PRESETS = (
    ModuleSpec("characters", "人物", ("character",), "characters"),
    ModuleSpec("world", "世界设定", ("world_rule", "place", "organization"), None),
    ModuleSpec("clues", "线索与伏笔", ("clue_plan",), "clues"),
    ModuleSpec("truth", "真相与事件经过", ("truth", "author_event"), None),
    ModuleSpec("timeline", "时间线", ("author_event",), "story_index"),
    ModuleSpec("plot", "剧情安排", ("plot_plan", "author_event"), None),
    ModuleSpec("reference", "自由资料", ("reference",), "glossary"),
)
```

每个实例拥有 module_id、kind、多条内容和不可变修订；同 kind 不共用一个文件。
`organize_domain` 表示在具体作品内可使用的整理动作，结果属于作品，不发布到库；characters
提交时仍明确选择身份或状态一个域，不隐含两次模型任务。

条目示例（位于某模块的确定版本中）：

```yaml
---
id: clue_watch
schema_version: 1
kind: clue_plan
title: 停在十点十七分的怀表
aliases: [怀表]
planned_plant_chapter: 3
planned_resolve_chapter: 22
decision: active
---
## 读者可见
怀表停在十点十七分。
## 作者安排
真正用途是误导死亡时间，第22章解释人为拨动。
```

头部只存可筛选字段，长说明保留自由文本。实际埋设／回收从本作品有效观察计算，不存在共享的 resolved 字段。
未来计划允许超过本作品末章；章号表示该版本的叙述安排，不是故事日期。作品需要不同安排时另存变体或
发布新版本并只更新本作品绑定，不改其他作品。复杂绑定参数覆盖系统暂不进入0.8。

条目 ID 在模块内稳定；完整身份含 library_id、module_id、revision_id、entry_id。
同名人物来自不同模块时不合并，条目重命名保留 ID；重复 ID、未知字段按诊断与写入校验处理。
跨模块引用按当前作品绑定解析，依赖缺失只提示，不能去全局库自动读取最新版。

## 4. 灵感的原子修订与恢复

一条灵感一个 JSON 文件，包含 head、修订数组与使用定位；一次操作在同一文件内原子替换，
避免“正文已保存、历史还没保存”两份文件分裂。第一版不拆成额外数据库或 revision 文件树。

```typescript
type IdeaRevision = {
  id: string;
  parentId: string | null;
  createdAt: string; // UTC 持久化，界面按用户时区显示
  operation: "create" | "edit" | "delete" | "restore";
  restoredFrom?: string;
  title: string;
  body: string;
  tags: string[];
  deleted: boolean;
};
type IdeaRecord = {
  schemaVersion: 1;
  id: string;
  headRevisionId: string;
  revisions: IdeaRevision[];
  origin?: { role: "user" | "assistant"; conversationId: string; messageSeq: number };
  uses: IdeaUse[];
};
type IdeaUse = {
  id: string;
  ideaRevisionId: string;
  kind: "author_link" | "applied";
  targetRecordId?: string;
  targetPath?: string;
  targetDigest?: string;
  quote?: string;
  start?: number;
  end?: number;
  turnId?: string;
};
```

每次修改需 `expected_digest` 和 `expected_revision`，服务端在条目锁内复核当前文件。
请求键保证网络重试不产生重复修订；同键不同请求体拒绝。恢复历史是追加新修订，不移动指针删除后续历史。
删除产生 tombstone 修订；删除状态下读取历史允许，普通搜索排除，恢复保持 ID。
内容允许空标题，body 必填，初始上限 20,000 字符；超限直接提示拆分，不截断保存。
历史不自动清理；大文件只分页返回修订元信息和按 ID 取正文，不能一次向浏览器或模型返回所有版本。

```python
# 契约示意：storage 的读、CAS 和原子替换必须在同一个条目锁内完成。
def restore_idea(store, idea_id, target_revision, expected_digest, request_key):
    with store.lock(idea_id):
        replay = store.idempotent_result(idea_id, request_key)
        if replay is not None:
            return replay
        current = store.read_checked(idea_id, expected_digest)
        old = current.require_revision(target_revision)
        new = current.append_revision(
            operation="restore", title=old.title, body=old.body,
            tags=old.tags, deleted=old.deleted, restored_from=old.id,
        )
        return store.atomic_replace(new, request_key=request_key)
```

实现中同键复用还须核对规范化请求体指纹。主文件不可解析时返回可修复错误，保留原字节；不得写入空历史。
用户写灵感不取得作品写锁，也不触发 Git；只锁该条灵感。正在写作时仍可记录其他想法。
若本轮已选中的灵感发生变化，任务继续使用受理时选定修订，UI 提示有新版本；不偷换材料。
与正式文件参考不同，灵感修订是不可变快照，删除当前条目不会抹掉已受理的那次输入。

`applied` 关系需要本轮确有受管目标写入，并核对目标指纹、区间和引用；服务端只证明定位存在，
不证明文学语义上完整落实。没有可定位证据时只记录“本轮曾作为参考”，不生成已落实关系。
检查失败不取消已落盘的正文，使用关系单独记录检查状态。目标更改后链接标为待核对，历史定位仍保留。

## 5. 共用章节索引和事件记录

作者事件位于已装全局模块的确定版本；正文观察位于本作品 `story-index.json`。时间线是两者的联合视图，
不能再写第三份独立、可变的时间线事件正文。章节摘要和正文事件在同一次目标发布中保存。
多个时间线／故事线实例的事件可以按绑定筛选；正文事件只存一次，按其引用显示，不复制到每个模块。

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Evidence:
    path: str
    digest: str
    start: int
    end: int
    quote: str

@dataclass(frozen=True)
class StoryTime:
    label: str                    # 原文时间描述；未知可为空
    normalized: str | None        # 仅显式可解析日期，保存原始 label
    before_ids: tuple[str, ...] = ()
    after_ids: tuple[str, ...] = ()

RecordIdentity = tuple[str, str, str, str]  # 库、模块、修订、条目

@dataclass(frozen=True)
class EventObservation:
    id: str
    text: str
    chapter: int
    mode: Literal["narration", "testimony", "recollection", "hypothesis", "dream", "unknown"]
    speaker_id: str | None
    story_time: StoryTime
    evidence: tuple[Evidence, ...]
    related_entity_refs: tuple[RecordIdentity, ...]
    author_event_ref: RecordIdentity | None  # 关联不等于把证词认定为真相

@dataclass(frozen=True)
class ChapterProjection:
    path: str
    digest: str
    recipe_version: int
    summary: str
    events: tuple[EventObservation, ...]
    outcome: Literal["found", "empty", "insufficient"]
```

作者事件另有 `kind=author_event`、`basis=truth|plan`、故事时间和计划呈现章节；不得通过整理工具发布库版本。
相关人物引用也必须使用完整模块身份，禁止把不同作品的同名人物状态串在一起。
计划的截止章过滤与正文观察不同：未来计划可以返回，未来正文证据不能作为当前已发生／已披露状态。
人物知情仅在有明确作者安排或正文依据时表达，不从人物出现位置自动推断完整认知状态。

### 5.1 原子发布与多来源

初版一个 `story-index.json` envelope 保存章节摘要、事件观察、覆盖和最后请求键。
每章发布在作品状态锁内重读最新 envelope，仅替换该章贡献后原子替换整个文件，避免覆盖并行结果。
发布前复核任务范围、完整读取区间、当前源指纹、证据与时间关系引用；任何一项失败，该章不发布。
一批已发布的其他章不回滚；正常结束仍有目标未发布则部分完成。

正文事件的每个观察属于来源章节。重建第八章不能删除第三章仍然存在的证据；多个观察可引用同一作者事件。
不同句子、不同章节的语义合并不能仅靠同名或文本散列。可确定重复提交复用 ID；同版本重发保证幂等。
改源后能明确指认旧观察且重新通过证据校验才沿用 ID；无法判定时创建新观察，旧关联显示待核对，不猜测承接。
作者长期关系优先指向模块内稳定条目；作品记录保存完整版本引用。升级绑定只标记需要重核对的关联，
不静默把旧观察改指新版本；面向派生观察的引用仍必须携带本作品证据版本。

线索与专名域同步升级为“条目＋按来源分组的贡献”；空结果替换本目标的旧贡献，不清空其他来源，
也不改作者计划。作者指令废弃伏笔改变计划；正文未再次提及不能自动推导为废弃。

### 5.2 两种排序

按章节：章序 → 原文区间。每章可展开摘要、事件和依据。
按故事时间：明确日期和已验证的 before/after 关系形成偏序；只对可比较项排序。
不连通或未知项独立分组，标明“先后未确定”；稳定 UI 顺序不能伪装成已知故事顺序。
环和矛盾日期显示双方来源，禁止生成看似有效的全序；排序失败不阻断按章节查看。
“回忆”不自动等于虚假，也不自动等于客观真相，保留表达方式及作者设定对照。

## 6. 基础目录、摘要和失效

采用三层索引：库索引按不可变模块版本复用；装配索引按作品 assembly digest 解析；正文索引按本作品章节生成。
库索引只包含固定字段、路径在库内的定位及来源，不含任何作品的生死／回收状态。短条目不强制语义摘要。
语义摘要带精确模块 revision 或正文 digest，显式生成；未生成、过期或版本不可用时回到原文，不自动启动模型。

库 head 更新不会使使用旧 revision 的作品失效；显式升级绑定才重新解析相关引用。
依赖某人物版本的核对记录随该依赖变化标待核对；仅依赖正文的观察不因无关模块版本变化而失效。
卸下排除该模块的默认候选，保留正文事实；重新装入相同版本可复用仍有效的索引。
文件指纹异常不能在原 revision 下重建出“新真源”，须恢复文件或显式导入为新版本。

## 7. 既有作品与格式升级

打开0.7作品只读兼容，不移动原设定、不创建全局模块、不运行模型。
显式“导入模块库”按现有资料分类创建实例、发布不可变版本并绑定本作品；保留旧文件与来源映射，
只有绑定成功后切换读取路径，不能旧源和新源双重注入。重复导入按源指纹和请求键幂等处理。
新作品只建正文和索引状态，不继承旧版强制的两份设定文件。

旧线索 due/dropped 来源不明时保留“旧记录，安排来源待确认”，不能凭缓存创建权威作者决定。
旧章节摘要和时间锚点作为 legacy 来源可读，不补造事件时间；明确发布新投影后按章切换，失败不提前切换。
库路径失联或指定版本缺失时显示依赖缺失，正文仍可读；不能用最新版或同名模块替代。

作品包导入导出已取消；仅复制作品目录时，缺少的精确模块依赖由装配诊断提示，不创建作品内第二套可编辑原文。
详细发布恢复、变体和兼容流程见[全局模块库契约](library-and-assembly.md)。

# 故事总览：分模块实现示例

2026-10-26 状态变更：整个故事总览模块已撤除，本文全部实现示例仅作历史参考；当前范围见[下线记录](notes/story-overview-retirement.md)。

日期：2026-10-25。状态：**设计示意；功能已实施，示例不作为独立生产程序编译**。
当前实现与验证见 [O6 实施记录](notes/O6-story-overview.md)。下文保留职责拆分的代码示意，
不能直接复制替代源码。生产实现使用逐目标只读工具、来源区间账本与服务端发布校验；
并把同一句中不同人物的来源锚点分开。真实字段以 `overview_models.py`、`overview_api.py` 和前端客户端为准。

## 1. 确定来源与模块内部对象

解决的问题：一个自由正文条目可能包含多个人物，整条 `RecordRef` 不足以标识人物；名字也不是跨模块身份。
来源和派生对象分别建模，作者无需新增字段。标识只承诺在确定来源版本内可复现。

```python
import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Literal


@dataclass(frozen=True, slots=True)
class ModuleSource:
    binding_id: str
    library_id: str
    module_id: str
    revision_id: str
    entry_id: str
    digest: str  # 原始记录 Markdown 的指纹


@dataclass(frozen=True, slots=True)
class ModuleEvidence:
    source: ModuleSource
    start: int  # Unicode 码点，含头部的原始记录文本
    end: int    # 半开区间 [start, end)
    quote: str


@dataclass(frozen=True, slots=True)
class FacetRef:
    source: ModuleSource
    facet_id: str


def make_facet_ref(
    kind: Literal["character", "clue"], evidence: ModuleEvidence,
) -> FacetRef:
    source = evidence.source
    identity = [
        source.binding_id, source.library_id, source.module_id,
        source.revision_id, source.entry_id, source.digest,
        kind, evidence.start, evidence.end,
    ]
    raw = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return FacetRef(source, "facet_" + sha256(raw.encode("utf-8")).hexdigest()[:24])
```

主依据必须指向明确的对象提及，发布前先校验；不能由模型随意提供一个碰巧存在的区间。
同源重复识别先按主依据与种类归并。若无法区分同名对象，保留未解析提及，不按名字自动合并。
模块版本、区间或规则变化后需重新解析，不宣称新旧对象具有相同身份。

## 2. 模块能力由有效结果决定

解决的问题：新模块统一自由书写，不能再用旧 `kind` 决定是否出现人物或线索视图。
清单只返回轻量能力，原文按选择读取；一个模块可拥有多个能力。

```python
from dataclasses import dataclass
from typing import Literal

Capability = Literal["content", "characters", "clues"]


@dataclass(frozen=True, slots=True)
class ModuleProjection:
    source: ModuleSource
    recipe_version: int
    outcome: Literal["found", "empty", "insufficient"]
    character_ids: tuple[str, ...]
    clue_ids: tuple[str, ...]


def capabilities(
    source: ModuleSource,
    projection: ModuleProjection | None,
    *,
    recipe_version: int,
) -> tuple[Capability, ...]:
    result: list[Capability] = ["content"]
    if projection is None:
        return tuple(result)
    if projection.source != source or projection.recipe_version != recipe_version:
        return tuple(result)
    if projection.outcome != "found":
        return tuple(result)
    if projection.character_ids:
        result.append("characters")
    if projection.clue_ids:
        result.append("clues")
    return tuple(result)
```

实际模块能力是各已校验记录投影的并集，并保留每条记录的覆盖情况；单条成功不等于整个模块已完整处理。
绑定不可读取时也保留其名称和诊断，但不能把“有 content 入口”解释为原文已经读取成功。
旧投影可从明确标注的旧结果入口阅读，不能作为当前有效图边混入关系图。

## 3. 校验原文后再发布

解决的问题：结构合法的 JSON 不一定有原文支持。发布必须核对实际来源、读取范围和引文，
空结果也要能证明读完了本次目标。以下仅展示精确引文的校验核心。

```python
from hashlib import sha256
from hastings.workspace.errors import WorkspaceError


def check_module_evidence(raw_text: str, evidence: ModuleEvidence) -> None:
    current_digest = sha256(raw_text.encode("utf-8")).hexdigest()
    if current_digest != evidence.source.digest:
        raise WorkspaceError(
            code="OVERVIEW_SOURCE_CHANGED", where="overview.publish",
            why="模块记录版本已改变", fix="重新读取当前绑定后更新总览",
        )
    if not 0 <= evidence.start < evidence.end <= len(raw_text):
        raise WorkspaceError(
            code="OVERVIEW_EVIDENCE_INVALID", where="overview.publish",
            why="依据区间超出原文", fix="按本次实际读取的区间重新提交",
        )
    # 此方案每处短依据最多 160 码点；复杂关系可提供多处依据。
    if len(evidence.quote) > 160 or raw_text[evidence.start:evidence.end] != evidence.quote:
        raise WorkspaceError(
            code="OVERVIEW_EVIDENCE_INVALID", where="overview.publish",
            why="引文与指定区间不一致", fix="提交准确原文，不先去空白或改写",
        )
```

完整发布顺序应为：

1. 验证目标来自冻结计划，绑定、完整记录引用和生成规则均匹配。
2. 通过现有库读取服务取得原始记录，核对指纹及读取账本覆盖；不能直接信任模型提交的原文。
3. 校验全部引文、对象端点、关系断言与本轮允许的引用。端点仅存在不代表关系有依据。
4. 空结果要求本目标完整覆盖；处理不完整时返回 `insufficient`/部分覆盖。
5. 在该目标的发布保护范围内重新核对来源，原子替换包含结果、覆盖与来源清单的投影。

外部编辑仍可能在发布后发生，因此读取端也必须核对来源有效性。
正文关联贡献采用同样的核对过程，并从已发布章投影取得原始指纹，不能使用“当前文件指纹＋旧引文”。

## 4. 冻结增量计划与部分完成

解决的问题：旧范围要求至少一个章节，新任务要支持纯模块；一次更新又可能只完成部分目标。
目标种类使用判别结构，不把模块身份塞进旧 `entity_paths`。

```python
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class ModuleTarget:
    id: str
    source: ModuleSource
    kind: Literal["module"] = "module"


@dataclass(frozen=True, slots=True)
class ChapterTarget:
    id: str
    path: str
    digest: str
    kind: Literal["chapter"] = "chapter"


@dataclass(frozen=True, slots=True)
class LinkTarget:
    id: str
    chapter_path: str
    chapter_digest: str
    module_generation: str
    depends_on: tuple[str, ...]
    kind: Literal["links"] = "links"


@dataclass(frozen=True, slots=True)
class OverviewPlan:
    generation: str
    assembly_digest: str | None
    as_of_chapter: int
    recipe_version: int
    targets: tuple[ModuleTarget | ChapterTarget | LinkTarget, ...]
```

服务端在既有受理锁内比较页面代数并冻结全部输入，客户端不能直接提交这些“已冻结”对象。
模块目标可以存在而章节目标为空；全部目标为空时直接返回无需更新。
关联目标依赖具体模块投影代数；模块变化可以重新建立关联，而不重新生成未改变的事件正文。

执行分批复用现有轮次、模型快照、活动事件和停止信号，处理模式为：

```python
async def execute_overview(plan, runner, store, cancellation):
    # runner/store 是拟定的适配接口：其内部接现有 harness、账本和原子发布。
    results = {}
    for batch in runner.ordered_batches(plan):
        cancellation.raise_if_cancelled()
        if not runner.dependencies_ready(batch, results):
            results.update(store.mark_blocked(batch, "依赖尚未完成"))
            continue
        runner.verify_sources(plan, batch)
        # 所有实际模型调用必须使用受理时固定的在线模型事实与只读整理能力。
        output = await runner.read_and_extract(batch, cancellation)
        results.update(store.publish_verified(plan, batch, output))
        runner.record_progress(results)
    return store.summarize(plan, results)
```

网络/模型失败和取消沿用既有终态处理，不在此处循环重试；已经发布的目标保留。
`summarize` 必须核对计划里的每个目标；不存在的提交不计作成功，`insufficient` 和被依赖阻塞的目标计入未完成。
达到预算或正常结束但目标未齐时显示部分更新。继续更新是作者的新命令，原请求键只能恢复原轮次。

## 5. 用途与接口必须同时接线

解决的问题：当前 `_tool_mode` 对未登记用途默认返回写作模式，新增字符串而遗漏工具约束会扩大权限。
必须同步改用途枚举、模式返回类型、受理、工具集合和发布服务，权限契约先验证。

```python
# 拟加入 service._tool_mode 的显式分支，同时扩充其返回 Literal。
if purpose == "organize_overview":
    return "overview"

# 拟加入 agent 工具构建的分支；发布工具只能写本轮派生目标。
if mode == "overview":
    allowed_readonly = {
        "list_files", "read_file", "search_text",
        "list_modules", "query_records", "read_record",
    }
    return [
        *[tool for tool in tools if tool.name in allowed_readonly],
        publish_overview_result_tool(frozen_plan, ledger),
    ]
```

只读工具本身仍须核对冻结范围，不能因为工具名只读就允许读取未挂载的库内容。
新用途不向模型提供写文件、编辑模块、装卸、删除或发布库版本的工具。

拟定请求与响应对应：

```ts
type StartOverviewUpdate = {
  request_key: string;
  observed_generation: string;
  as_of_chapter: number; // 纯模块时允许 0
};

type OverviewUpdateResponse =
  | { state: "noop"; generation: string }
  | {
      state: "accepted";
      turn_id: string;
      conversation_id: string;
      generation: string;
      target_count: number;
    };

type OverviewUpdateProgress = {
  turn_id: string;
  run_status: "running" | "completed" | "failed" | "stopped" | "interrupted";
  outcome: "pending" | "complete" | "partial";
  completed: number;
  total: number;
  remaining_target_ids: string[];
};
```

POST 在 200 与 202 下返回对应分支；任务详情仍区分轮次状态和目标完成度。
前端冻结此提交体直至收到明确结果，丢失响应后不能用更新后的页面代数替换原提交体重试。
旧单域任务的请求与响应继续保留，不给旧客户端返回它无法识别的新域。

清单 DTO 与后台使用同名字段；展示模型的转换只在一处完成：

```ts
type PageDto<T> = { items: T[]; next_cursor: string | null };
type BindingSummaryDto = {
  binding_id: string;
  display_name: string;
  module: { library_id: string; module_id: string; revision_id: string };
  capabilities: ("content" | "characters" | "clues")[];
  availability: "available" | "unavailable";
};
type OverviewManifestDto = {
  generation: string;
  assembly_digest: string | null;
  as_of_chapter: number;
  bindings: PageDto<BindingSummaryDto>;
  chapters: PageDto<{ path: string; chapter: number; title: string }>;
  update_summary: { changed_targets: number; incomplete_targets: number };
};

function bindingPresentation(dto: BindingSummaryDto): BindingManifest {
  return {
    id: dto.binding_id,
    name: dto.display_name,
    revisionId: dto.module.revision_id,
    capabilities: dto.capabilities,
    availability: dto.availability,
  };
}
```

`BindingManifest` 是下节页面示例采用的展示类型。模块与章节页分别续页，清单代数变化就停止拼接；
卡片/对象读取继续携带完整来源，不能仅凭此展示模型中的版本号作为引用凭据。

## 6. 前端缓存与选中状态

解决的问题：模块保存、切换作品或更新任务完成时，旧响应可能晚到。缓存键应含确切来源与查询，
可见内容还要受当前请求代数保护。根页面统一管理选择，人物卡和图不各存一份选中项。

```ts
type OverviewSelection =
  | { kind: "none" }
  | { kind: "event"; eventId: string; sourceDigest: string }
  | {
      kind: "facet";
      bindingId: string;
      revisionId: string;
      entryId: string;
      facetId: string;
    };

type OverviewQuery = {
  workspaceId: string;
  generation: string;
  chapter: number;
  order: "chapter" | "story";
  bindingId?: string;
  revisionId?: string;
  view: "timeline" | "content" | "characters" | "clues";
  cursor?: string;
};

function overviewKey(query: OverviewQuery): string {
  return JSON.stringify([
    query.workspaceId, query.generation, query.chapter, query.order,
    query.bindingId ?? null, query.revisionId ?? null,
    query.view, query.cursor ?? null,
  ]);
}

function canShowResponse(
  requested: OverviewQuery, current: OverviewQuery,
  returnedGeneration: string,
): boolean {
  return returnedGeneration === requested.generation
    && overviewKey(requested) === overviewKey(current);
}
```

实际状态层另加请求序号与 `AbortController`，同一键的后发请求不能被旧响应覆盖。
后台响应可以写入它自己的缓存键，但不能把当前视图切回去。来源失效先刷新清单，
清单尚未返回时保留加载状态，不把“未加载到绑定”当作“模块已卸下”。

对象打开时先建立其完整引用和详情状态；不能因为对象摘要已经缓存就跳过详情所需的初始化。
新提取对象、首次打开、收起后重开、原处来源更新与立即定位都应有回归，延续此前灵感即时编辑问题的检查经验。

## 7. 混合模块的局部视图

解决的问题：模块入口来自实际装配，而不是固定的“人物/线索/名词”导航。
同一模块可以提供多个视图，尚无派生结果时直接读原文。

```tsx
type ModuleView = "content" | "characters" | "clues";

type BindingManifest = {
  id: string;
  name: string;
  revisionId: string;
  capabilities: ModuleView[];
  availability: "available" | "unavailable";
};

function ModuleOverview({ binding, requestedView, onViewChange }: {
  binding: BindingManifest;
  requestedView: ModuleView;
  onViewChange: (view: ModuleView) => void;
}) {
  if (binding.availability === "unavailable") {
    return <BindingUnavailable bindingId={binding.id} />;
  }
  const view = binding.capabilities.includes(requestedView)
    ? requestedView : "content";
  return <section aria-label={binding.name}>
    {binding.capabilities.length > 1 && <ModuleViewPicker
      views={binding.capabilities} value={view} onChange={onViewChange}
    />}
    {view === "characters" ? <CharacterModule binding={binding} />
      : view === "clues" ? <ClueModule binding={binding} />
      : <BoundModuleContent binding={binding} />}
  </section>;
}
```

示例组件均为拟定适配组件。`BoundModuleContent` 接现有模块内容读取，不复制一套可编辑文本。
默认视图优先选择已存在的专门能力；混合模块通过局部切换访问全部能力。
入口显示名使用作品绑定名，模块版本升级不能只改 `revisionId` 后沿用旧对象数据。

## 8. 图与列表读取同一份数据

解决的问题：图看起来完整但列表、原文或截止章不一致，会让作者误判关联。
关系与线索进展先形成统一的可访问数据，再分别呈现。

```tsx
function CharacterRelations({ data, selectedId, onSelect }: {
  data: RelationPage;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  return <section aria-label="人物关系">
    <RelationGraph data={data} selectedId={selectedId} onSelect={onSelect} />
    <RelationList data={data} selectedId={selectedId} onSelect={onSelect} />
    {data.nextCursor && <LoadRelations cursor={data.nextCursor} />}
  </section>;
}

function ClueThread({ clue, chapter, onOpenEvent, onOpenSource }: {
  clue: ClueDetail;
  chapter: number;
  onOpenEvent: (eventId: string) => void;
  onOpenSource: (source: EvidenceRef) => void;
}) {
  // 服务端已按截止章裁剪；此处再次约束展示，不承担服务端访问范围校验。
  const written = clue.observations.filter(item => item.chapter <= chapter);
  return <section aria-label={clue.name}>
    <h3>{clue.name}</h3>
    <ol>{written.map(item => <li key={item.id}>
      <button onClick={() => onOpenEvent(item.eventId)}>{item.text}</button>
      <button onClick={() => onOpenSource(item.evidence)}>查看原文</button>
    </li>)}</ol>
    {clue.declaredPlan && <DeclaredSourceCard value={clue.declaredPlan} />}
  </section>;
}
```

图默认读取一层关系，页内端点与边一致；未读关系有明确的继续加载入口。
图、列表及已写进展都保留来源性质：作者设定、正文叙述、人物证词等不能因为画成同一种线而混同。
否定、假设或人物猜测不能被简化成肯定关系；关系变化按有依据的正文观察保留时点。
原文定位先核对版本；来源已变时展示旧引文与需要更新的说明，不在新正文中盲目高亮旧偏移。

## 9. 验证入口（实施前建议）

先按计划的 SO01–SO18 编写相应后端与浏览器用例，再核对真实在线语义。建议新增位置：

- `tests/workspace/test_story_overview.py`、`test_module_facets.py`、`test_overview_updates.py`、`test_overview_api.py`。
- 前端总览状态、路由和选择单元测试；`frontend/e2e/story-overview.spec.ts` 复用隔离作品和真实服务。
- `eval/cases/story_overview.yaml`、`eval/suites/story-overview.json`，并给评测执行器增加实际总览用途步骤。

以下保留设计时建议的验证入口。实际测试集中在 `tests/workspace/test_story_overview.py`，本轮命令、结果与在线复核见实施记录：

```powershell
uv run pytest tests/workspace/test_story_overview.py tests/workspace/test_module_facets.py tests/workspace/test_overview_updates.py tests/workspace/test_overview_api.py -q
uv run python -m eval run --suite story-overview --env-file .env

# 在 frontend 目录，使用独立端口、状态目录和截图目录运行：
npm test
npm run build
npx playwright test --project=workspace story-overview.spec.ts
```

在线运行必须使用现有 `.env` 的在线模型，原文证据与生产工具校验不可用模拟发布替代。
先确认无需更新和单纯浏览的调用数为 0，再记录真实语义整理的调用规模与人工复核结论。

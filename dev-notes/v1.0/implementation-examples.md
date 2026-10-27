# 1.0 分模块实现示例

日期：2026-10-27。状态：**评审用示例，未接入业务**。
对应职责见[开发计划](development-plan.md)，完整数据和接口以[数据契约](data-and-recovery.md)、[接口契约](api-and-frontend.md)为准。
以下重点展示容易出错的边界；HTTP装配、完整记录校验、跨进程锁工厂和实际模型调用在实施阶段接入。
Python段落按本页顺序共享类型；前端段落分别为独立模块。示例检查不代替生产验收。

## 1. 领域：观察、来源与引用

身份不能随名称变化，人物表述不能直接变成作者真相。位置使用服务端全文码点，保留证据章号。

```python
# example: clue_models.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class Anchor:
    evidence_id: str
    path: str
    digest: str
    chapter: int
    start: int
    end: int
    quote: str

@dataclass(frozen=True)
class Observation:
    observation_id: str
    clue_id: str
    kind: Literal["plant", "mention", "advance", "resolve"]
    mode: Literal["narration", "testimony", "recollection", "hypothesis", "dream", "unknown"]
    text: str
    anchor: Anchor

@dataclass(frozen=True)
class ClueReference:
    workspace_lineage: str
    clue_id: str
    clue_revision: str
    browse_as_of_chapter: int
    inference_id: str | None = None
    inference_revision: str | None = None
    report_id: str | None = None
```

## 2. 旧记录身份迁移

仅迁移旧版本已按文字归为同组的记录。新建线索绝不调用这个按文字归组的兼容函数。
本函数只构建候选；完整迁移还需复制并核对覆盖、保留备份，最终与本次操作一次提交。

```python
# example: clue_migration.py
from copy import deepcopy
from hashlib import sha1
from collections.abc import Callable, Mapping, Sequence

def migrate_legacy_identity(
    rows: Sequence[Mapping[str, object]],
    create_id: Callable[[], str],
) -> tuple[dict[str, dict[str, object]], list[dict[str, object]], dict[str, str]]:
    clues: dict[str, dict[str, object]] = {}
    contributions: list[dict[str, object]] = []
    aliases: dict[str, str] = {}
    by_old_key: dict[str, str] = {}
    for raw in rows:
        key = str(raw.get("clue", "")).strip()
        if not key:
            raise ValueError("旧线索缺少描述，迁移不能跳过后覆盖原文件")
        if key not in by_old_key:
            clue_id = create_id()
            if clue_id in clues:
                raise ValueError("线索身份生成器返回重复标识")
            by_old_key[key] = clue_id
            clues[clue_id] = {"clue_id": clue_id, "title": key, "identity": "legacy_unverified"}
            aliases[sha1(("clues\x1f" + key).encode("utf-8")).hexdigest()[:16]] = clue_id
        contribution = deepcopy(dict(raw))
        contribution["clue_id"] = by_old_key[key]
        contributions.append(contribution)
    return clues, contributions, aliases
```

外层用标准UUID生成 clue_id，校验原记录证据与版本；ValueError 转为统一迁移错误。
不将无法解释的旧条目静默丢弃，不因格式迁移调用模型补真相。

## 3. 来源定位：指纹、换行与区间

evidence_id 必须先从本作品已登记记录解析出 Anchor；本函数不接受客户端给的任意路径读取文件。
过期引文允许阅读，但不返回可供高亮的旧坐标。

```python
# example: source_locations.py
from hashlib import sha256
from hastings.workspace.errors import WorkspaceError

@dataclass(frozen=True)
class Location:
    freshness: Literal["valid", "stale"]
    quote: str
    start: int | None
    end: int | None

def normalized_text(raw: bytes) -> str:
    return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")

def resolve_anchor(raw: bytes, anchor: Anchor) -> Location:
    if sha256(raw).hexdigest() != anchor.digest:
        return Location("stale", anchor.quote, None, None)
    text = normalized_text(raw)
    if not (0 <= anchor.start < anchor.end <= len(text)) or text[anchor.start:anchor.end] != anchor.quote:
        raise WorkspaceError(
            code="CLUE_SCOPE_INVALID", where="线索依据",
            why="引文与原文区间不符", fix="重新读取实际原文并核对引用",
        )
    return Location("valid", anchor.quote, anchor.start, anchor.end)
```

模块来源用精确四段引用解析实际文本后做相同区间核对，另检查当前绑定状态。
不把旧修订仍能读取当成当前计划关联仍有效。

## 4. 存储：查重、版本与原子发布

本例展示已存在的新账本的关键写事务。`locked` 必须由应用提供跨进程互斥；
`validate` 必须校验完整账本和关系，不能以简单“JSON可解析”代替。初始化和迁移走同一锁定服务。

```python
# example: clue_store.py
import json
import os
import tempfile
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

class ClueFileStore:
    def __init__(self, path: Path, locked: Callable[[], ContextManager[None]], validate: Callable[[dict[str, Any]], None]):
        self.path, self.locked, self.validate = path, locked, validate

    def mutate(self, *, expected_revision: str, request_key: str, kind: str,
               payload: dict[str, Any], update: Callable[[dict[str, Any]], dict[str, Any]]) -> dict[str, Any]:
        identity = json.dumps({"kind": kind, "payload": payload, "expected_revision": expected_revision}, sort_keys=True, ensure_ascii=False, allow_nan=False)
        fingerprint = sha256(identity.encode("utf-8")).hexdigest()
        with self.locked():
            try:
                current = json.loads(self.path.read_text(encoding="utf-8"))
                self.validate(current)
            except (OSError, ValueError, TypeError) as exc:
                raise WorkspaceError(code="CLUE_LEDGER_READ_FAILED", where="线索记录",
                                     why="现有记录无法读取或校验", fix="保留原文件并检查备份后重试") from exc
            previous = current["requests"].get(request_key)
            if previous is not None:
                if previous["fingerprint"] != fingerprint:
                    raise WorkspaceError(code="CLUE_REVISION_CONFLICT", where="线索操作",
                                         why="同一请求键对应不同内容", fix="重新读取记录并为新操作创建请求键")
                return deepcopy(previous["result"])
            if current["ledger_revision"] != expected_revision:
                raise WorkspaceError(code="CLUE_REVISION_CONFLICT", where="线索记录",
                                     why="记录版本已变化", fix="重新读取后继续，保留当前草稿")
            candidate = deepcopy(current)
            result = update(candidate)
            candidate["ledger_revision"] = "rev_" + uuid4().hex
            result = {**result, "ledger_revision": candidate["ledger_revision"]}
            candidate["requests"][request_key] = {"fingerprint": fingerprint, "result": result}
            self.validate(candidate)
            handle, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".clues-", suffix=".tmp")
            try:
                with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                    json.dump(candidate, stream, ensure_ascii=False, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            except OSError as exc:
                raise WorkspaceError(code="CLUE_LEDGER_WRITE_FAILED", where="线索记录",
                                     why="本次记录未发布", fix="检查目录权限与磁盘后使用原请求重试") from exc
            finally:
                Path(temporary).unlink(missing_ok=True)
            return deepcopy(result)
```

实际记录校验应以专门模型实现，未知格式报明确的 schema 错误；迁移不能在读失败分支初始化空文件。
request_key、目标和版本由业务服务登记；HTTP与模型工具共用 mutate，不各写一份发布逻辑。
新贡献与 coverage 在同一个 update 内修改，从而一次提交。

## 5. 视图：按实际提及与截止章投影

按每条观察过滤后再计算回收状态，避免后章回收信息出现在前章。
本章列表用实际来源路径筛选，能够找到早章埋设、当前章推进的线索。

```python
# example: clue_views.py
def visible_observations(
    observations: Sequence[Observation], *, as_of_chapter: int,
    source_is_current: Callable[[Anchor], bool],
) -> tuple[Observation, ...]:
    return tuple(sorted(
        (item for item in observations
         if item.anchor.chapter <= as_of_chapter and source_is_current(item.anchor)),
        key=lambda item: (item.anchor.chapter, item.anchor.start, item.observation_id),
    ))

def chapter_clue_ids(observations: Sequence[Observation], chapter_path: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(item.clue_id for item in observations if item.anchor.path == chapter_path))

def observed_status(observations: Sequence[Observation]) -> Literal["open", "resolved"]:
    return "resolved" if any(item.kind == "resolve" for item in observations) else "open"
```

完整业务还要叠加按有效章保存的废弃决定与作者计划，不能把作者计划的回收章当已回收。
失效观察另列供作者查看，不进入上面这份可用于当前推理的投影。

## 6. 核对：实际提供的原文与报告完成条件

读取记录来自现有 MaterialLedger 的真实模型输入，不从浏览器或模型自述生成。
此例验证 file 证据；计划引用应单独放作者目标区，不进入 reader_sources 来证明前提已交代。

```python
# example: clue_reviews.py
@dataclass(frozen=True)
class ReadSource:
    raw: bytes
    chapter: int
    ranges: tuple[tuple[int, int], ...]

@dataclass(frozen=True)
class Finding:
    kind: Literal["inference_gap", "alternative_open", "premise_undisclosed", "counter_evidence"]
    certainty: Literal["supported", "suspected"]
    description: str
    evidence: tuple[Anchor, ...]

def covered(ranges: Sequence[tuple[int, int]], start: int, end: int) -> bool:
    cursor = start
    for left, right in sorted(ranges):
        if right <= cursor:
            continue
        if left > cursor:
            return False
        cursor = right
        if cursor >= end:
            return True
    return cursor >= end

def validate_clue_report(
    *, status: Literal["findings", "no_findings", "incomplete"], findings: Sequence[Finding],
    targets: Mapping[str, str], as_of_chapter: int, reader_sources: Mapping[str, ReadSource],
) -> tuple[str, ...]:
    if not targets:
        raise ValueError("正文核对目标不能为空")
    uncovered: list[str] = []
    for path, expected_digest in targets.items():
        source = reader_sources.get(path)
        if (source is None or source.chapter > as_of_chapter
                or sha256(source.raw).hexdigest() != expected_digest
                or not covered(source.ranges, 0, len(normalized_text(source.raw)))):
            uncovered.append(path)
    if (status == "no_findings" and findings) or (status == "findings" and not findings):
        raise ValueError("报告状态与问题清单不符")
    if status != "incomplete" and uncovered:
        raise ValueError("尚有未完整提供的目标，不能报告完成")
    for finding in findings:
        if not finding.evidence:
            raise ValueError("每条问题需要可核对依据")
        for anchor in finding.evidence:
            source = reader_sources.get(anchor.path)
            if (source is None or source.chapter != anchor.chapter or anchor.chapter > as_of_chapter
                    or not covered(source.ranges, anchor.start, anchor.end)
                    or resolve_anchor(source.raw, anchor).freshness != "valid"):
                raise ValueError("报告引用不属于本次实际提供的有效范围")
    return tuple(uncovered)
```

外层同时核对目标／模块修订、停止信号、真实目标与参考的读取覆盖以及目录清单，
以统一错误信封返回。充分支持、合理怀疑和有意开放的语义判断由独立在线样本复核，
不能仅从以上结构校验宣称“推理正确”。

## 7. 对话快照：真实版本与请求身份

在首次受理时核对引用，重试先查询已受理的幂等记录。引用不是授权，传入越界版本时保留草稿并报冲突。

```python
# example: clue_context.py
def freeze_reference(reference: ClueReference, *, lineage: str, clue_id: str,
                     revision: str, writer_cutoff: int, observations: Sequence[Observation]) -> dict[str, object]:
    if reference.workspace_lineage != lineage or reference.clue_id != clue_id:
        raise WorkspaceError(code="CLUE_ACCESS_DENIED", where="线索引用",
                             why="引用不属于当前作品", fix="从当前作品重新选择线索")
    if reference.clue_revision != revision:
        raise WorkspaceError(code="CLUE_REFERENCE_STALE_CONFLICT", where="线索引用",
                             why="引用版本已变化", fix="保留草稿并重新读取引用")
    cutoff = min(reference.browse_as_of_chapter, writer_cutoff)
    if cutoff < 0:
        raise ValueError("截止章不能小于零")
    visible = [item for item in observations if item.anchor.chapter <= cutoff]
    return {"clue_id": clue_id, "clue_revision": revision, "reader_cutoff": cutoff,
            "reader_observation_ids": [item.observation_id for item in visible]}

def request_fingerprint(payload: Mapping[str, object]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()
```

输入 observations 必须是已经核对版本的投影。作者计划独立按精确模块读取并冻结，不放入 reader_observation_ids。
request_fingerprint 的 payload 必须包含作者消息、用途、context全部引用、核对目标和版本，不能只散列文字。
0.9摘要可以保留这些身份及待处理事项，实际证据仍需当前版本原文。

## 8. 前端：Unicode与安全高亮

真实正文 digest 由服务端按字节计算，浏览器不得对渲染文本散列后代替。
下例产生源文本索引；最终 DOM 定位仍通过 Markdown 源位置映射完成。

```typescript
// example: sourceLocation.ts
export interface SourceAnchor {
  digest: string;
  start: number;
  end: number;
  quote: string;
  freshness: "valid" | "stale" | "source_missing";
}
export function sourceRange(text: string, digest: string, anchor: SourceAnchor): [number, number] | null {
  if (anchor.freshness !== "valid" || digest !== anchor.digest) return null;
  const points = Array.from(text);
  if (!Number.isInteger(anchor.start) || !Number.isInteger(anchor.end) ||
      anchor.start < 0 || anchor.end <= anchor.start || anchor.end > points.length) return null;
  if (points.slice(anchor.start, anchor.end).join("") !== anchor.quote) return null;
  const from = points.slice(0, anchor.start).join("").length;
  return [from, from + anchor.quote.length];
}
```

## 9. 前端：阅读、浏览和任务时点

定位原文只改变阅读位置；服务响应按作品和请求序号接收，避免切作品后旧结果覆盖当前详情。

```typescript
// example: cluesState.ts
export interface PanelState {
  workspaceId: string;
  requestId: number;
  selectedPath: string | null;
  browseAsOf: number;
  taskStateChapter: number | null;
  selectedClueId: string | null;
}
export function locateSource(state: PanelState, path: string): PanelState {
  return { ...state, selectedPath: path };
}
export function acceptsResponse(state: PanelState, response: { workspaceId: string; requestId: number }): boolean {
  return state.workspaceId === response.workspaceId && state.requestId === response.requestId;
}
```

## 10. 前端：加入草稿保留已有输入

同一线索版本去重，不同版本要求明确处理；精确引用与已有参考一起保存，不自动发送。

```typescript
// example: clueDraft.ts
export interface ClueRef {
  workspace_lineage: string;
  clue_id: string;
  clue_revision: string;
  browse_as_of_chapter: number;
}
export interface Draft {
  text: string;
  reference_paths: string[];
  clue_references: ClueRef[];
}
export function addClueToDraft(draft: Draft, ref: ClueRef): Draft {
  const prior = draft.clue_references.find(item =>
    item.workspace_lineage === ref.workspace_lineage && item.clue_id === ref.clue_id);
  if (prior) {
    if (prior.clue_revision !== ref.clue_revision || prior.browse_as_of_chapter !== ref.browse_as_of_chapter) {
      throw new Error("[CLUE_REFERENCE_STALE_CONFLICT] 线索引用：草稿已有另一版本或时点。建议：查看差异后选择保留的引用。");
    }
    return draft;
  }
  if (draft.clue_references.length >= 10) throw new Error("[CLUE_SCOPE_INVALID] 线索引用：已达到本次10条上限。建议：分组讨论或移除部分引用。");
  return { ...draft, clue_references: [...draft.clue_references, { ...ref }] };
}
```

真实UI更新须使用当前store的函数式更新或草稿revision，持久化冲突时保留本地草稿。
“讨论设计”可准备一段消息建议，已有输入不被直接覆盖；作者选择插入或自行输入后再发送。

## 11. 共享详情组件

列表和写作辅助区使用同一组件与服务字段。下例省略样式，按阅读顺序呈现，不用连续状态徽标代替内容。

```tsx
// example: ClueDetail.tsx
interface Detail {
  clue_id: string;
  title: string;
  observations: { observation_id: string; mode: string; text: string; evidence_ids: string[] }[];
  reader_seen: { text: string; basis_ids: string[] }[];
  author_designs: { link_id: string; summary: string }[];
  inferences: { inference_id: string }[];
}
interface Props {
  detail: Detail;
  onLocate: (evidenceId: string) => void;
  onDiscuss: (clueId: string) => void;
  onReview: (inferenceId: string) => void;
}
export function ClueDetail({ detail, onLocate, onDiscuss, onReview }: Props) {
  return <section aria-label="线索详情">
    <h2>{detail.title}</h2>
    <h3>正文依据</h3>
    {detail.observations.map(item => <div key={item.observation_id}>
      <p>{item.text}</p>
      {item.mode === "testimony" && <p>人物表述，信息来源需结合原文判断。</p>}
      {item.evidence_ids.map(id => <button type="button" key={id} onClick={() => onLocate(id)}>查看原文</button>)}
    </div>)}
    <h3>读者已见</h3>
    {detail.reader_seen.map(item => <p key={item.basis_ids.join(":") + item.text}>{item.text}</p>)}
    {detail.author_designs.length > 0 && <details>
      <summary>作者计划</summary>
      {detail.author_designs.map(plan => <p key={plan.link_id}>{plan.summary}</p>)}
    </details>}
    <button type="button" onClick={() => onDiscuss(detail.clue_id)}>讨论设计</button>
    {detail.inferences.map(item => <button type="button" key={item.inference_id}
      onClick={() => onReview(item.inference_id)}>核对推理</button>)}
  </section>;
}
```

实际组件还需支持来源变化、无正文计划、覆盖、任务与报告、返回焦点和多目标的明确标题。
没有目标时显示构思入口；后端未接通的关联或核对动作不开放占位按钮。

## 12. 评测接线示意

下面是待扩展schema的目标表达，当前评测器尚不支持这些新action，不能直接作为可运行套件。

```yaml
id: clue_note_photo
steps:
  - action: organize_clues
    chapters: [正文/0002 遗书.md]
  - action: save_clue_inference
    select_by_fixture: letter_back
    intent: deduce
    goal: 林岚在案发前接触过遗书原件
  - action: review_clue_inference
    as_of_chapter: 2
    checks:
      - id: premise_gap
        kind: manual
        rubric: 指出照片或转述仍是可能的信息来源，不直接定罪；引用真实正文。
  - action: prepare_clue_discussion
    expected: draft_only
```

select_by_fixture 由评测器映射生产服务返回的实际ID；rubric留在评测器，不进入作者消息或模型证据。
实施时注册schema、runtime、断言、成本和报告，保持旧案例缺省行为不变。

## 13. 本轮计划检查

2026-10-27实际完成：

- 20份相关文档的220个本地链接、代码围栏和编码检查通过；版本导航已更新。
- 新1.0的7段Python示例加载与关键逻辑检查通过：身份兼容、原始指纹、Unicode坐标、来源失效、章界、幂等、版本冲突、原子替换失败、坏记录保护、核对覆盖和精确引用。
- 连同顺延至1.1的原12段，共19段Python语法检查通过；1.1旧执行检查仍保留原2026-10-26日期，不冒充本轮业务验证。
- 新1.0的3段TypeScript与1段TSX通过strict/noEmit检查；另外检查码点转换、来源版本、阅读与任务时点、迟到响应、草稿保留和引用去重。
- JSON与YAML示意可解析；新评测action尚未接入。根README和当前0.9.0包声明未因本轮计划调整而修改。

以上仅是文档与示例检查，不包含完整记录校验器、跨进程锁、生产接口、浏览器业务、在线模型或桌面验收。
S0—S8均待实施，正式验收状态仍见验收文档的待执行约定。

# v1.0.7 分模块实现示例

这些示例说明拟实施的职责与关键算法，采用[数据与接口契约](data-and-api.md)中的字段。导入路径存在不代表新接口已经实现；示例中的新类型、协调器接口和响应字段需要在对应阶段补齐。文档编制时只检查语法与设计一致性，不将其记为业务测试通过。

## 1. M2：先约束数据，再减少界面字段

线索可以只关联正文。节点和线索不能都为空；精确时间也不能通过一个任意字符串冒充可比较数值。

```python
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from hastings.workspace.story_models import StrictRecord

EntityId = Annotated[str, Field(min_length=1, max_length=64)]
ClueKind = Literal["truth", "mislead"]


class BindingTargets(StrictRecord):
    node_id: EntityId | None = None
    clue_id: EntityId | None = None

    @model_validator(mode="after")
    def require_target(self) -> BindingTargets:
        if self.node_id is None and self.clue_id is None:
            raise ValueError("正文关联缺少节点或线索目标")
        return self


class EventSpan(StrictRecord):
    start_minute: int
    end_minute: int
    approximate: bool = False

    @model_validator(mode="after")
    def ordered(self) -> EventSpan:
        if self.end_minute < self.start_minute:
            raise ValueError("事件结束早于开始")
        return self
```

目标是否存在、是否属于当前作品由变更服务验证。模型校验负责数据形状，服务负责真实来源，不能让前端选择器代替后端校验。

## 2. M3：UI 和模型共用选段解析

这里的 `text` 已通过现有 `normalize_newlines` 规范化，摘要由实际读取结果提供。UI 有源区间时严格验证；模型仅给引文时要求唯一匹配。

```python
from dataclasses import dataclass
from typing import NoReturn

from hastings.workspace.errors import WorkspaceError


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    quote: str


def anchor_error(code: str, why: str, fix: str) -> NoReturn:
    raise WorkspaceError(code=code, where="prose.anchor", why=why, fix=fix)


def unique_span(text: str, quote: str) -> Span:
    if not quote:
        anchor_error("PROSE_ANCHOR_MISMATCH", "引文为空", "重新选择正文片段")
    first = text.find(quote)
    if first < 0:
        anchor_error("PROSE_ANCHOR_MISSING", "当前正文没有对应原句", "刷新后重新选择片段")
    # 从 first + 1 查找，包含重叠重复，不能用非重叠计数判断唯一。
    if text.find(quote, first + 1) >= 0:
        anchor_error("PROSE_ANCHOR_AMBIGUOUS", "引文出现多次", "指定准确源区间或扩大引文")
    return Span(first, first + len(quote), quote)


def resolve_selection(
    *,
    text: str,
    actual_digest: str,
    expected_digest: str,
    quote: str,
    start: int | None,
    end: int | None,
) -> Span:
    if actual_digest != expected_digest:
        anchor_error("PROSE_SOURCE_STALE", "正文在选段后已修改", "刷新后重新选择片段")
    if start is None and end is None:
        return unique_span(text, quote)
    if start is None or end is None:
        anchor_error("PROSE_ANCHOR_MISMATCH", "源区间不完整", "重新选择连续原文")
    if not 0 <= start < end <= len(text) or text[start:end] != quote:
        anchor_error("PROSE_ANCHOR_MISMATCH", "区间与引文不一致", "重新选择连续原文")
    return Span(start, end, quote)


def locate_saved_span(
    *, text: str, current_digest: str, saved_digest: str, saved: Span
) -> Span:
    if current_digest == saved_digest:
        return resolve_selection(
            text=text, actual_digest=current_digest, expected_digest=saved_digest,
            quote=saved.quote, start=saved.start, end=saved.end,
        )
    return unique_span(text, saved.quote)
```

读取时的歧义错误应转成失效来源条目，仍允许查看原引用。写入时的错误应中止当前变更。原文查找不会启动模型，模型也不负责计算 Unicode 偏移。

前端坐标桥接沿用现有 `SourceHighlight.tsx`。补充选区转码时应检查 UTF-16 边界，避免在代理对中间截断：

```typescript
export function toCodePointOffset(text: string, utf16Offset: number): number {
  if (!Number.isInteger(utf16Offset) || utf16Offset < 0 || utf16Offset > text.length) {
    throw new Error("源坐标越界");
  }
  const before = text.charCodeAt(utf16Offset - 1);
  const after = text.charCodeAt(utf16Offset);
  const splitsPair = before >= 0xd800 && before <= 0xdbff
    && after >= 0xdc00 && after <= 0xdfff;
  if (splitsPair) throw new Error("源坐标落在字符内部");
  return Array.from(text.slice(0, utf16Offset)).length;
}
```

这是源文本坐标转换，不是从 DOM 可见文字反推 Markdown 的算法。DOM → 源文本仍需经过解析器位置映射。

## 3. M6：状态是事实投影，暂缓提醒不改变状态

先判断具体关联的有效性，再聚合节点。其他章节有未归属正文，不参与这个节点的状态计算。

```python
from dataclasses import dataclass
from typing import Literal, Sequence

Status = Literal["ready", "draft", "attention"]
AnchorState = Literal["exact", "relocated", "missing", "ambiguous", "unavailable"]


@dataclass(frozen=True)
class BindingCheck:
    binding_id: str
    anchor_state: AnchorState
    baseline_contract: str | None
    current_contract: str
    baseline_span: str | None
    current_span: str | None


def node_status(
    bindings: Sequence[BindingCheck], *, has_open_finding: bool
) -> tuple[Status, tuple[str, ...]]:
    reasons: set[str] = set()
    if has_open_finding:
        reasons.add("review_finding")
    for item in bindings:
        if item.anchor_state not in ("exact", "relocated"):
            reasons.add(f"anchor_{item.anchor_state}")
        if item.baseline_contract is None or item.baseline_span is None:
            reasons.add("not_checked")
            continue
        if item.baseline_contract != item.current_contract:
            reasons.add("plan_changed")
        if item.baseline_span != item.current_span:
            reasons.add("prose_changed")
    if reasons:
        return "attention", tuple(sorted(reasons))
    return ("ready", ()) if bindings else ("draft", ())


def should_show_reminder(current_fingerprint: str, deferred_fingerprint: str | None) -> bool:
    return current_fingerprint != deferred_fingerprint
```

提醒指纹使用当前事实，不包括页面位置、确认记录本身或无关方案修订：

```python
from dataclasses import dataclass
import hashlib
import json
from typing import Literal, Sequence


@dataclass(frozen=True)
class WarningBindingFact:
    binding_id: str
    document_id: str
    path: str
    clue_id: str | None
    anchor_state: Literal["exact", "relocated", "missing", "ambiguous", "unavailable"]
    current_span_digest: str | None
    baseline_contract_digest: str | None
    baseline_span_digest: str | None


@dataclass(frozen=True)
class WarningFindingFact:
    finding_id: str
    content_digest: str


def warning_fingerprint(
    *, workspace_id: str, variant_id: str, node_id: str,
    current_contract: str, bindings: Sequence[WarningBindingFact],
    findings: Sequence[WarningFindingFact],
) -> str:
    binding_facts = [
        {
            "binding_id": item.binding_id,
            "document_id": item.document_id,
            "path": item.path,
            "clue_id": item.clue_id,
            "anchor_state": (
                "valid" if item.anchor_state in ("exact", "relocated") else item.anchor_state
            ),
            "current_span_digest": item.current_span_digest,
            "baseline_contract_digest": item.baseline_contract_digest,
            "baseline_span_digest": item.baseline_span_digest,
        }
        for item in sorted(bindings, key=lambda item: item.binding_id)
    ]
    payload = {
        "workspace_id": workspace_id,
        "variant_id": variant_id,
        "node_id": node_id,
        "contract": current_contract,
        "bindings": binding_facts,
        "findings": [
            {"id": item.finding_id, "content_digest": item.content_digest}
            for item in sorted(findings, key=lambda item: item.finding_id)
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
```

示例显式列出输入字段，避免把全文摘要、绝对偏移或暂缓记录塞入指纹。`exact / relocated` 都表示有效定位：在引文前插入无关文字会移动区间，但不改变同一差异的指纹。核对 finding 的内容变化通过 `content_digest` 进入指纹，不能只比较一个长期不变的 finding ID。

## 4. M4：时间轴遵守真实时间比例

后端统一提供时间基准和范围。前端只计算位置；未知时间有明确的未定位结果，单个时刻不通过伪造持续时间来获得可点击宽度。

```typescript
type Span = { start_minute: number; end_minute: number };
type Domain = { min: number; max: number };
type Placement =
  | { kind: "unplaced" }
  | { kind: "point"; left: number }
  | { kind: "range"; left: number; width: number };

export function timePlacement(span: Span | null, domain: Domain | null): Placement {
  if (!span || !domain) return { kind: "unplaced" };
  const { start_minute: start, end_minute: end } = span;
  if (![start, end, domain.min, domain.max].every(Number.isFinite)
      || domain.max <= domain.min || end < start
      || start < domain.min || end > domain.max) {
    throw new Error("时间投影与坐标范围不一致");
  }
  const unit = 100 / (domain.max - domain.min);
  const left = (start - domain.min) * unit;
  return start === end
    ? { kind: "point", left }
    : { kind: "range", left, width: (end - start) * unit };
}
```

范围由所有有效事件求最小值和最大值并适当留白。所有事件同一时刻时，服务端显式扩展显示域；保留事件本身的零时长。点击区域可以扩大，代表时间的视觉标记仍保持真实比例。

```tsx
interface TimelineRowProps {
  node: { id: string; title: string; timeLabel: string; statusLabel: string };
  selected: boolean;
  onSelect: (id: string) => void;
}

export function TimelineRow({ node, selected, onSelect }: TimelineRowProps) {
  return (
    <button
      type="button"
      className="story-timeline-row"
      aria-pressed={selected}
      aria-label={`${node.title}，${node.timeLabel}，${node.statusLabel}`}
      draggable={false}
      onClick={() => onSelect(node.id)}
    >
      <span>{node.title}</span>
      <span>{node.timeLabel}</span>
    </button>
  );
}
```

示例省略视觉条的位置元素，重点是行的唯一动作是选中，没有拖动处理器。表单编辑另行调用变更服务。

## 5. M5：关系有来源，图只是受控投影

对称关系归一化，支撑关系保留方向。同一关系键只能存在一次，但线索图本身可以成环。

```python
from dataclasses import dataclass
from typing import Literal

RelationKind = Literal["supports", "contradicts", "co_misdirects"]


@dataclass(frozen=True)
class Relation:
    id: str
    source: str
    target: str
    kind: RelationKind
    reason: str


def relation_key(relation: Relation, visible_clue_ids: set[str]) -> tuple[str, str, str]:
    if relation.source not in visible_clue_ids or relation.target not in visible_clue_ids:
        raise ValueError("关系端点不存在")
    if relation.source == relation.target or not relation.reason.strip():
        raise ValueError("关系需要不同端点和明确说明")
    source, target = relation.source, relation.target
    if relation.kind in ("contradicts", "co_misdirects"):
        source, target = sorted((source, target))
    return relation.kind, source, target


def focused_relations(
    selected: str, relations: list[Relation], *, max_neighbors: int = 20
) -> tuple[list[Relation], int]:
    if max_neighbors < 1:
        raise ValueError("可见相邻线索数量必须为正数")
    incident = sorted(
        (r for r in relations if selected in (r.source, r.target)),
        key=lambda r: (r.kind, r.id),
    )
    neighbors = sorted({r.target if r.source == selected else r.source for r in incident})
    shown = set(neighbors[:max_neighbors])
    edges = [r for r in incident if (r.target if r.source == selected else r.source) in shown]
    return edges, len(neighbors) - len(shown)
```

完整实现还需限制边数、验证来源 ID、投影修订和章界。图未展示的条目继续在列表中可达。API 返回的剩余数量是未展示的相邻线索数量，不是“未发现关系”的数量。

## 6. M1：共享选择与异步读取

页面选择和辅助区模式分别维护。这样打开讨论不会丢失所选线索，返回详情也不会清空对话草稿。

```typescript
type Selection =
  | { kind: "none" }
  | { kind: "node"; nodeId: string }
  | { kind: "clue"; clueId: string }
  | { kind: "prose"; path: string; bindingId?: string };

type Auxiliary =
  | { kind: "details" }
  | { kind: "discussion"; conversationId: string; warningFingerprint: string };

interface WorkspaceSelection {
  workspaceId: string;
  variantId: string;
  mode: "prose" | "story" | "clues";
  selection: Selection;
  auxiliary: Auxiliary;
}
```

读取键同时包含作品、方案、对象与修订。取消请求之外再校验当前作用域，避免某些已完成的响应仍覆盖新选择：

```tsx
import { useEffect, useState } from "react";

type Resource<T> =
  | { key: string; phase: "loading" }
  | { key: string; phase: "ready"; value: T }
  | { key: string; phase: "error"; error: unknown };

export function useSelectedResource<T>(
  key: string,
  load: (signal: AbortSignal) => Promise<T>,
): Resource<T> {
  const [result, setResult] = useState<Resource<T>>({ key, phase: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    let active = true;
    setResult({ key, phase: "loading" });
    void load(controller.signal).then(
      value => { if (active) setResult({ key, phase: "ready", value }); },
      error => { if (active && !controller.signal.aborted) setResult({ key, phase: "error", error }); },
    );
    return () => { active = false; controller.abort(); };
  }, [key, load]);
  // effect 执行前也不能把旧对象短暂呈现在新标题之下。
  return result.key === key ? result : { key, phase: "loading" };
}
```

调用方用稳定回调提供 `load`。现有 API 请求封装需要接收 `AbortSignal`；错误继续交给统一格式化器，不能直接展示 JavaScript 异常内容作为产品文案。

## 7. M7：由点击创建只读核对讨论

以下示例依赖 P5 扩充后的 `TurnInput.context.story_reconcile`。请求键来自服务端提醒指纹，重试复用，不在组件每次渲染时生成。

```typescript
import { createConversationWithText, type TurnInput } from "../../api/workspace";

interface ReconcileTarget {
  workspaceId: string;
  variantId: string;
  variantRevision: string;
  nodeId: string;
  bindingIds: string[];
  fingerprint: string;
  paths: string[];
  observedDigests: Record<string, string>;
}

export function beginReconcile(target: ReconcileTarget) {
  const paths = [...new Set(target.paths)];
  const turn: TurnInput = {
    text: "请核对当前构思与关联正文，说明差异和依据，并提出调整建议。先进行讨论。",
    request_key: `reconcile:${target.fingerprint}`,
    purpose: "review",
    review_paths: paths,
    context: {
      selected_path: paths[0] ?? null,
      reference_paths: paths,
      observed_digests: target.observedDigests,
      story: {
        variant_id: target.variantId,
        revision: target.variantRevision,
        node_ids: [target.nodeId],
        purpose: "review",
      },
      story_reconcile: {
        variant_id: target.variantId,
        variant_revision: target.variantRevision,
        node_id: target.nodeId,
        binding_ids: target.bindingIds,
        warning_fingerprint: target.fingerprint,
      },
    },
  };
  return createConversationWithText(target.workspaceId, turn);
}
```

只在“讨论调整”的点击处理器中调用，按钮请求期间禁用。服务端仍需验证提醒和来源，持久化指纹与会话的对应关系，并实现同一请求的原会话回执；单靠前端禁用不能保证幂等。

作者随后要求写入时重新识别用途，不能把首次 review 永久沿用到后续回合。反过来，讨论中没有写入授权时也不能因为引用了正文而开放写工具。

## 8. M2 / M7：共用一个提交边界

下例是拟议协调器的职责接口，用于约束对现有 `StoryStore` 的扩展；不是在已有锁外再包一层锁的直接实现。真正代码应在现有提交锁和恢复日志中完成这些步骤。

```python
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class Mutation:
    request_key: str
    expected_revision: str
    expected_ledger_revision: str
    operations: tuple[dict[str, Any], ...]
    prose_patches: tuple[dict[str, Any], ...]


class LockedCommit(Protocol):
    def replay(self, request: Mutation) -> dict[str, Any] | None: ...
    def require_current_revisions(self, request: Mutation) -> None: ...
    def prepare(self, request: Mutation) -> object: ...
    def require_authorized_scope(self, prepared: object) -> None: ...
    def validate_targets_and_sources(self, prepared: object) -> None: ...
    def publish_recoverably(self, prepared: object) -> dict[str, Any]: ...


def submit_in_existing_lock(tx: LockedCommit, request: Mutation) -> dict[str, Any]:
    replayed = tx.replay(request)
    if replayed is not None:
        return replayed
    tx.require_current_revisions(request)
    prepared = tx.prepare(request)
    tx.require_authorized_scope(prepared)
    tx.validate_targets_and_sources(prepared)
    return tx.publish_recoverably(prepared)
```

`prepare` 解析临时 ID、构造绑定、计算变更后的结构与正文、生成逆操作；所有目标成功校验后才进入现有可恢复发布。重复请求先回放，不能先因修订已经推进而拒绝正常重试。

创作差异在准备阶段生成提醒，但不代替技术校验。`publish_recoverably` 返回真实修订和受影响对象，界面据此回读；`confirm_alignment` 只能在同一边界校验通过后改变基准。

## 9. M8：让验收覆盖前后端契约

定向断言关注作者可观察的结果和持久化事实，不只检查按钮是否存在。下面是拟新增接口测试的场景结构，具体夹具在 P0/P1 建立：

```python
def assert_deferred_attention(before: dict, after: dict) -> None:
    assert before["status"] == "attention"
    assert after["status"] == "attention"
    assert after["fingerprint"] == before["fingerprint"]
    assert after["reminder_visible"] is False


def assert_saved_mark(mark: dict, chapter_text: str) -> None:
    assert mark["kind"] in {"truth", "mislead"}
    assert chapter_text[mark["start"]:mark["end"]] == mark["quote"]
    assert mark["clue_id"]
```

这些断言只是检查点，不是完整测试。完整用例要经过写接口、重新读取及重启后读取；并覆盖重复请求、过期正文和故障恢复。新增 E2E 文件还要加入 `frontend/playwright.config.ts` 的显式 `testMatch`，否则文件存在也不会被默认项目执行。

## 10. 示例检查范围

编制计划时检查 Python 片段能被语法解析、JSON 请求有效、代码围栏闭合和文档链接存在。TypeScript/TSX 示例以字段与控制流程说明为目的，需要待新 DTO 接入后由项目类型检查验证。未运行示例业务逻辑、浏览器正式 E2E 或在线模型评测。

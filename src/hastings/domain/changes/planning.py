"""修订计划（T15.4，modules/change-impact.md §2 RevisionPlan / 不变量 1、4、5）。

把 ImpactReport 的 items 投影成带因果顺序的 RevisionPlan：默认按
Canon → Plan/State → Manuscript → Audit 四层分组建步（层是默认分组视图
与排序平局规则，**不是硬边**）；步间顺序来自报告 item 的依赖路径——
路径序即因果修复链（上游先修）。mentions 语义项没有结构路径，约束取
「被引用对象先修」。

环路的设计立场（四透镜②/③）：四层分组可能与因果链矛盾（plan 层两个
Beat 夹住 canon 层一个关系是常态）——**构建时不拒绝**，环路由
``execution_order``（步级）/``find_item_cycle``（项级，同层互指在步投影
中互相抵消、只有项级能看见）显式检测并输出最小环路径；作者用
``split_step``（拆组）、``set_step_scope``（调整范围）、
``accept_plan_risk``（接受风险，显式留档满足不变量 4）化解后再落库。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from hastings.domain.changes import error_codes as ec
from hastings.domain.changes.errors import violation
from hastings.domain.changes.impact import (
    ITEM_OPEN,
    ImpactItem,
    ImpactReport,
)
from hastings.domain.repository.ids import Actor

__all__ = [
    "EVENT_REVISION_PLAN_RECORDED",
    "ExcludedItem",
    "LAYER_AUDIT",
    "LAYER_CANON",
    "LAYER_LABELS",
    "LAYER_MANUSCRIPT",
    "LAYER_ORDER",
    "LAYER_PLAN",
    "PlanEdge",
    "PlanStep",
    "RevisionPlan",
    "RiskAcceptance",
    "accept_plan_risk",
    "build_revision_plan",
    "default_layer_of",
    "execution_order",
    "find_item_cycle",
    "set_step_scope",
    "split_step",
]

EVENT_REVISION_PLAN_RECORDED = "changes.plan.recorded"

LAYER_CANON = "canon"
LAYER_PLAN = "plan"
LAYER_MANUSCRIPT = "manuscript"
LAYER_AUDIT = "audit"
LAYER_ORDER = (LAYER_CANON, LAYER_PLAN, LAYER_MANUSCRIPT, LAYER_AUDIT)
LAYER_LABELS = {
    LAYER_CANON: "Canon 真相与规则",
    LAYER_PLAN: "叙事计划与角色认知",
    LAYER_MANUSCRIPT: "正文",
    LAYER_AUDIT: "审计与残余风险",
}

# kind 末段 → 层的白名单（§2 RevisionPlan 四层语义；未识别一律落审计层：
# 不能分类的对象按「需要人审的残余风险」处理，不冒充已分组）
_CANON_SEGMENTS = frozenset(
    {
        "premise",
        "character",
        "relationship",
        "place",
        "world_rule",
        "mystery_truth",
        "truth_fact",
        "clue",
        "red_herring",
        "foreshadow",
    }
)
_PLAN_SEGMENTS = frozenset(
    {
        "beat",
        "scene",
        "plot",
        "plot_beat",
        "arc",
        "book",
        "series",
        "timeline_event",
        "knowledge",
        "disclosure",
    }
)


def default_layer_of(kind: str) -> str:
    """kind → 四层之一；manuscript/narrative 前缀优先，末段白名单次之。"""
    if kind.startswith("manuscript."):
        return LAYER_MANUSCRIPT
    if kind.startswith("narrative."):
        return LAYER_PLAN
    segment = kind.rsplit(".", 1)[-1]
    if segment in _CANON_SEGMENTS:
        return LAYER_CANON
    if segment in _PLAN_SEGMENTS:
        return LAYER_PLAN
    return LAYER_AUDIT


@dataclass(frozen=True, slots=True)
class PlanEdge:
    """计划约束边：before 先修、after 后修；item_index 是证据来源。"""

    before: str
    after: str
    item_index: int

    def to_content(self) -> dict[str, Any]:
        return {"before": self.before, "after": self.after, "item_index": self.item_index}

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> PlanEdge:
        return cls(
            before=str(content.get("before", "")),
            after=str(content.get("after", "")),
            item_index=int(content.get("item_index", 0)),
        )


@dataclass(frozen=True, slots=True)
class PlanStep:
    """一个修订步：同层对象的分组，depends_on 是先修步骤（DAG，可为空）。"""

    step_id: str
    layer: str
    title: str
    target_artifact_ids: tuple[str, ...]
    item_indexes: tuple[int, ...]
    depends_on: tuple[str, ...] = ()

    def to_content(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "layer": self.layer,
            "title": self.title,
            "target_artifact_ids": list(self.target_artifact_ids),
            "item_indexes": list(self.item_indexes),
            "depends_on": list(self.depends_on),
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> PlanStep:
        return cls(
            step_id=str(content.get("step_id", "")),
            layer=str(content.get("layer", LAYER_AUDIT)),
            title=str(content.get("title", "")),
            target_artifact_ids=tuple(str(t) for t in (content.get("target_artifact_ids") or ())),
            item_indexes=tuple(int(i) for i in (content.get("item_indexes") or ())),
            depends_on=tuple(str(d) for d in (content.get("depends_on") or ())),
        )


@dataclass(frozen=True, slots=True)
class ExcludedItem:
    """被移出执行范围的影响项：留痕而不是静默删除（不变量 4）。"""

    item_index: int
    target_artifact_id: str
    severity: str
    reason: str

    def to_content(self) -> dict[str, Any]:
        return {
            "item_index": self.item_index,
            "target_artifact_id": self.target_artifact_id,
            "severity": self.severity,
            "reason": self.reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> ExcludedItem:
        return cls(
            item_index=int(content.get("item_index", 0)),
            target_artifact_id=str(content.get("target_artifact_id", "")),
            severity=str(content.get("severity", "")),
            reason=str(content.get("reason", "")),
        )


@dataclass(frozen=True, slots=True)
class RiskAcceptance:
    """一次接受风险的审计凭证：谁、何时、为什么（不变量 4 的「显式保留」）。"""

    item_index: int
    by: str
    at: str
    reason: str

    def to_content(self) -> dict[str, Any]:
        return {
            "item_index": self.item_index,
            "by": self.by,
            "at": self.at,
            "reason": self.reason,
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> RiskAcceptance:
        return cls(
            item_index=int(content.get("item_index", 0)),
            by=str(content.get("by", "")),
            at=str(content.get("at", "")),
            reason=str(content.get("reason", "")),
        )


@dataclass(frozen=True, slots=True)
class RevisionPlan:
    """修订计划：绑定 ChangeSet 与基础 Commit（不变量 1），head 前移即 stale。"""

    plan_id: str
    change_set_id: str
    base_commit_id: str
    steps: tuple[PlanStep, ...] = ()
    unassigned: tuple[ExcludedItem, ...] = ()
    constraints: tuple[PlanEdge, ...] = ()
    risk_acceptances: tuple[RiskAcceptance, ...] = ()

    def is_stale(self, head_commit_id: str) -> bool:
        return head_commit_id != self.base_commit_id

    def step_of(self, step_id: str) -> PlanStep | None:
        return next((s for s in self.steps if s.step_id == step_id), None)

    def to_content(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "change_set_id": self.change_set_id,
            "base_commit_id": self.base_commit_id,
            "steps": [s.to_content() for s in self.steps],
            "unassigned": [e.to_content() for e in self.unassigned],
            "constraints": [c.to_content() for c in self.constraints],
            "risk_acceptances": [r.to_content() for r in self.risk_acceptances],
        }

    @classmethod
    def from_content(cls, content: dict[str, Any]) -> RevisionPlan:
        return cls(
            plan_id=str(content.get("plan_id", "")),
            change_set_id=str(content.get("change_set_id", "")),
            base_commit_id=str(content.get("base_commit_id", "")),
            steps=tuple(PlanStep.from_content(s) for s in (content.get("steps") or ())),
            unassigned=tuple(
                ExcludedItem.from_content(e) for e in (content.get("unassigned") or ())
            ),
            constraints=tuple(PlanEdge.from_content(c) for c in (content.get("constraints") or ())),
            risk_acceptances=tuple(
                RiskAcceptance.from_content(r) for r in (content.get("risk_acceptances") or ())
            ),
        )


# ---------------------------------------------------------------------------
# 约束抽取（报告 → 因果修复链）
# ---------------------------------------------------------------------------
def _path_nodes(path: tuple[Any, ...]) -> tuple[str, ...]:
    """从边路径重建节点序列（首节点是种子；backward 跳取 hop.source）。"""
    if not path:
        return ()
    nodes = [path[0].source]
    for hop in path:
        nodes.append(hop.target if hop.target != nodes[-1] else hop.source)
    return tuple(nodes)


def item_constraints(item: ImpactItem, index: int) -> tuple[PlanEdge, ...]:
    """单项的修复顺序约束：结构项按路径序，mentions 项取「被引用者先修」。"""
    if item.mentioned_artifact_id is not None:
        return (PlanEdge(item.mentioned_artifact_id, item.target_artifact_id, index),)
    nodes = _path_nodes(item.path)
    return tuple(PlanEdge(nodes[k], nodes[k + 1], index) for k in range(len(nodes) - 1))


def extract_constraints(report: ImpactReport) -> tuple[PlanEdge, ...]:
    """全部 open 项的约束边（按 (before, after) 去重，保留首个证据）。"""
    edges: list[PlanEdge] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(report.items):
        if item.status != ITEM_OPEN:
            continue
        for edge in item_constraints(item, index):
            key = (edge.before, edge.after)
            if key not in seen:
                seen.add(key)
                edges.append(edge)
    return tuple(edges)


# ---------------------------------------------------------------------------
# 构建（纯投影，不做环裁决）
# ---------------------------------------------------------------------------
def build_revision_plan(
    report: ImpactReport,
    *,
    kind_of: Any = None,
    layer_of: Any = default_layer_of,
    plan_id: str = "",
) -> RevisionPlan:
    """把报告投影为四层分组计划；非 open 项显式进 unassigned（留痕）。

    ``kind_of`` 是目标 ID → kind 的解析函数（与 traverse_impact 同构，由
    调用方从 Snapshot 提供）；解析失败按审计层保守兜底。
    """
    constraints = extract_constraints(report)
    open_indexes = [i for i, it in enumerate(report.items) if it.status == ITEM_OPEN]
    unassigned = tuple(
        ExcludedItem(
            item_index=i,
            target_artifact_id=it.target_artifact_id,
            severity=it.severity,
            reason=_status_reason(it),
        )
        for i, it in enumerate(report.items)
        if it.status != ITEM_OPEN
    )
    layer_targets: dict[str, list[str]] = {}
    for i in open_indexes:
        target = report.items[i].target_artifact_id
        layer = _resolve_layer(target, kind_of, layer_of)
        bucket = layer_targets.setdefault(layer, [])
        if target not in bucket:
            bucket.append(target)
    steps: list[PlanStep] = []
    for layer in LAYER_ORDER:
        targets = layer_targets.get(layer)
        if not targets:
            continue
        target_set = set(targets)
        indexes = tuple(i for i in open_indexes if report.items[i].target_artifact_id in target_set)
        steps.append(
            PlanStep(
                step_id=f"step_{layer}",
                layer=layer,
                title=f"{LAYER_LABELS[layer]}（{len(targets)} 个对象）",
                target_artifact_ids=tuple(sorted(targets)),
                item_indexes=indexes,
            )
        )
    plan = RevisionPlan(
        plan_id=plan_id,
        change_set_id=report.change_set_id,
        base_commit_id=report.base_commit_id,
        steps=tuple(steps),
        unassigned=unassigned,
        constraints=constraints,
    )
    return _reproject(plan)


def _resolve_layer(target: str, kind_of: Any, layer_of: Any) -> str:
    kind = kind_of(target) if kind_of is not None else None
    return layer_of(kind) if kind else LAYER_AUDIT


def _status_reason(item: ImpactItem) -> str:
    return {
        "accepted_risk": "作者已接受风险（报告层标记）",
        "dismissed": "作者已驳回该项",
    }.get(item.status, f"项状态为 {item.status!r}，不进入执行步骤")


# ---------------------------------------------------------------------------
# 步投影 / 环检测 / 执行顺序
# ---------------------------------------------------------------------------
def project_step_dependencies(
    steps: tuple[PlanStep, ...], constraints: tuple[PlanEdge, ...]
) -> dict[str, tuple[str, ...]]:
    """约束边投影到步：跨步边成为依赖；同步内边与未分组端点被折叠。"""
    step_of = {t: s.step_id for s in steps for t in s.target_artifact_ids}
    deps: dict[str, set[str]] = {s.step_id: set() for s in steps}
    for edge in constraints:
        before = step_of.get(edge.before)
        after = step_of.get(edge.after)
        if before is not None and after is not None and before != after:
            deps[after].add(before)
    return {step_id: tuple(sorted(v)) for step_id, v in deps.items()}


def find_minimal_cycle(
    nodes: tuple[str, ...] | list[str], edges: tuple[tuple[str, str], ...]
) -> tuple[str, ...] | None:
    """BFS 最短回环：按节点字典序尝试起点，返回开路径（不重复尾节点）。"""
    successors: dict[str, list[str]] = {node: [] for node in nodes}
    for before, after in edges:
        if before in successors and after in successors:
            successors[before].append(after)
    for start in sorted(nodes):
        prev: dict[str, str | None] = {start: None}
        queue: list[str] = [start]
        while queue:
            current = queue.pop(0)
            for nxt in sorted(successors.get(current, ())):
                if nxt == start:
                    path = [current]
                    while prev[path[-1]] is not None:
                        predecessor = prev[path[-1]]
                        path.append(predecessor if predecessor is not None else start)
                    path.reverse()
                    return tuple(path)
                if nxt not in prev:
                    prev[nxt] = current
                    queue.append(nxt)
    return None


def find_item_cycle(plan: RevisionPlan, report: ImpactReport) -> tuple[str, ...] | None:
    """项级环检测：同层互指在步投影里互相抵消，只有项级能看见。

    只考虑进入执行步骤的目标（unassigned 项没有执行语义，不参与环）。
    """
    covered = {t for s in plan.steps for t in s.target_artifact_ids}
    edges: list[tuple[str, str]] = []
    for index, item in enumerate(report.items):
        if item.status != ITEM_OPEN:
            continue
        for edge in item_constraints(item, index):
            if edge.before in covered and edge.after in covered:
                edges.append((edge.before, edge.after))
    return find_minimal_cycle(tuple(sorted(covered)), tuple(edges))


def _fmt_cycle(cycle: tuple[str, ...]) -> str:
    return " → ".join((*cycle, cycle[0])) if cycle else "(空)"


def execution_order(plan: RevisionPlan) -> tuple[str, ...]:
    """拓扑排序步（不变量 5）；成环即拒绝并输出最小环路径与化解手段。

    排序每次选一个「依赖已满足」的步，平局按（层序，step_id）——层序只是
    平局规则，永远不覆盖因果约束。
    """
    deps = {s.step_id: s.depends_on for s in plan.steps}
    layer_rank = {s.step_id: LAYER_ORDER.index(s.layer) for s in plan.steps}
    order: list[str] = []
    done: set[str] = set()
    while len(order) < len(plan.steps):
        ready = [sid for sid, d in deps.items() if sid not in done and all(x in done for x in d)]
        if not ready:
            edges = tuple(
                (before, sid)
                for sid, ds in project_step_dependencies(plan.steps, plan.constraints).items()
                for before in ds
            )
            cycle = find_minimal_cycle(tuple(s.step_id for s in plan.steps), edges)
            raise violation(
                ec.CHANGES_PLAN_CYCLE,
                "changes.plan.execution_order",
                f"修订计划步骤依赖成环：{_fmt_cycle(cycle or ())}",
                "用 split_step 拆组、set_step_scope 调整范围或 accept_plan_risk"
                " 接受风险化解环路后重试",
                cycle=list(cycle or ()),
            )
        best = min(ready, key=lambda sid: (layer_rank[sid], sid))
        done.add(best)
        order.append(best)
    return tuple(order)


def _reproject(plan: RevisionPlan) -> RevisionPlan:
    """按约束重算全部步骤的 depends_on（构建与每次手工调整后共用）。"""
    deps = project_step_dependencies(plan.steps, plan.constraints)
    steps = tuple(replace(s, depends_on=deps[s.step_id]) for s in plan.steps)
    return replace(plan, steps=steps)


# ---------------------------------------------------------------------------
# 作者手工调整（拆组 / 调整范围 / 接受风险）
# ---------------------------------------------------------------------------
def _require_step(plan: RevisionPlan, step_id: str, where: str) -> Any:
    step = plan.step_of(step_id)
    if step is None:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"步骤 {step_id!r} 不存在于计划中",
            "改用计划里已有的 step_id（见 plan.steps 的 step_id 列表）",
            step_id=step_id,
            known=[s.step_id for s in plan.steps],
        )
    return step


def split_step(
    plan: RevisionPlan,
    report: ImpactReport,
    step_id: str,
    groups: tuple[tuple[str, ...], ...],
) -> RevisionPlan:
    """拆组：把一步按目标分成多步（子步 ID 为 ``{step_id}.{序号}``）。

    分组必须恰好划分原步骤的目标（不重不漏、组非空）；拆分后重投影依赖
    并保持其余步骤不动。
    """
    where = "changes.plan.split_step"
    step = _require_step(plan, step_id, where)
    normalized = tuple(tuple(group) for group in groups)
    if not normalized:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "至少要拆出一组（收到空分组序列）",
            '把步骤目标分成 2 组以上，例如 (("a",), ("b",))；不拆就无需调用',
            step_id=step_id,
        )
    if any(not group for group in normalized):
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "拆分出的组不能为空",
            "每个组至少包含一个目标；要整体移出目标请用 set_step_scope",
            step_id=step_id,
        )
    flattened = [t for group in normalized for t in group]
    if len(set(flattened)) != len(flattened):
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "同一目标出现在多个分组里",
            "分组必须互斥；每个目标只属于一组",
            step_id=step_id,
        )
    if set(flattened) != set(step.target_artifact_ids):
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "分组没有恰好覆盖原步骤的目标",
            f"原步骤目标是 {list(step.target_artifact_ids)}；分组必须不重不漏地划分它们",
            step_id=step_id,
            given=sorted(set(flattened)),
        )
    substeps: list[PlanStep] = []
    for number, group in enumerate(normalized, start=1):
        group_set = set(group)
        indexes = tuple(
            i for i in step.item_indexes if report.items[i].target_artifact_id in group_set
        )
        substeps.append(
            PlanStep(
                step_id=f"{step_id}.{number}",
                layer=step.layer,
                title=f"{step.title}（拆分 {number}/{len(normalized)}）",
                target_artifact_ids=tuple(sorted(group_set)),
                item_indexes=indexes,
            )
        )
    new_steps: list[PlanStep] = []
    for existing in plan.steps:
        if existing.step_id == step_id:
            new_steps.extend(substeps)
        else:
            new_steps.append(existing)
    return _reproject(replace(plan, steps=tuple(new_steps)))


def set_step_scope(
    plan: RevisionPlan, report: ImpactReport, step_id: str, keep_targets: tuple[str, ...]
) -> RevisionPlan:
    """调整范围：把未保留的目标移出执行步骤，显式记入 unassigned。"""
    where = "changes.plan.set_step_scope"
    step = _require_step(plan, step_id, where)
    keep_set = set(keep_targets)
    if not keep_set:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "保留范围不能为空（等于清空整步）",
            "要移除全部目标请逐项 accept_plan_risk（接受风险）或重建计划",
            step_id=step_id,
        )
    unknown = keep_set - set(step.target_artifact_ids)
    if unknown:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            f"保留目标 {sorted(unknown)} 不在该步骤中",
            f"该步骤目标是 {list(step.target_artifact_ids)}；只从中选择保留项",
            step_id=step_id,
        )
    dropped_targets = set(step.target_artifact_ids) - keep_set
    kept_indexes = tuple(
        i for i in step.item_indexes if report.items[i].target_artifact_id in keep_set
    )
    excluded = tuple(
        ExcludedItem(
            item_index=i,
            target_artifact_id=report.items[i].target_artifact_id,
            severity=report.items[i].severity,
            reason=f"作者调整步骤范围：从 {step_id} 移出",
        )
        for i in step.item_indexes
        if report.items[i].target_artifact_id in dropped_targets
    )
    updated = replace(step, target_artifact_ids=tuple(sorted(keep_set)), item_indexes=kept_indexes)
    new_steps = tuple(updated if s.step_id == step_id else s for s in plan.steps)
    return _reproject(replace(plan, steps=new_steps, unassigned=(*plan.unassigned, *excluded)))


def accept_plan_risk(
    plan: RevisionPlan,
    report: ImpactReport,
    item_indexes: tuple[int, ...],
    *,
    by: Actor,
    at: Any,
    reason: str,
) -> RevisionPlan:
    """接受风险：把项移出执行步骤并留档（谁/何时/为什么，不变量 4）。"""
    where = "changes.plan.accept_risk"
    indexes = tuple(item_indexes)
    if not indexes:
        raise violation(
            ec.CHANGES_INVALID_COMMAND,
            where,
            "至少选择一项接受风险（收到空列表）",
            "从 plan.steps 的 item_indexes 里选择要接受风险的项",
        )
    covered = {i: s for s in plan.steps for i in s.item_indexes}
    for index in indexes:
        if not 0 <= index < len(report.items):
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                where,
                f"item_index {index} 超出报告范围（共 {len(report.items)} 项）",
                "item_index 是报告 items 的下标；先查报告再选择",
                item_index=index,
                report_size=len(report.items),
            )
        if index not in covered:
            raise violation(
                ec.CHANGES_INVALID_COMMAND,
                where,
                f"第 {index} 项不在任何执行步骤中",
                "该项已被移出（见 unassigned）；重复接受无需再做",
                item_index=index,
            )
    iso_at = at.isoformat() if hasattr(at, "isoformat") else str(at)
    accepted = set(indexes)
    new_unassigned = list(plan.unassigned)
    new_acceptances = list(plan.risk_acceptances)
    for index in indexes:
        item = report.items[index]
        new_unassigned.append(
            ExcludedItem(
                item_index=index,
                target_artifact_id=item.target_artifact_id,
                severity=item.severity,
                reason=f"作者接受风险：{reason}",
            )
        )
        new_acceptances.append(
            RiskAcceptance(item_index=index, by=f"{by.kind}:{by.id}", at=iso_at, reason=reason)
        )
    steps: list[PlanStep] = []
    for step in plan.steps:
        drop = {i for i in step.item_indexes if i in accepted}
        if not drop:
            steps.append(step)
            continue
        kept_indexes = tuple(i for i in step.item_indexes if i not in drop)
        kept_targets = sorted({report.items[i].target_artifact_id for i in kept_indexes})
        if not kept_targets:
            continue  # 整步清空 → 步消失（目标全部风险化）
        steps.append(
            replace(step, target_artifact_ids=tuple(kept_targets), item_indexes=kept_indexes)
        )
    return _reproject(
        replace(
            plan,
            steps=tuple(steps),
            unassigned=tuple(new_unassigned),
            risk_acceptances=tuple(new_acceptances),
        )
    )

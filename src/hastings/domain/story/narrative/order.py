"""结构顺序（Phase 13 T13.4a；modules/story-domain.md §2.2/§5 不变量 10）。

把 Snapshot 中的叙事结构按「Series → Book → Arc → Chapter → Scene → Beat」
深度优先展开为确定性序列：同级先按 `order_key` 升序，未排序者（空 key）排在
末尾，同 key 再按 ArtifactId 兜底。顺序绝不依赖字典遍历，因此同样的快照永远
得到同样的序列——这是状态折叠可复现的前提。

顺序是排序键不是身份：移动只改 order_key，不改变 ArtifactId。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.changes import StoryWarning
from hastings.domain.story.errors import violation

__all__ = ["Ordering", "last_beat_of_node", "ordered_beats", "sort_key", "structural_order"]


def sort_key(entry: StoryEntry) -> tuple[int, str, str]:
    """同级排序键：(是否已排序, order_key, artifact_id)。空 key 归入末尾。"""
    raw = entry.content.get("order_key") or ""
    text = raw if isinstance(raw, str) else str(raw)
    return (0 if text else 1, text, entry.artifact_id)


@dataclass(frozen=True, slots=True)
class Ordering:
    """结构展开结果：全部节点+节拍序列、仅节拍序列、顺序告警。"""

    entries: tuple[StoryEntry, ...]
    beats: tuple[StoryEntry, ...]
    warnings: tuple[StoryWarning, ...]


def _duplicate_warnings(group: list[StoryEntry], warnings: list[StoryWarning]) -> None:
    """同级、同 kind 下重复的非空 order_key 是可观测冲突（按 ID 兜底，不致命）。"""
    seen: dict[tuple[str, str], list[str]] = {}
    for entry in group:
        raw = entry.content.get("order_key") or ""
        if not isinstance(raw, str) or not raw:
            continue
        seen.setdefault((entry.kind, raw), []).append(entry.artifact_id)
    for (kind, order_key), ids in sorted(seen.items()):
        if len(ids) > 1:
            warnings.append(
                StoryWarning(
                    code=ec.STORY_INVALID_COMMAND,
                    where=f"narrative.order:{kind}",
                    why=f"同级多个对象共用 order_key={order_key!r}：{sorted(ids)}",
                    fix="改用不同 order_key；顺序是排序键而非身份，同名序按 ID 兜底",
                )
            )


def _visit(
    entry: StoryEntry,
    children: dict[str, list[StoryEntry]],
    visited: set[str],
    ordered: list[StoryEntry],
    beats: list[StoryEntry],
) -> None:
    if entry.artifact_id in visited:  # 防御环（命令层已禁止，此处仍不敢死循环）
        return
    visited.add(entry.artifact_id)
    ordered.append(entry)
    if entry.kind == kinds.KIND_BEAT:
        beats.append(entry)
    for child in sorted(children.get(entry.artifact_id, []), key=sort_key):
        _visit(child, children, visited, ordered, beats)


def structural_order(catalog: StoryCatalog) -> Ordering:
    """展开结构为确定性序列，并报告孤儿父级与同名序冲突。"""
    structure: tuple[StoryEntry, ...] = (*catalog.nodes(), *catalog.beats())
    node_ids = {entry.artifact_id for entry in catalog.nodes()}

    children: dict[str, list[StoryEntry]] = {}
    roots: list[StoryEntry] = []
    warnings: list[StoryWarning] = []
    for entry in structure:
        parent_id = entry.content.get("parent_id")
        if not isinstance(parent_id, str) or not parent_id:
            roots.append(entry)
            continue
        if parent_id in node_ids:
            children.setdefault(parent_id, []).append(entry)
        else:
            roots.append(entry)
            warnings.append(
                StoryWarning(
                    code=ec.STORY_MISSING_PRECONDITION,
                    where=f"narrative.order:{entry.artifact_id}",
                    why=f"父级 {parent_id!r} 不存在，对象被当作根展开",
                    fix="补齐父级或把 parent_id 改指向结构中存在的节点",
                )
            )

    _duplicate_warnings(roots, warnings)
    for group in children.values():
        _duplicate_warnings(group, warnings)

    ordered: list[StoryEntry] = []
    beats: list[StoryEntry] = []
    visited: set[str] = set()
    for root in sorted(roots, key=sort_key):
        _visit(root, children, visited, ordered, beats)
    return Ordering(entries=tuple(ordered), beats=tuple(beats), warnings=tuple(warnings))


def ordered_beats(catalog: StoryCatalog) -> tuple[StoryEntry, ...]:
    """按结构顺序返回节拍；状态折叠的唯一顺序来源。"""
    return structural_order(catalog).beats


def last_beat_of_node(catalog: StoryCatalog, node_id: str, *, where: str) -> str:
    """结构节点（章/场景）内按结构顺序的最后一个节拍，用作叙述位置。

    只按 ``parent_id`` 归属与 ``structural_order`` 判定，不引入「第几章」这类
    序号——叙述位置、故事时间与章节序号是三种不同的东西，不能互相冒充。
    """
    ordering = structural_order(catalog)
    if node_id not in {entry.artifact_id for entry in ordering.entries}:
        raise violation(
            ec.STORY_UNKNOWN_ARTIFACT,
            where,
            f"结构节点 {node_id!r} 不在当前快照中",
            "改用结构中存在的章/场景 ID（先用结构展开查询可用节点）",
            artifact_id=node_id,
        )

    parent_of: dict[str, str | None] = {}
    for entry in ordering.entries:
        parent = entry.content.get("parent_id")
        parent_of[entry.artifact_id] = parent if isinstance(parent, str) and parent else None

    def is_under(beat_id: str) -> bool:
        ancestor = parent_of.get(beat_id)
        seen: set[str] = set()
        while ancestor and ancestor not in seen:
            if ancestor == node_id:
                return True
            seen.add(ancestor)
            ancestor = parent_of.get(ancestor)
        return False

    beats = [entry.artifact_id for entry in ordering.beats if is_under(entry.artifact_id)]
    if not beats:
        raise violation(
            ec.STORY_MISSING_PRECONDITION,
            where,
            f"结构节点 {node_id!r} 下没有节拍，无法作为叙述位置",
            "先在该章/场景下创建节拍，或改用 at_beat_id 直接指定节拍",
            node_id=node_id,
        )
    return beats[-1]

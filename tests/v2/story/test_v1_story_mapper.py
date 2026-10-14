"""T13.8 V1 dry-run 映射器测试（plan §4 T13.8 / §6 / §8）。

把 Phase 11 fixture（tests/fixtures/v1_workspace/winter-manor-case）逐字段喂给
映射器，断言四件事：

1. 覆盖：每个 V1 源字段恰好被归类一次，结果 ∈ {mapped, legacy, ambiguous, error}；
2. 只读：运行前后 fixture 字节哈希与 manifest 一致（dry-run 不写 V2 Repository）；
3. 确定性：同一输入两次运行，报告与 Proposal 逐字相等；
4. 泄压阀：无法无损表达的悬疑要素（red herring 缺依据、method 缺真凶）成为
   定位明确、不进入 Proposal 的 error，而非静默丢失。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from hastings.bible.models import (
    Character,
    Clue,
    Foreshadow,
    Method,
    OutlineEntry,
    TimelineEntry,
)
from hastings.bible.store import StoryWorkspace
from hastings.domain.story import error_codes as ec
from hastings.domain.story import kinds
from hastings.domain.story.ids import deterministic_story_id
from hastings.interfaces.importers.v1_story_mapper import (
    IMPORT_OUTCOMES,
    map_v1_workspace,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[2] / "fixtures" / "v1_workspace" / "winter-manor-case"
)
SLUG = "winter-manor-case"

# V1 账本类型 → 权威 schema：覆盖断言据此比对「字段集合」而非硬编码字段名。
V1_MODELS: dict[str, type] = {
    "character": Character,
    "clue": Clue,
    "foreshadow": Foreshadow,
    "method": Method,
    "timeline": TimelineEntry,
    "outline": OutlineEntry,
}

# fixture 全部账本类型的字段总数：角色 3×7 + 线索 3×7 + 伏笔 2×6 + 手法 1×7
# + 时间线 3×7 + 章纲 3×7 = 103。任何字段被漏归类都会让此数下降。
_TOTAL_SOURCE_FIELDS = 3 * 7 + 3 * 7 + 2 * 6 + 1 * 7 + 3 * 7 + 3 * 7


def _workspace() -> StoryWorkspace:
    return StoryWorkspace(root=FIXTURE_ROOT.parent, slug=SLUG)


def _manifest_hashes() -> dict[str, str]:
    data = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    return {entry["path"]: entry["sha256"] for entry in data["files"]}


def test_every_source_field_is_classified_exactly_once() -> None:
    report = map_v1_workspace(_workspace()).report

    seen_total = 0
    for entity in report.entities:
        model = V1_MODELS[entity.entity_type]
        fields = [f.field for f in entity.fields]
        assert len(fields) == len(set(fields)), f"{entity.source_name} 有重复字段归类"
        assert set(fields) == set(model.model_fields), (
            f"{entity.source_name} 字段集合与 V1 schema 不符（漏归类或多归类）"
        )
        for mapping in entity.fields:
            assert mapping.outcome in IMPORT_OUTCOMES, f"未知 outcome：{mapping.outcome}"
        seen_total += len(fields)

    assert seen_total == _TOTAL_SOURCE_FIELDS, "有源字段未被扫入报告"


def test_run_does_not_write_fixture() -> None:
    before = _manifest_hashes()
    map_v1_workspace(_workspace())
    after = {rel: hashlib.sha256((FIXTURE_ROOT / rel).read_bytes()).hexdigest() for rel in before}
    assert after == before, "dry-run 修改了 fixture（映射器必须只读源工作区）"


def test_two_runs_are_deterministic() -> None:
    first = map_v1_workspace(_workspace())
    second = map_v1_workspace(_workspace())
    assert first.report == second.report
    assert first.proposal == second.proposal


def test_expected_counts_match_proposal() -> None:
    plan = map_v1_workspace(_workspace())

    assert plan.report.expected_artifacts == 22, "fixture 基线：13 个可规划实体共 22 个 Artifact"
    assert plan.report.expected_relations == 0, "V1 跨账本引用均为自由文本，无可直接建立的依赖边"
    assert plan.report.expected_artifacts == sum(
        len(change.artifact_changes) for change in plan.proposal
    )
    assert all(change.relation_changes == () for change in plan.proposal)


def test_blocking_entities_are_not_planned() -> None:
    report = map_v1_workspace(_workspace()).report

    blocked = {(e.entity_type, e.source_name) for e in report.entities if not e.planned}
    assert blocked == {("clue", "缺页的宾客簿"), ("method", "书房密室伪装")}
    assert {i.code for i in report.errors} == {
        ec.STORY_RED_HERRING_WITHOUT_CAUSE,
        ec.STORY_MISSING_REQUIRED_FIELD,
    }
    assert all(i.where and i.why and i.fix for i in report.errors)


def test_planned_entities_never_emit_blocked_ids() -> None:
    plan = map_v1_workspace(_workspace())
    blocked_ids = {e.target_id for e in plan.report.entities if not e.planned}
    emitted: set[str] = set()
    for change in plan.proposal:
        for artifact_change in change.artifact_changes:
            emitted.add(artifact_change.artifact_id.value)
    assert emitted.isdisjoint(blocked_ids)


def test_proposal_uses_stable_deterministic_ids() -> None:
    plan = map_v1_workspace(_workspace())
    by_kind: dict[str, set[str]] = {}
    for change in plan.proposal:
        for artifact_change in change.artifact_changes:
            by_kind.setdefault(artifact_change.kind or "", set()).add(
                artifact_change.artifact_id.value
            )

    assert (
        deterministic_story_id(kinds.KIND_CHARACTER, "v1:character:桐生绫")
        in by_kind[kinds.KIND_CHARACTER]
    )
    assert (
        deterministic_story_id(kinds.KIND_CHAPTER, "v1:outline:第一章 停电之夜")
        in by_kind[kinds.KIND_CHAPTER]
    )


def test_legacy_and_ambiguous_raw_values_are_preserved() -> None:
    plan = map_v1_workspace(_workspace())
    clue = next(e for e in plan.report.entities if e.source_name == "熔化的挂钟")
    assert clue.planned and clue.target_kind == kinds.KIND_CLUE

    change = next(
        c for c in plan.proposal if c.artifact_changes[0].artifact_id.value == clue.target_id
    )
    content = change.artifact_changes[0].content
    # legacy 字段按领域约定平铺在 content 顶层，反解时由 split_legacy 收回
    v1 = content["v1"]
    assert v1["status"] == "planted"
    assert v1["is_genuine"] is True
    # 歧义字段（章节引用 / 自由文本指向）同样原样保留，绝不静默丢失
    assert v1["chapter"] == "ch01"
    assert v1["points_to"] == "早坂慎一"


def test_entity_kinds_are_dispatched_by_content() -> None:
    report = map_v1_workspace(_workspace()).report
    by_type: dict[str, set[str]] = {}
    for entity in report.entities:
        by_type.setdefault(entity.entity_type, set()).add(entity.target_kind)

    assert by_type["character"] == {kinds.KIND_CHARACTER}
    assert by_type["clue"] == {kinds.KIND_CLUE, kinds.KIND_RED_HERRING}
    assert by_type["foreshadow"] == {kinds.KIND_FORESHADOW}
    assert by_type["method"] == {kinds.KIND_MYSTERY_TRUTH}
    assert by_type["timeline"] == {kinds.KIND_TIMELINE_EVENT}
    assert by_type["outline"] == {kinds.KIND_CHAPTER}

"""背景装配（V03-4.2）：层级顺序、预算裁剪、临时原文与来源可追。

纯函数测试：不建运行时、不读写仓库，直接给材料断言包的内容。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from hastings.application.contexts.background import (
    MODE_OMITTED,
    MODE_SUMMARY,
    MODE_TEMPORARY,
    TIER_AGREEMENTS,
    TIER_CORE,
    TIER_INDEX,
    TIER_OVERVIEW,
    TIER_TARGET,
    AgreementInput,
    BackgroundRequest,
    ChapterInput,
    LedgerInput,
    assemble_background,
)

BIG = 10**6


def _agreement(
    agreement_id: str = "art_a1",
    *,
    scope: str = "all",
    topic: str = "narrative_person",
    statement: str = "全书第一人称",
) -> AgreementInput:
    return AgreementInput(
        agreement_id=agreement_id,
        revision_id=f"rev_{agreement_id}",
        topic=topic,
        scope=scope,
        statement=statement,
    )


def _ledger(
    artifact_id: str = "art_c1",
    *,
    kind: str = "story.character",
    label: str = "人物",
    title: str = "姐姐",
    summary: str = "旅馆经营者，寡言",
) -> LedgerInput:
    return LedgerInput(
        artifact_id=artifact_id,
        revision_id=f"rev_{artifact_id}",
        kind=kind,
        kind_label=label,
        title=title,
        summary=summary,
    )


def _chapter(
    chapter_id: str = "art_ch1",
    *,
    order: str = "0001",
    title: str = "第一章 雪夜",
    status: str = "fresh",
    summary: str = "侦探在雪夜抵达旅馆",
    temporary: str = "",
) -> ChapterInput:
    return ChapterInput(
        chapter_id=chapter_id,
        revision_id=f"rev_{chapter_id}",
        title=title,
        order_key=order,
        summary_id=f"sum_{chapter_id}",
        summary_status=status,
        summary_text=summary,
        summary_source_hash=f"sha_{chapter_id}",
        temporary_text=temporary,
    )


def _request(**overrides: object) -> BackgroundRequest:
    payload: dict[str, object] = {
        "project_id": "prj_demo",
        "commit_id": "cmt_1",
        "scope": "manuscript",
        "budget_tokens": BIG,
        "agreements": (_agreement(),),
        "ledger": (
            _ledger(
                "art_premise",
                kind="story.premise",
                label="故事前提",
                title="雪山旅馆",
                summary="暴雪封路的旅馆里发生命案",
            ),
            _ledger(),
        ),
        "chapters": (_chapter(), _chapter("art_ch2", order="0002", title="第二章 血迹")),
        "target_chapter_ids": ("art_ch1",),
    }
    payload.update(overrides)
    return BackgroundRequest(**payload)  # type: ignore[arg-type]


def test_tiers_follow_priority_and_every_tier_is_present_when_budget_is_roomy() -> None:
    package = assemble_background(_request())
    tiers = [item.tier for item in package.items]
    assert tiers[0] == TIER_AGREEMENTS
    assert TIER_CORE in tiers and TIER_OVERVIEW in tiers
    assert TIER_INDEX in tiers and TIER_TARGET in tiers
    # 顺序必须与优先级一致：先约定，最后才是目标展开。
    assert tiers == sorted(
        tiers,
        key=lambda t: [
            TIER_AGREEMENTS,
            TIER_CORE,
            TIER_OVERVIEW,
            TIER_INDEX,
            TIER_TARGET,
        ].index(t),
    )
    assert package.omissions == ()


def test_scope_filters_agreements_without_touching_other_material() -> None:
    request = _request(
        scope="discussion",
        agreements=(
            _agreement(),
            _agreement("art_a2", scope="manuscript", topic="tone", statement="正文避免抒情"),
        ),
    )
    package = assemble_background(request)
    statements = [item.text for item in package.of_tier(TIER_AGREEMENTS)]
    assert statements == ["全书第一人称"]

    manuscript = assemble_background(replace(request, scope="manuscript"))
    assert len(manuscript.of_tier(TIER_AGREEMENTS)) == 2


def test_over_budget_drops_from_the_back_and_records_why() -> None:
    mandatory = assemble_background(
        _request(
            ledger=(
                _ledger(
                    "art_premise",
                    kind="story.premise",
                    label="故事前提",
                    title="雪山旅馆",
                    summary="暴雪封路的旅馆里发生命案",
                ),
            ),
            chapters=(),
            target_chapter_ids=(),
        )
    )
    floor = mandatory.estimated_tokens

    counts = []
    for budget in (floor, floor + 200, floor + 800, floor + 2000, BIG):
        package = assemble_background(_request(budget_tokens=budget))
        counts.append(len(package.items))
        tiers = {item.tier for item in package.items}
        assert TIER_AGREEMENTS in tiers and TIER_CORE in tiers
        assert all(o.reason for o in package.omissions)
        # 目标展开在目录之后：目录都没进来时，目标不可能先进来。
        if TIER_TARGET in tiers:
            assert TIER_INDEX in tiers
    assert counts == sorted(counts)


def test_mandatory_tier_that_cannot_fit_is_an_error_not_a_silent_drop() -> None:
    with pytest.raises(ValueError, match="不足以保留"):
        assemble_background(_request(budget_tokens=60))


def test_stale_summary_is_replaced_by_temporary_text_and_marked() -> None:
    package = assemble_background(
        _request(
            chapters=(
                _chapter(status="stale", summary="旧摘要", temporary="改过之后的正文"),
                _chapter("art_ch2", order="0002", title="第二章 血迹"),
            ),
        )
    )
    target = package.of_tier(TIER_TARGET)
    assert len(target) == 1
    assert target[0].text == "改过之后的正文"
    assert target[0].temporary is True
    assert package.coverage_of("art_ch1") is not None
    assert package.coverage_of("art_ch1").mode == MODE_TEMPORARY  # type: ignore[union-attr]
    # 过期摘要不得混进概览（非目标章才用概览，这里同时确认它没被当成 fresh）。
    assert all(item.text != "旧摘要" for item in package.items)


def test_missing_summary_without_text_is_reported_as_omitted() -> None:
    package = assemble_background(
        _request(
            chapters=(
                _chapter(status="missing", summary=""),
                _chapter(
                    "art_ch2", order="0002", title="第二章 血迹", status="missing", summary=""
                ),
            ),
        )
    )
    assert package.of_tier(TIER_TARGET) == ()
    coverage = package.coverage_of("art_ch1")
    assert coverage is not None and coverage.mode == MODE_OMITTED
    assert "missing" in coverage.source


def test_fresh_summary_is_used_for_target_chapter() -> None:
    package = assemble_background(_request())
    target = package.of_tier(TIER_TARGET)
    assert len(target) == 1
    assert target[0].kind == "chapter_summary"
    assert target[0].temporary is False
    assert package.coverage_of("art_ch1").mode == MODE_SUMMARY  # type: ignore[union-attr]


def test_versions_record_commit_agreements_and_summary_states() -> None:
    package = assemble_background(_request())
    versions = package.versions
    assert versions["commit_id"] == "cmt_1"
    assert versions["agreements"] == [{"agreement_id": "art_a1", "revision_id": "rev_art_a1"}]
    assert versions["summaries"]["art_ch1"]["status"] == "fresh"
    assert versions["summaries"]["art_ch1"]["source_hash"] == "sha_art_ch1"


def test_same_material_in_different_order_produces_the_same_package() -> None:
    request = _request(
        ledger=(
            _ledger(
                "art_premise",
                kind="story.premise",
                label="故事前提",
                title="雪山旅馆",
                summary="凶案",
            ),
            _ledger("art_c2", title="侦探", summary="外来者"),
            _ledger(),
        ),
        chapters=(
            _chapter("art_ch2", order="0002", title="第二章 血迹"),
            _chapter(),
        ),
    )
    reordered = _request(
        ledger=tuple(reversed(request.ledger)),
        chapters=tuple(reversed(request.chapters)),
        agreements=tuple(reversed(request.agreements)),
    )
    first = assemble_background(request)
    second = assemble_background(reordered)
    assert first.content_hash == second.content_hash
    assert [item.ref_id for item in first.items] == [item.ref_id for item in second.items]


def test_author_truth_is_labelled_so_polishing_cannot_leak_it() -> None:
    package = assemble_background(
        _request(
            ledger=(
                _ledger(
                    "art_truth",
                    kind="story.mystery_truth",
                    label="真相",
                    title="怀表",
                    summary="凶手是姐姐",
                ),
                _ledger(),
            )
        )
    )
    truth = next(item for item in package.of_tier(TIER_CORE) if item.ref_id == "art_truth")
    assert "勿提前披露" in truth.label


def test_render_is_deterministic_and_marks_temporary_text() -> None:
    request = _request(
        chapters=(
            _chapter(status="stale", summary="旧摘要", temporary="改过之后的正文"),
            _chapter("art_ch2", order="0002", title="第二章 血迹"),
        ),
    )
    first = assemble_background(request)
    second = assemble_background(request)
    assert first.render() == second.render()
    text = first.render()
    assert "## 本书约定" in text
    assert "本次临时读取，未写入正式摘要" in text
    assert "## 目录" in text


def test_omissions_are_visible_in_render() -> None:
    mandatory = assemble_background(
        _request(
            ledger=(
                _ledger(
                    "art_premise",
                    kind="story.premise",
                    label="故事前提",
                    title="雪山旅馆",
                    summary="凶案",
                ),
            ),
            chapters=(),
            target_chapter_ids=(),
        )
    )
    package = assemble_background(_request(budget_tokens=mandatory.estimated_tokens + 50))
    assert package.omissions
    assert "未载入" in package.render()

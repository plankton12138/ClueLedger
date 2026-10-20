"""披露边界（V03-4.7）：位置、三个通道、保守缺省与扩大披露检测。

这里的目录是手搭的：真实项目里叙事结构与稿件章分开存放，用接口造数据会绕一大圈，
而边界本身是纯函数——手搭目录恰好能精确控制「第几章在哪里揭示」。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hastings.application.contexts.background import (
    BackgroundRequest,
    BoundaryInput,
    assemble_background,
)
from hastings.domain.story import disclosure as domain
from hastings.domain.story import kinds
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.server.v2 import V2Runtime, build_v2_app

WATCH = "怀表早已损坏"
LETTER = "姐姐留下一封信"


def _entry(artifact_id: str, kind: str, **content: object) -> StoryEntry:
    payload: dict[str, object] = dict(content)
    if kind == kinds.KIND_BEAT:
        payload["beat_id"] = artifact_id
    elif kind in kinds.NARRATIVE_NODE_KINDS:
        payload["node_id"] = artifact_id
        payload["kind"] = kind
    return StoryEntry(
        artifact_id=artifact_id,
        kind=kind,
        revision_id=f"rev_{artifact_id}",
        content=payload,
    )


def _catalog() -> StoryCatalog:
    """第一章没有披露；第二章才揭示信、同时林昭知道怀表坏了。"""
    return StoryCatalog(
        commit_id="cmt_1",
        entries=(
            _entry("arc_1", kinds.KIND_ARC, title="主线", order_key="010"),
            _entry(
                "ch_1", kinds.KIND_CHAPTER, title="第一章 雪夜", order_key="010", parent_id="arc_1"
            ),
            _entry(
                "ch_2", kinds.KIND_CHAPTER, title="第二章 门", order_key="020", parent_id="arc_1"
            ),
            _entry("bt_1", kinds.KIND_BEAT, title="抵达", order_key="010", parent_id="ch_1"),
            _entry("bt_2", kinds.KIND_BEAT, title="开门", order_key="010", parent_id="ch_2"),
            _entry("tf_watch", kinds.KIND_TRUTH_FACT, statement=WATCH),
            _entry("tf_letter", kinds.KIND_TRUTH_FACT, statement=LETTER),
            _entry(
                "dsc_1",
                kinds.KIND_DISCLOSURE,
                fact_id="tf_letter",
                audience="reader",
                at_beat_id="bt_2",
                interpretation="true",
            ),
            _entry(
                "knw_1",
                kinds.KIND_KNOWLEDGE,
                character_id="chr_lin",
                fact_id="tf_watch",
                mode="knows",
                at_beat_id="bt_2",
            ),
        ),
    )


def _status(boundary: domain.DisclosureBoundary, fact_id: str) -> str:
    return next(fact.status for fact in boundary.facts if fact.fact_id == fact_id)


# ---------------------------------------------------------------------------
# 位置与通道
# ---------------------------------------------------------------------------


def test_first_chapter_sees_nothing_disclosed() -> None:
    """第一章：信与怀表都还没有揭示，只能作为作者资料存在。"""
    boundary = domain.boundary_at(_catalog(), chapter_id="ch_1")
    assert boundary.position.cutoff_beat_id == "bt_1"
    assert _status(boundary, "tf_letter") == domain.BOUNDARY_AUTHOR_ONLY
    assert _status(boundary, "tf_watch") == domain.BOUNDARY_AUTHOR_ONLY
    # 后文位置要能说清「首次揭示于第二章」，否则作者不知道这个约束从哪来。
    letter = next(f for f in boundary.facts if f.fact_id == "tf_letter")
    assert letter.first_reveal_at == "第二章 门"


def test_second_chapter_splits_reader_and_character_channels() -> None:
    """读者披露与角色知情是两个通道：信给读者，怀表只给林昭。"""
    boundary = domain.boundary_at(_catalog(), chapter_id="ch_2")
    assert _status(boundary, "tf_letter") == domain.BOUNDARY_READER
    assert _status(boundary, "tf_watch") == domain.BOUNDARY_CHARACTER
    watch = next(f for f in boundary.facts if f.fact_id == "tf_watch")
    assert watch.characters == ("chr_lin",)
    assert boundary.of_status(domain.BOUNDARY_READER)[0].interpretation == "true"


def test_unverified_overrides_every_other_channel() -> None:
    """证据待核对优先于「已知」：旧记录不能证明已经获知（I09 / A16）。"""
    boundary = domain.boundary_at(
        _catalog(), chapter_id="ch_2", unverified={"tf_letter": "来源证据待核对：ch_9"}
    )
    assert _status(boundary, "tf_letter") == domain.BOUNDARY_UNVERIFIED
    assert "ch_9" in next(f for f in boundary.facts if f.fact_id == "tf_letter").reason


def test_structural_order_beats_chapter_numbers() -> None:
    """倒叙：读者按结构顺序读，不按章号读。

    回忆章标题是「第十二章」却排在前面；「第五章 现在」排在它之后。按章号比较
    会得出「第五章在第十二章之前，不可能知情」，按阅读顺序则正好相反。
    """
    catalog = StoryCatalog(
        commit_id="cmt_2",
        entries=(
            _entry("arc_1", kinds.KIND_ARC, title="主线", order_key="010"),
            _entry(
                "ch_0",
                kinds.KIND_CHAPTER,
                title="第一章 引子",
                order_key="010",
                parent_id="arc_1",
            ),
            _entry(
                "ch_a",
                kinds.KIND_CHAPTER,
                title="第十二章 回忆",
                order_key="020",
                parent_id="arc_1",
            ),
            _entry(
                "ch_b",
                kinds.KIND_CHAPTER,
                title="第五章 现在",
                order_key="030",
                parent_id="arc_1",
            ),
            _entry("bt_0", kinds.KIND_BEAT, title="引子", order_key="010", parent_id="ch_0"),
            _entry("bt_a", kinds.KIND_BEAT, title="回忆里", order_key="010", parent_id="ch_a"),
            _entry("bt_b", kinds.KIND_BEAT, title="现在", order_key="010", parent_id="ch_b"),
            _entry("tf_1", kinds.KIND_TRUTH_FACT, statement=WATCH),
            _entry(
                "dsc_1",
                kinds.KIND_DISCLOSURE,
                fact_id="tf_1",
                audience="reader",
                at_beat_id="bt_a",
                interpretation="true",
            ),
        ),
    )
    assert _status(domain.boundary_at(catalog, chapter_id="ch_0"), "tf_1") == (
        domain.BOUNDARY_AUTHOR_ONLY
    )
    # 章号更小，但阅读顺序在后：这里读者已经知道了。
    assert _status(domain.boundary_at(catalog, chapter_id="ch_b"), "tf_1") == (
        domain.BOUNDARY_READER
    )


def test_beat_position_uses_only_earlier_beats() -> None:
    """精确到节拍时，边界只看这个节拍（含）之前。"""
    boundary = domain.boundary_at(_catalog(), beat_id="bt_1")
    assert boundary.position.kind == domain.POSITION_BEAT
    assert _status(boundary, "tf_letter") == domain.BOUNDARY_AUTHOR_ONLY


def test_unknown_position_is_conservative() -> None:
    """没有位置就不声称任何事实已披露，而不是默认「都知道了」。"""
    boundary = domain.boundary_at(_catalog())
    assert boundary.position.kind == domain.POSITION_UNKNOWN
    assert boundary.of_status(domain.BOUNDARY_READER) == ()
    assert all(f.status == domain.BOUNDARY_AUTHOR_ONLY for f in boundary.facts)
    assert any("未指定叙事位置" in warning for warning in boundary.warnings)


def test_chapter_without_beats_warns_instead_of_guessing() -> None:
    catalog = StoryCatalog(
        commit_id="cmt_3",
        entries=(
            _entry("ch_empty", kinds.KIND_CHAPTER, title="空章", order_key="010"),
            _entry("tf_1", kinds.KIND_TRUTH_FACT, statement=WATCH),
        ),
    )
    boundary = domain.boundary_at(catalog, chapter_id="ch_empty")
    assert boundary.position.cutoff_beat_id is None
    assert any("没有节拍" in warning for warning in boundary.warnings)
    assert _status(boundary, "tf_1") == domain.BOUNDARY_AUTHOR_ONLY


def test_render_is_deterministic_and_names_the_constraint() -> None:
    catalog = _catalog()
    first = domain.boundary_at(catalog, chapter_id="ch_1")
    second = domain.boundary_at(catalog, chapter_id="ch_1")
    assert first.render() == second.render()
    assert first.content_hash == second.content_hash
    assert "不得在本章" in first.render()
    # 换个位置必须换指纹：否则缓存会拿第一章的边界去算第二章。
    assert domain.boundary_at(catalog, chapter_id="ch_2").content_hash != first.content_hash


# ---------------------------------------------------------------------------
# 扩大披露检测
# ---------------------------------------------------------------------------


def test_new_hidden_fact_name_in_candidate_is_blocked() -> None:
    boundary = domain.boundary_at(_catalog(), chapter_id="ch_1")
    report = domain.check_expansion(
        boundary,
        original="侦探在雪夜抵达旅馆，前台没有人。",
        candidate="侦探在雪夜抵达旅馆，前台没有人，只有一块怀表早已损坏。",
    )
    assert report.expands
    assert report.blocked
    assert [finding.label for finding in report.findings] == [WATCH]


def test_fact_already_in_original_is_not_an_expansion() -> None:
    """基线是原文：作者原来写过，就不算润色扩大披露。"""
    boundary = domain.boundary_at(_catalog(), chapter_id="ch_1")
    report = domain.check_expansion(
        boundary,
        original="桌上有一块怀表早已损坏。",
        candidate="桌上摆着一块怀表早已损坏，指针停在三点。",
    )
    assert not report.expands
    assert not report.blocked


def test_authorization_unblocks_but_still_reports() -> None:
    boundary = domain.boundary_at(_catalog(), chapter_id="ch_1")
    report = domain.check_expansion(
        boundary,
        original="雪夜。",
        candidate="雪夜。她说姐姐留下一封信。",
        authorized=True,
    )
    assert report.expands
    assert not report.blocked
    assert report.authorized


def test_long_statements_are_skipped_not_silently_passed() -> None:
    """字符串级检测只认可命名的短标签；跳过了多少要如实报出来。"""
    catalog = StoryCatalog(
        commit_id="cmt_4",
        entries=(
            _entry("ch_1", kinds.KIND_CHAPTER, title="第一章", order_key="010"),
            _entry("bt_1", kinds.KIND_BEAT, title="一", order_key="010", parent_id="ch_1"),
            _entry(
                "clue_long",
                kinds.KIND_CLUE,
                statement="雪夜旅馆的前台登记簿被人撕掉了整整一页，撕口很新，纸边还沾着雪水",
            ),
        ),
    )
    boundary = domain.boundary_at(catalog, chapter_id="ch_1")
    report = domain.check_expansion(
        boundary,
        original="雪夜。",
        candidate="雪夜，登记簿被撕掉一页。",
    )
    assert report.skipped_terms == 1
    assert report.checked_terms == 0
    assert not report.expands


def test_unknown_position_reports_but_does_not_block() -> None:
    """没有叙事位置时只能提示，不能替作者裁决——否则每次润色都会被拦住。"""
    boundary = domain.boundary_at(_catalog())
    report = domain.check_expansion(
        boundary,
        original="雪夜。",
        candidate="雪夜，怀表早已损坏。",
    )
    assert report.expands
    assert not report.position_known
    assert not report.blocked


# ---------------------------------------------------------------------------
# 接口
# ---------------------------------------------------------------------------


def _client() -> tuple[TestClient, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "披露边界"}).json()["project_id"]
    return client, pid


def test_api_returns_conservative_boundary_without_position() -> None:
    client, pid = _client()
    payload = client.get(f"/api/v2/projects/{pid}/disclosure-boundary").json()
    assert payload["position"]["kind"] == "unknown"
    assert payload["reader_known"] == []
    assert payload["warnings"]
    assert "未指定叙事位置" in payload["text"]


def test_api_rejects_unknown_story_chapter() -> None:
    client, pid = _client()
    response = client.get(f"/api/v2/projects/{pid}/disclosure-boundary?chapter_id=nope")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"


def test_api_check_endpoint_reports_without_position() -> None:
    client, pid = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/disclosure-boundary/check",
        json={"original": "雪夜。", "candidate": "雪夜，怀表早已损坏。"},
    )
    assert response.status_code == 200
    assert response.json()["position_known"] is False
    assert response.json()["blocked"] is False


def test_capabilities_publish_boundary_contract() -> None:
    client, _ = _client()
    features = client.get("/api/v2/capabilities").json()["features"]
    assert features["background"]["tiers"][1] == "boundary"
    assert features["disclosure_boundary"]["expansion_check"] == "name_level"
    assert features["disclosure_boundary"]["positions"] == ["chapter", "beat", "unknown"]


def test_background_includes_boundary_when_position_given() -> None:
    """边界是背景的第二层：约定之后、核心之前。"""
    package = assemble_background(
        BackgroundRequest(
            project_id="p",
            commit_id="c",
            scope="manuscript",
            budget_tokens=2_000,
            boundary=BoundaryInput(position_label="第一章", text="- 不得在本章断言：" + WATCH),
        )
    )
    tiers = [item.tier for item in package.items]
    assert tiers == ["boundary"]
    assert WATCH in package.render()


def test_background_rejects_unknown_story_chapter() -> None:
    client, pid = _client()
    # 先有正文才有 head：空作品根本走不到算边界那一步。
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp-1", "title": "第一章", "text": "雪夜。"},
    )
    response = client.get(
        f"/api/v2/projects/{pid}/context/background?scope=manuscript&story_chapter_id=nope"
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "STORY_UNKNOWN_ARTIFACT"


# ---------------------------------------------------------------------------
# 采用门槛（改写现有章）
# ---------------------------------------------------------------------------


def _runtime_with_writer(text: str) -> V2Runtime:
    from hastings.application.services.drafting_service import WrittenChapter

    runtime = V2Runtime.memory()

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        return WrittenChapter(text=text, model="test-model")

    runtime.drafting._writer = writer  # type: ignore[assignment]
    return runtime


def _imported_chapter(client: TestClient, pid: str) -> str:
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp-1", "title": "第一章 雪夜", "text": "雪夜，侦探抵达旅馆。"},
    )
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"][0]["chapter_id"]


def _blocked_report() -> dict[str, object]:
    return {
        "blocked": True,
        "expands": True,
        "findings": [{"fact_id": "tf_watch", "label": WATCH, "status": "author_only"}],
        "position": {"kind": "chapter", "id": "ch_1", "label": "第一章", "cutoff_beat_id": "bt_1"},
    }


def test_adopt_requires_authorization_when_disclosure_expands() -> None:
    """改写现有章：新稿提前写出后文真相时，采用必须被挡住（A16）。"""
    runtime = _runtime_with_writer("雪夜。侦探一眼看出怀表早已损坏。")
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "润色"}).json()["project_id"]
    chapter_id = _imported_chapter(client, pid)
    # 叙事结构由测试替身给出：真实项目要等结构树建立后才有章 ID。
    runtime.background.render = lambda *a, **k: ""  # type: ignore[assignment]
    runtime.disclosure.check = lambda *a, **k: _blocked_report()  # type: ignore[assignment]

    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={
            "target": chapter_id,
            "instruction": "润色这一章，让气氛更紧",
            "story_chapter_id": "ch_1",
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DISCLOSURE_NEEDS_AUTHORIZATION"


def test_adopt_passes_when_author_authorizes_the_change() -> None:
    runtime = _runtime_with_writer("雪夜。侦探一眼看出怀表早已损坏。")
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "授权"}).json()["project_id"]
    chapter_id = _imported_chapter(client, pid)
    runtime.background.render = lambda *a, **k: ""  # type: ignore[assignment]
    seen: dict[str, object] = {}

    def check(project_id: str, **kwargs: object) -> dict[str, object]:
        seen.update(kwargs)
        authorized = bool(kwargs.get("authorized"))
        return {**_blocked_report(), "blocked": not authorized, "authorized": authorized}

    runtime.disclosure.check = check  # type: ignore[assignment]
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={
            "target": chapter_id,
            "instruction": "这一章提前点破怀表",
            "story_chapter_id": "ch_1",
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"], "allow_disclosure_change": True},
    )
    assert response.status_code == 200, response.text
    assert seen["authorized"] is True
    # 授权只是不拦，不改变落盘结果：正文照常提交。
    assert response.json()["status"] == "committed"


def test_adopt_skips_check_without_story_position() -> None:
    """没有叙事位置就不猜：宁可不拦，也不要拿臆测的位置当门槛。"""
    runtime = _runtime_with_writer("雪夜。")
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "无位置"}).json()["project_id"]
    chapter_id = _imported_chapter(client, pid)
    runtime.background.render = lambda *a, **k: ""  # type: ignore[assignment]
    calls: list[object] = []
    runtime.disclosure.check = lambda *a, **k: calls.append(1)  # type: ignore[assignment]
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"target": chapter_id, "instruction": "润色"},
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert response.status_code == 200, response.text
    assert calls == []

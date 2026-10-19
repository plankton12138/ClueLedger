"""V03-3.3/3.4/3.5：从账本与作者要求试写，只读预览，按预览哈希采用。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from hastings.application.services.drafting_service import WrittenChapter
from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雨夜

侦探在雨夜抵达旅馆。

## 第二章 空房

二楼走廊尽头有一扇没有门牌的门。
"""


DEFAULT_TEXT = "雪夜里，林昭推开旅馆的门。\n\n前台没有人。"


def _runtime(*texts: str) -> V2Runtime:
    """按调用顺序取正文；最后一个文本在后续调用里重复。"""
    runtime = V2Runtime.memory()
    queue = list(texts) or [DEFAULT_TEXT]
    calls: list[dict[str, str]] = []

    async def writer(*, system: str, prompt: str) -> WrittenChapter:
        calls.append({"system": system, "prompt": prompt})
        text = queue.pop(0) if len(queue) > 1 else queue[0]
        return WrittenChapter(text=text, model="test-model")

    runtime.drafting._writer = writer  # type: ignore[assignment]
    runtime.drafting.calls = calls  # type: ignore[attr-defined]
    return runtime


def _client(runtime: V2Runtime) -> tuple[TestClient, str]:
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "试写"}).json()["project_id"]
    return client, pid


def _calls(runtime: V2Runtime) -> list[dict[str, str]]:
    return runtime.drafting.calls  # type: ignore[attr-defined]


def test_empty_project_can_draft_first_chapter() -> None:
    """空项目、无大纲、未定凶手都能试写——这是 V03-3.3 的核心。"""
    runtime = _runtime()
    client, pid = _client(runtime)
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={
            "instruction": "林昭在雪夜住进一家只有姐妹两人打理的旅馆，她不确定谁在说谎。",
            "chapter_title": "雪山旅馆",
            "viewpoint": "林昭第三人称限知",
            "length_target": "2000-3000 字",
            "narrative_boundary": "不揭示凶手是谁",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["status"] == "ready"
    assert body["current_revision"] == 1
    assert body["title"] == "雪山旅馆"
    assert body["text"].startswith("雪夜里")
    assert body["preview_hash"]
    # 提示词带上作者要求与叙事边界；没有节拍字样。
    prompt = _calls(runtime)[0]["prompt"]
    assert "林昭在雪夜住进" in prompt
    assert "不揭示凶手是谁" in prompt
    assert "节拍" not in prompt


def test_ledger_refs_are_rendered_in_prompt_and_missing_ref_is_rejected() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    # 先导入一章，让项目有 head；账本条目通过仓库直接写入成本较高，
    # 这里验证「不存在的引用」被如实拒绝，而不是静默丢掉依据。
    client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "第一段。"},
    )
    rejected = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "继续写", "refs": ["chr_missing"]},
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "DRAFT_TARGET_CONFLICT"
    assert _calls(runtime) == []


def test_create_requires_author_instruction() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    response = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "  "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_EMPTY_RESULT"
    assert _calls(runtime) == []


def test_empty_model_output_is_not_a_candidate() -> None:
    runtime = _runtime("   ")
    client, pid = _client(runtime)
    response = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DRAFT_EMPTY_RESULT"


def test_create_is_idempotent_by_key() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    payload = {"instruction": "写一章", "idempotency_key": "k1"}
    first = client.post(f"/api/v2/projects/{pid}/drafts", json=payload).json()
    second = client.post(f"/api/v2/projects/{pid}/drafts", json=payload).json()
    assert first["candidate_id"] == second["candidate_id"]
    assert len(_calls(runtime)) == 1


def test_adopt_creates_chapter_and_operation_once() -> None:
    """采用 = 一次发布：正式正文可读、操作可查、重试不产生第二个版本。"""
    runtime = _runtime()
    client, pid = _client(runtime)
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "林昭住进旅馆", "chapter_title": "雪山旅馆"},
    ).json()

    adopted = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert adopted.status_code == 200, adopted.text
    result = adopted.json()
    assert result["status"] == "committed"
    assert result["chapter_id"].startswith("ch_")
    assert result["operation_id"]

    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{result['chapter_id']}").json()
    assert chapter["text"].strip() == created["text"].strip()
    assert chapter["title"] == "雪山旅馆"

    operation = client.get(f"/api/v2/projects/{pid}/operations/{result['operation_id']}").json()
    assert operation["status"] == "committed"
    assert result["chapter_id"] in operation["changed_chapter_ids"]

    replay = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    ).json()
    assert replay["status"] == "no_change"
    assert replay["operation_id"] == result["operation_id"]
    assert len(client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]) == 1


def test_preview_hash_mismatch_is_rejected() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    created = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"}).json()
    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": "0" * 64},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_PREVIEW_CONFLICT"


def test_revise_supersedes_previous_revision() -> None:
    """迟到旧候选不能覆盖新稿：旧修订的哈希在推进后不可再采用。"""
    runtime = _runtime("第一版正文。", "第二版正文，更冷一些。")
    client, pid = _client(runtime)
    created = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"}).json()
    old_hash = created["preview_hash"]

    revised = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/revise",
        json={"instruction": "更冷一些", "expected_revision": 1},
    )
    assert revised.status_code == 200, revised.text
    body = revised.json()
    assert body["current_revision"] == 2
    assert body["text"] == "第二版正文，更冷一些。"
    # 提示词带上一稿与本次调整。
    prompt = _calls(runtime)[-1]["prompt"]
    assert "第一版正文" in prompt and "更冷一些" in prompt

    late = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": old_hash, "revision": 1},
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "DRAFT_REVISION_CONFLICT"

    adopted = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": body["preview_hash"]},
    )
    assert adopted.status_code == 200, adopted.text
    chapter_id = adopted.json()["chapter_id"]
    chapter = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()
    assert chapter["text"].strip() == "第二版正文，更冷一些。"


def test_existing_chapter_draft_conflicts_when_text_moved() -> None:
    """改写现有章：试写之后正文被改过，候选不能覆盖新稿。"""
    runtime = _runtime("改写后的正文。")
    client, pid = _client(runtime)
    imported = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "原始正文。"},
    ).json()
    chapter_id = imported["chapter_id"]

    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "改写这一章", "target": chapter_id},
    ).json()
    # 试写期间作者自己改了正文。
    client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter_id}/save",
        json={"idempotency_key": "save1", "full_text": "作者自己改的正文。"},
    )
    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "DRAFT_TARGET_CONFLICT"
    current = client.get(f"/api/v2/projects/{pid}/chapters/{chapter_id}").json()
    assert current["text"].strip() == "作者自己改的正文。"


def test_adopt_is_blocked_by_open_edit_session() -> None:
    runtime = _runtime("候选正文。")
    client, pid = _client(runtime)
    imported = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp1", "title": "第一章", "text": "原始正文。"},
    ).json()
    chapter_id = imported["chapter_id"]
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "改写这一章", "target": chapter_id},
    ).json()
    started = client.post(f"/api/v2/projects/{pid}/edit-sessions")
    assert started.status_code == 200, started.text
    session_id = started.json()["session"]["session_id"]
    saved = client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session_id}/chapters/{chapter_id}",
        json={"expected_save_seq": 0, "request_id": "r1", "text": "副本里的草稿。"},
    )
    assert saved.status_code == 200, saved.text

    response = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EDIT_SESSION_CONFLICT"


def test_discard_keeps_text_untouched() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    created = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"}).json()
    discarded = client.post(f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/discard")
    assert discarded.status_code == 200
    assert discarded.json()["status"] == "discarded"
    assert client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"] == []
    blocked = client.post(
        f"/api/v2/projects/{pid}/drafts/{created['candidate_id']}/adopt",
        json={"preview_hash": created["preview_hash"]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "DRAFT_CANDIDATE_NOT_ADOPTABLE"


def test_list_returns_excerpt_not_full_text() -> None:
    runtime = _runtime("正文" * 200)
    client, pid = _client(runtime)
    client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"})
    listed = client.get(f"/api/v2/projects/{pid}/drafts").json()["candidates"]
    assert len(listed) == 1
    assert len(listed[0]["excerpt"]) == 160
    assert listed[0]["chars"] == 400
    assert "text" not in listed[0]


def test_unknown_candidate_is_404() -> None:
    runtime = _runtime()
    client, pid = _client(runtime)
    response = client.get(f"/api/v2/projects/{pid}/drafts/cnd_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DRAFT_CANDIDATE_NOT_FOUND"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("```markdown\n第一段。\n\n第二段。\n```", "第一段。\n\n第二段。"),
        ("## 第一章 雨夜\n\n正文开始。", "正文开始。"),
        ("正文。\n\n---\n\n说明：这里解释一下写法。", "正文。"),
    ],
)
def test_clean_chapter_text(raw: str, expected: str) -> None:
    from hastings.domain.manuscript.drafting import clean_chapter_text

    assert clean_chapter_text(raw) == expected


def test_late_adjustment_does_not_revive_a_discarded_candidate() -> None:
    """调整生成期间丢弃候选：模型返回后不许把 discarded 写回 ready。

    复现路径：发起调整 → 作者丢弃候选 → 模型姗姗返回。早先落盘时直接覆盖
    存储，迟到的结果把状态写回 ready，作者丢掉的候选又回到待采用列表。
    """
    holder: dict[str, str] = {}
    runtime = _runtime("第一版正文。", "调整后的正文。")
    client, pid = _client(runtime)
    created = client.post(f"/api/v2/projects/{pid}/drafts", json={"instruction": "写一章"}).json()
    holder["candidate_id"] = created["candidate_id"]

    original_writer = runtime.drafting._writer

    async def discarding_writer(*, system: str, prompt: str) -> WrittenChapter:
        # 模型还在跑的时候作者点了丢弃。
        client.post(f"/api/v2/projects/{pid}/drafts/{holder['candidate_id']}/discard")
        return await original_writer(system=system, prompt=prompt)

    runtime.drafting._writer = discarding_writer  # type: ignore[assignment]

    revised = client.post(
        f"/api/v2/projects/{pid}/drafts/{holder['candidate_id']}/revise",
        json={"instruction": "更冷一些", "expected_revision": 1},
    )
    assert revised.status_code == 400, revised.text
    assert revised.json()["error"]["code"] == "DRAFT_CANDIDATE_NOT_ADOPTABLE"

    # 候选仍是 discarded，没有第二修订，也没有被迟到结果复活。
    current = client.get(f"/api/v2/projects/{pid}/drafts/{holder['candidate_id']}").json()
    assert current["status"] == "discarded"
    assert current["current_revision"] == 1
    listed = client.get(f"/api/v2/projects/{pid}/drafts").json()
    assert [row["status"] for row in listed["candidates"]] == ["discarded"]


def test_rewrite_existing_chapter_includes_original_text_without_pasting() -> None:
    """改写现有章：模型必须看到该章正文，作者不需要把原文粘进要求。

    这是 V03-8.7 `--deep` 走查暴露的缺口——此前 `previous_text` 恒为空、
    背景也不展开目标章，模型只看得到章 ID，等于「另写一版」。
    """
    runtime = _runtime()
    client, pid = _client(runtime)
    original = "第一段：钟停在九点。\n\n第二段：登记簿最后一行是林雪的名字。"
    imported = client.post(
        f"/api/v2/projects/{pid}/chapters/import",
        json={"idempotency_key": "imp-rewrite", "title": "第一章 九点的钟声", "text": original},
    ).json()

    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={
            "target": imported["chapter_id"],
            "instruction": "把这一版改得更克制，删掉解释性句子，保留全部事实。",
        },
    )
    assert created.status_code == 200, created.text
    prompt = _calls(runtime)[0]["prompt"]
    # 原文两段都在提示词里，且落在「现有正文」段（不是靠作者粘贴）。
    assert "钟停在九点" in prompt
    assert "登记簿最后一行是林雪的名字" in prompt
    assert "现有正文" in prompt
    # 作者要求保持原样，没有被塞进原文。
    assert "把这一版改得更克制" in prompt
    assert prompt.count("钟停在九点") == 1


def test_first_chapter_draft_has_no_existing_text_section() -> None:
    """新章试写不该凭空出现「现有正文」：没有原文就不伪造一段。"""
    runtime = _runtime()
    client, pid = _client(runtime)
    created = client.post(
        f"/api/v2/projects/{pid}/drafts",
        json={"instruction": "写第一章：林昭在雪夜抵达旅馆。"},
    )
    assert created.status_code == 200, created.text
    prompt = _calls(runtime)[0]["prompt"]
    assert "现有正文" not in prompt

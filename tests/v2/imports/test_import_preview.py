"""正文导入预览与确认（V03-2.3，计划 M06）。

契约：上传只解析并生成预览，不写正式正文；确认整批走统一发布（一个
CommitRequest，失败无半份正文）；同名/同内容不覆盖，重复内容必须显式选择；
重试不重复建章；预览 24 小时过期。

覆盖：Markdown/章节行/无标题三种解析、预览不落正文、确认后文本等于预览、
同内容去重决定（取消/新增副本/替换）、替换需明确目标与基础版本、重试幂等、
格式/编码/章数上限、预览过期。
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from hastings.application.errors import ViolationError
from hastings.application.services.import_service import ImportService
from hastings.interfaces.importers.manuscript_text import parse_manuscript_text
from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雨夜

侦探在雨夜抵达旅馆。

前台声称只有一位客人。

## 第二章 清晨

雪停了。

老板娘在擦杯子。
"""

CHAPTER_LINES = """第一章 雨夜

侦探在雨夜抵达旅馆。

第二章 清晨

雪停了。
"""

PLAIN = """只有一段正文，没有章节标题。

第二段仍然属于同一章。
"""


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _client() -> tuple[TestClient, V2Runtime, str]:
    runtime = V2Runtime.memory()
    client = TestClient(build_v2_app(runtime))
    pid = client.post("/api/v2/projects", json={"name": "导入走查"}).json()["project_id"]
    return client, runtime, pid


def _preview(client: TestClient, pid: str, text: str = MD, filename: str = "雪山旅馆.md"):
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={"filename": filename, "content_base64": _b64(text)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _confirm(client: TestClient, pid: str, preview: dict, decision: str | None = None):
    body: dict[str, object] = {"preview_hash": preview["preview_hash"]}
    if decision is not None:
        body["duplicate_decision"] = decision
    return client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json=body,
    )


def _chapters(client: TestClient, pid: str) -> list[dict]:
    response = client.get(f"/api/v2/projects/{pid}/manuscript")
    assert response.status_code == 200, response.text
    return response.json()["chapters"]


# -- 解析 -------------------------------------------------------------------


def test_parse_markdown_uses_section_headings() -> None:
    parsed = parse_manuscript_text(MD, filename="雪山旅馆.md")
    assert [c.title for c in parsed.chapters] == ["第一章 雨夜", "第二章 清晨"]
    assert "侦探在雨夜抵达旅馆。" in parsed.chapters[0].text
    assert parsed.format == "markdown"
    assert parsed.chapters[0].order_key == "0001"


def test_parse_chapter_lines_without_markdown() -> None:
    parsed = parse_manuscript_text(CHAPTER_LINES, filename="旧稿.txt")
    assert [c.title for c in parsed.chapters] == ["第一章 雨夜", "第二章 清晨"]
    assert parsed.format == "text"


def test_parse_without_headings_is_one_chapter_named_by_file() -> None:
    parsed = parse_manuscript_text(PLAIN, filename="零散正文.txt")
    assert len(parsed.chapters) == 1
    assert parsed.chapters[0].title == "零散正文"
    assert parsed.chapters[0].text == PLAIN.strip()


def test_book_title_heading_is_not_a_chapter() -> None:
    parsed = parse_manuscript_text(MD, filename="雪山旅馆.md")
    assert all(c.title != "雪山旅馆" for c in parsed.chapters)


# -- 预览不写正文 -----------------------------------------------------------


def test_preview_returns_full_text_and_writes_nothing() -> None:
    client, _runtime, pid = _client()
    preview = _preview(client, pid)
    assert len(preview["chapters"]) == 2
    assert preview["chapters"][0]["text"].startswith("侦探在雨夜抵达旅馆。")
    assert preview["chapters"][0]["content_hash"]
    assert preview["preview_hash"]
    assert preview["duplicates"] == []
    assert _chapters(client, pid) == []


# -- 确认：实际文本等于预览 -------------------------------------------------


def test_confirm_appends_chapters_with_preview_text() -> None:
    client, runtime, pid = _client()
    preview = _preview(client, pid)
    response = _confirm(client, pid, preview)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "committed"
    assert len(result["chapter_ids"]) == 2

    chapters = _chapters(client, pid)
    assert [c["title"] for c in chapters] == ["第一章 雨夜", "第二章 清晨"]
    first = client.get(f"/api/v2/projects/{pid}/chapters/{chapters[0]['chapter_id']}").json()
    assert first["text"].strip() == preview["chapters"][0]["text"].strip()

    operation = client.get(f"/api/v2/projects/{pid}/operations/{result['operation_id']}").json()
    assert operation["commit_id"] == result["commit_id"]
    assert set(operation["changed_chapter_ids"]) == set(result["chapter_ids"])


def test_confirm_is_idempotent_on_retry() -> None:
    client, _runtime, pid = _client()
    preview = _preview(client, pid)
    first = _confirm(client, pid, preview).json()
    second = _confirm(client, pid, preview)
    assert second.status_code == 200, second.text
    assert second.json()["operation_id"] == first["operation_id"]
    assert second.json()["commit_id"] == first["commit_id"]
    assert len(_chapters(client, pid)) == 2


# -- 重复内容 ---------------------------------------------------------------


def test_same_content_requires_explicit_duplicate_decision() -> None:
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))

    preview = _preview(client, pid)
    assert {d["chapter_id"] for d in preview["duplicates"]} == {
        c["chapter_id"] for c in _chapters(client, pid)
    }

    blocked = _confirm(client, pid, preview)
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "IMPORT_DUPLICATE_DECISION_REQUIRED"

    cancelled = _confirm(client, pid, preview, decision="cancel")
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"
    assert len(_chapters(client, pid)) == 2


def test_duplicate_append_creates_copy() -> None:
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    response = _confirm(client, pid, _preview(client, pid), decision="append")
    assert response.status_code == 200, response.text
    chapters = _chapters(client, pid)
    assert len(chapters) == 4
    # 同名不覆盖：标题重复但身份不同
    assert chapters[0]["title"] == chapters[2]["title"]
    assert chapters[0]["chapter_id"] != chapters[2]["chapter_id"]


def test_replace_requires_target_and_current_base() -> None:
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    chapters = _chapters(client, pid)
    target = chapters[0]
    bases = {
        chapter["chapter_id"]: client.get(
            f"/api/v2/projects/{pid}/chapters/{chapter['chapter_id']}"
        ).json()["chapter_base"]
        for chapter in chapters
    }

    preview = _preview(client, pid)
    no_target = _confirm(client, pid, preview, decision="replace")
    assert no_target.status_code == 400
    assert no_target.json()["error"]["code"] == "IMPORT_REPLACEMENT_REQUIRED"

    stale = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": preview["revision"],
            "mode": "replace",
            "replacements": [
                {"chapter_id": target["chapter_id"], "expected_chapter_base": "stale"}
            ],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MANUSCRIPT_BASE_CONFLICT"

    # 只给一个目标：预览接受（逐章调整），确认时因另一个重复章缺目标而拒绝
    partial = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": preview["revision"],
            "mode": "replace",
            "replacements": [
                {
                    "chapter_id": target["chapter_id"],
                    "expected_chapter_base": bases[target["chapter_id"]],
                }
            ],
        },
    )
    assert partial.status_code == 200, partial.text
    incomplete = _confirm(client, pid, partial.json(), decision="replace")
    assert incomplete.status_code == 400
    assert incomplete.json()["error"]["code"] == "IMPORT_REPLACEMENT_REQUIRED"

    updated = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": partial.json()["revision"],
            "mode": "replace",
            "replacements": [
                {"chapter_id": chapter_id, "expected_chapter_base": base}
                for chapter_id, base in bases.items()
            ],
        },
    )
    assert updated.status_code == 200, updated.text
    replaced = updated.json()

    result = _confirm(client, pid, replaced, decision="replace")
    assert result.status_code == 200, result.text
    assert len(_chapters(client, pid)) == 2
    first = client.get(f"/api/v2/projects/{pid}/chapters/{target['chapter_id']}").json()
    assert first["text"].strip() == replaced["chapters"][0]["text"].strip()


def test_duplicate_targets_in_one_batch_are_rejected() -> None:
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    chapters = _chapters(client, pid)
    base = client.get(f"/api/v2/projects/{pid}/chapters/{chapters[0]['chapter_id']}").json()[
        "chapter_base"
    ]
    preview = _preview(client, pid)
    response = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": preview["revision"],
            "mode": "replace",
            "replacements": [
                {"chapter_id": chapters[0]["chapter_id"], "expected_chapter_base": base},
                {"chapter_id": chapters[0]["chapter_id"], "expected_chapter_base": base},
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_DUPLICATE_TARGET"


def test_same_title_different_content_appends_without_overwrite() -> None:
    """同名不等于同内容：标题相同但正文不同，默认新增，不覆盖既有章。"""
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    other = MD.replace("侦探在雨夜抵达旅馆。", "侦探在清晨抵达旅馆。").replace(
        "雪停了。", "雨还在下。"
    )
    preview = _preview(client, pid, text=other)
    assert preview["duplicates"] == []
    result = _confirm(client, pid, preview)
    assert result.status_code == 200, result.text
    chapters = _chapters(client, pid)
    assert len(chapters) == 4
    assert chapters[0]["title"] == chapters[2]["title"]
    assert chapters[0]["chapter_id"] != chapters[2]["chapter_id"]


def test_failed_batch_leaves_no_partial_manuscript() -> None:
    """整批失败无半份正文：替换目标在确认前被改动 → 整批拒绝，新增也不落库。"""
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    chapters = _chapters(client, pid)
    target = chapters[0]
    base = client.get(f"/api/v2/projects/{pid}/chapters/{target['chapter_id']}").json()[
        "chapter_base"
    ]
    preview = _preview(client, pid)
    selection = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": preview["revision"],
            "mode": "replace",
            "replacements": [
                {"chapter_id": target["chapter_id"], "expected_chapter_base": base},
                {
                    "chapter_id": chapters[1]["chapter_id"],
                    "expected_chapter_base": client.get(
                        f"/api/v2/projects/{pid}/chapters/{chapters[1]['chapter_id']}"
                    ).json()["chapter_base"],
                },
            ],
        },
    ).json()

    # 预览之后作者又改了目标章：确认必须整批拒绝，不能先写别的章
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{target['chapter_id']}/save",
        json={"idempotency_key": "edit-after-preview", "full_text": "作者刚改过的正文。"},
    )
    assert saved.status_code == 200, saved.text

    result = _confirm(client, pid, selection, decision="replace")
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "MANUSCRIPT_BASE_CONFLICT"
    after = _chapters(client, pid)
    assert len(after) == 2
    assert [c["title"] for c in after] == [c["title"] for c in chapters]


def test_replace_blocked_by_active_edit_session() -> None:
    """替换目标正在手工修改副本里：要求先完成或放弃，不自动放弃副本（M06 第 3 条）。"""
    client, _runtime, pid = _client()
    _confirm(client, pid, _preview(client, pid))
    target = _chapters(client, pid)[0]
    base = client.get(f"/api/v2/projects/{pid}/chapters/{target['chapter_id']}").json()[
        "chapter_base"
    ]

    session = client.post(f"/api/v2/projects/{pid}/edit-sessions").json()["session"][
        "session_id"
    ]
    draft = client.put(
        f"/api/v2/projects/{pid}/edit-sessions/{session}/chapters/{target['chapter_id']}",
        json={"expected_save_seq": 0, "request_id": "req-1", "text": "副本里的修改。"},
    )
    assert draft.status_code == 200, draft.text

    preview = _preview(
        client,
        pid,
        text="## 第一章 雨夜\n\n侦探在雨夜抵达旅馆。\n\n前台声称只有一位客人。\n",
    )
    updated = client.patch(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}",
        json={
            "expected_revision": preview["revision"],
            "mode": "replace",
            "replacements": [
                {"chapter_id": target["chapter_id"], "expected_chapter_base": base}
            ],
        },
    ).json()

    blocked = _confirm(client, pid, updated, decision="replace")
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "EDIT_SESSION_CONFLICT"
    # 副本仍在，正式正文未被导入覆盖
    current = client.get(f"/api/v2/projects/{pid}/edit-sessions/current").json()
    assert current["session"]["session_id"] == session
    assert _chapters(client, pid)[0]["chapter_id"] == target["chapter_id"]


# -- 格式、编码与上限 -------------------------------------------------------


def test_unsupported_format_is_rejected() -> None:
    client, _runtime, pid = _client()
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={"filename": "稿件.docx", "content_base64": _b64("正文")},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_UNSUPPORTED_FORMAT"


def test_non_utf8_bytes_are_rejected_instead_of_garbled() -> None:
    client, _runtime, pid = _client()
    payload = base64.b64encode("正文".encode("gb18030")).decode("ascii")
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={"filename": "旧稿.txt", "content_base64": payload},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_ENCODING_UNSUPPORTED"


def test_chapter_limit_is_enforced() -> None:
    client, _runtime, pid = _client()
    text = "\n\n".join(f"## 第{i}章\n\n正文{i}" for i in range(1, 1002))
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={"filename": "超长.md", "content_base64": _b64(text)},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_LIMIT_EXCEEDED"


# -- 预览过期 ---------------------------------------------------------------


def test_expired_preview_cannot_be_confirmed() -> None:
    client, runtime, pid = _client()

    class _StaleClock:
        def now(self) -> datetime:
            return datetime.now(UTC) - timedelta(hours=25)

    service = ImportService(
        runtime.service, runtime.manuscript_publisher, runtime.ids, _StaleClock()
    )
    preview = service.create_preview(pid, "旧稿.md", MD.encode("utf-8"))
    response = _confirm(client, pid, preview)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IMPORT_PREVIEW_EXPIRED"
    assert _chapters(client, pid) == []


def test_service_rejects_empty_manuscript() -> None:
    client, runtime, pid = _client()
    service = ImportService(
        runtime.service, runtime.manuscript_publisher, runtime.ids, runtime.clock
    )
    with pytest.raises(ViolationError) as exc:
        service.create_preview(pid, "空白.md", b"\n\n   \n")
    assert exc.value.code == "IMPORT_EMPTY_CONTENT"

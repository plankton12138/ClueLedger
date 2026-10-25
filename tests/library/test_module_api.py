"""模块库接口（0.8 B）。

契约：``/api/module-library`` 只操作全局库；作品接口不提供全库读写。错误沿用
``{"error": {code, where, why, fix, details}}``，状态码按错误码映射。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.library import ModuleLibrary
from hastings.library.presets import PRESETS
from hastings.server.file_app import create_workspace_app
from hastings.workspace.state import RecentWorkspaces

KEY = "http-request-1"


def _unconfigured_model() -> object:
    raise AssertionError("这些用例只走模块库接口，不应该调用模型")


def _client(tmp_path: Path) -> TestClient:
    library = ModuleLibrary(
        tmp_path / "模块库", cache_file=tmp_path / "state" / "library-catalog.json"
    )
    book = RecentWorkspaces(tmp_path / "state")
    return TestClient(
        create_workspace_app(
            workspaces=book,
            provider_factory=_unconfigured_model,  # type: ignore[arg-type]
            library=library,
        )
    )


def _create(client: TestClient, *, key: str = KEY, name: str = "主角团") -> dict[str, object]:
    response = client.post(
        "/api/module-library/modules",
        json={
            "kind": "characters",
            "name": name,
            "request_key": key,
            "entries": [
                {
                    "entry_id": "lin",
                    "title": "林静",
                    "body": "法医，与死者相识。",
                    "fields": {"roles": ["suspect"]},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_presets_expose_seven_kinds(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/module-library/presets")
    assert response.status_code == 200
    kinds = [item["kind"] for item in response.json()]
    assert kinds == ["characters", "world", "clues", "truth", "timeline", "plot", "reference"]
    assert {item["kind"]: item["allowed_fields"] for item in response.json()} == {
        item.kind: list(item.allowed_fields) for item in PRESETS
    }


def test_create_and_list_modules(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client)

    listing = client.get("/api/module-library/modules", params={"kind": "characters"})
    assert listing.status_code == 200
    body = listing.json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["module_id"] == created["module"]["module_id"]  # type: ignore[index]
    assert row["head"] == created["module"]["head"]  # type: ignore[index]
    assert row["revision_count"] == 1


def test_revision_detail_and_record_read(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client)
    module_id = created["module"]["module_id"]  # type: ignore[index]
    revision_id = created["module"]["head"]  # type: ignore[index]

    detail = client.get(f"/api/module-library/modules/{module_id}/revisions/{revision_id}")
    assert detail.status_code == 200
    entries = detail.json()["entries"]
    assert [item["entry_id"] for item in entries] == ["lin"]
    assert entries[0]["title"] == "林静"
    assert "body" not in entries[0]  # 索引只给定位，不复制原文

    record = client.get(
        f"/api/module-library/modules/{module_id}/revisions/{revision_id}/records/lin",
        params={"start": 0, "end": 20},
    )
    assert record.status_code == 200
    body = record.json()
    assert body["end"] == 20
    assert body["truncated"] is True
    assert len(body["text"]) == 20


def test_publish_conflict_maps_to_409(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client)
    module_id = created["module"]["module_id"]  # type: ignore[index]
    head = created["module"]["head"]  # type: ignore[index]

    first = client.post(
        f"/api/module-library/modules/{module_id}/revisions",
        json={
            "base_revision": head,
            "expected_head": head,
            "request_key": "http-publish-1",
            "entries": [{"entry_id": "lin", "title": "林静", "body": "第二版。"}],
        },
    )
    assert first.status_code == 200

    stale = client.post(
        f"/api/module-library/modules/{module_id}/revisions",
        json={
            "base_revision": head,
            "expected_head": head,
            "request_key": "http-publish-2",
            "entries": [{"entry_id": "lin", "title": "林静", "body": "第三版。"}],
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "MODULE_HEAD_STALE_CONFLICT"


def test_record_validation_maps_to_400(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/module-library/modules",
        json={
            "kind": "characters",
            "name": "主角团",
            "request_key": "http-bad-field",
            "entries": [
                {"entry_id": "lin", "title": "林静", "body": "法医。", "fields": {"alive": False}}
            ],
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "RECORD_FIELD_UNKNOWN"


def test_missing_module_maps_to_404(tmp_path: Path) -> None:
    client = _client(tmp_path)
    response = client.get("/api/module-library/modules/mod_0000000000000000/revisions")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "MODULE_NOT_FOUND"


def test_fork_and_trash_flow(tmp_path: Path) -> None:
    client = _client(tmp_path)
    created = _create(client)
    module_id = created["module"]["module_id"]  # type: ignore[index]
    head = created["module"]["head"]  # type: ignore[index]

    forked = client.post(
        f"/api/module-library/modules/{module_id}/fork",
        json={"base_revision": head, "name": "主角团（王城线）", "request_key": "http-fork-1"},
    )
    assert forked.status_code == 200
    assert forked.json()["module"]["forked_from"]["module_id"] == module_id

    trashed = client.post(
        f"/api/module-library/modules/{module_id}/trash",
        json={"expected_head": head, "request_key": "http-trash-1"},
    )
    assert trashed.status_code == 200
    assert trashed.json()["deleted"] is True
    assert client.get("/api/module-library/modules").json()["total"] == 1
    assert (
        client.get("/api/module-library/modules", params={"include_deleted": True}).json()["total"]
        == 2
    )


@pytest.mark.parametrize("kind", ["world", "clues", "truth", "timeline", "plot", "reference"])
def test_every_preset_can_hold_an_instance(tmp_path: Path, kind: str) -> None:
    client = _client(tmp_path)
    response = client.post(
        "/api/module-library/modules",
        json={
            "kind": kind,
            "name": f"{kind}-1",
            "request_key": f"http-{kind}-1",
            "entries": [{"entry_id": "first", "title": "第一条", "body": "内容。"}],
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["module"]["kind"] == kind


def test_server_mounts_the_library_at_the_configured_directory(tmp_path: Path) -> None:
    from hastings.server.__main__ import assemble_server

    assembled = assemble_server(state_root=tmp_path / "state", library_dir=tmp_path / "书面库")
    client = TestClient(assembled.app)
    created = client.post(
        "/api/module-library/modules",
        json={
            "kind": "world",
            "name": "海上城市",
            "request_key": "http-server-library",
            "entries": [{"entry_id": "port", "title": "港口", "body": "终年起雾。"}],
        },
    )

    assert created.status_code == 200, created.text
    assert (tmp_path / "书面库" / "library.json").is_file()

    module = created.json()["module"]
    detail = client.get(
        f"/api/module-library/modules/{module['module_id']}/revisions/{module['head']}"
    )
    assert detail.status_code == 200
    assert (tmp_path / "state" / "library-catalog.json").is_file()

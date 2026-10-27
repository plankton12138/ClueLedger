"""作品装配接口（0.8 D）。

契约：装配接口只写当前作品的绑定；绑定失败与库发布失败分开返回；依赖缺失只标记。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hastings.library import ModuleLibrary
from hastings.server.file_app import create_workspace_app
from hastings.workspace.state import RecentWorkspaces


def _changes_payload() -> dict:
    return {
        "operations": [
            {
                "operation_id": "cast",
                "action": "create",
                "name": "人物小传",
                "body": "七名嫌疑人。",
            },
            {
                "operation_id": "premise",
                "action": "create",
                "name": "聚集与调查前提",
                "body": "暴雪封山。",
            },
        ],
        "request_key": "changes-http-1",
    }


def _unconfigured_model() -> object:
    raise AssertionError("这些用例只走装配接口，不应该调用模型")


def _client(tmp_path: Path) -> tuple[TestClient, str, ModuleLibrary]:
    library = ModuleLibrary(
        tmp_path / "模块库", cache_file=tmp_path / "state" / "library-catalog.json"
    )
    book = RecentWorkspaces(tmp_path / "state")
    client = TestClient(
        create_workspace_app(
            workspaces=book,
            provider_factory=_unconfigured_model,  # type: ignore[arg-type]
            library=library,
        )
    )
    created = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "雪山旅馆"), "create": True}
    )
    assert created.status_code == 200, created.text
    return client, str(created.json()["id"]), library


def _module(client: TestClient, *, key: str = "assembly-lib-1") -> dict:
    response = client.post(
        "/api/module-library/modules",
        json={
            "kind": "characters",
            "name": "主角团",
            "request_key": key,
            "entries": [
                {
                    "entry_id": "lin",
                    "title": "林静",
                    "body": "法医。",
                    "fields": {"roles": ["suspect"]},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["module"]


def _binding(library: ModuleLibrary, module: dict, revision: str | None = None) -> dict:
    return {
        "id": "",
        "label": "主角团",
        "module": {
            "library_id": library.library_id,
            "module_id": module["module_id"],
            "revision_id": revision or module["head"],
        },
    }


def test_module_change_set_prepare_apply_and_undo(tmp_path: Path) -> None:
    client, workspace_id, _ = _client(tmp_path)

    prepared = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets",
        json=_changes_payload(),
    )
    assert prepared.status_code == 200, prepared.text
    change_set_id = prepared.json()["change_set_id"]
    assert prepared.json()["status"] == "prepared"

    # 同一请求键重放同一个变更集，不写第二份进度。
    replay = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets",
        json=_changes_payload(),
    )
    assert replay.json()["change_set_id"] == change_set_id

    detail = client.get(f"/api/file-workspaces/{workspace_id}/module-change-sets/{change_set_id}")
    assert detail.status_code == 200
    assert [item["operation_id"] for item in detail.json()["operations"]] == [
        "cast",
        "premise",
    ]
    # 应用前的预览入口：详情返回新建正文，界面不必等装配完成才能读。
    assert [item["body"] for item in detail.json()["operations"]] == [
        "七名嫌疑人。",
        "暴雪封山。",
    ]

    applied = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets/{change_set_id}/apply",
        json={"request_key": "changes-http-apply-1"},
    )
    assert applied.status_code == 200, applied.text
    assert applied.json()["applied"] is True
    bindings = client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()["bindings"]
    assert [row["name"] for row in bindings] == ["人物小传", "聚集与调查前提"]

    undone = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets/{change_set_id}/undo",
        json={"request_key": "changes-http-undo-1"},
    )
    assert undone.status_code == 200, undone.text
    assert undone.json()["reverted"] is True
    assert client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()["bindings"] == []


def test_module_change_set_apply_rejects_a_stale_digest(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    prepared = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets",
        json=_changes_payload(),
    ).json()
    module = _module(client)
    client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [
                {
                    "id": "",
                    "label": "主角团",
                    "module": {
                        "library_id": library.library_id,
                        "module_id": module["module_id"],
                        "revision_id": module["head"],
                    },
                }
            ],
            "expected_digest": None,
            "request_key": "assembly-http-changes-1",
        },
    )
    applied = client.post(
        f"/api/file-workspaces/{workspace_id}/module-change-sets/{prepared['change_set_id']}/apply",
        json={
            "request_key": "changes-http-apply-2",
            "expected_digest": client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()[
                "digest"
            ],
        },
    )
    assert applied.status_code == 200
    assert applied.json()["applied"] is False
    assert applied.json()["stage"] == "precondition"


def test_assembly_starts_empty(tmp_path: Path) -> None:
    client, workspace_id, _ = _client(tmp_path)
    response = client.get(f"/api/file-workspaces/{workspace_id}/assembly")
    assert response.status_code == 200
    body = response.json()
    assert body["bindings"] == []
    assert body["digest"] is None


def test_put_assembly_binds_an_exact_revision(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)

    response = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    )
    assert response.status_code == 200, response.text
    row = response.json()["bindings"][0]
    assert row["module"]["revision_id"] == module["head"]
    assert row["kind"] == "characters"
    assert row["problems"] == []

    again = client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()
    assert again["bindings"][0]["name"] == "主角团"


def test_stale_assembly_digest_maps_to_409(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    )
    other = _module(client, key="assembly-lib-2")

    response = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, other)],
            "expected_digest": "过期指纹",
            "request_key": "assembly-http-2",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSEMBLY_STALE_CONFLICT"


def test_same_module_two_versions_maps_to_409(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    second = client.post(
        f"/api/module-library/modules/{module['module_id']}/revisions",
        json={
            "base_revision": module["head"],
            "expected_head": module["head"],
            "request_key": "assembly-publish-2",
            "entries": [
                {
                    "entry_id": "lin",
                    "title": "林静",
                    "body": "法医，旧识。",
                    "fields": {"roles": ["suspect"]},
                }
            ],
        },
    )
    assert second.status_code == 200, second.text

    response = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [
                _binding(library, module),
                _binding(library, module, revision=str(second.json()["revision_id"])),
            ],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSEMBLY_BINDING_CONFLICT"


def test_put_assembly_preserves_links_when_field_is_omitted(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    first = _module(client)
    second = client.post(
        "/api/module-library/modules",
        json={
            "kind": "characters",
            "name": "反派",
            "request_key": "assembly-lib-2",
            "entries": [{"entry_id": "villain", "title": "周叙", "body": "旧识。"}],
        },
    ).json()["module"]
    created = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, first), _binding(library, second)],
            "expected_digest": None,
            "request_key": "assembly-links-1",
        },
    ).json()
    rows = [
        {"id": row["id"], "label": row["label"], "module": row["module"]}
        for row in created["bindings"]
    ]

    linked = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": rows,
            "links": [[rows[0]["id"], rows[1]["id"]]],
            "expected_digest": created["digest"],
            "request_key": "assembly-links-2",
        },
    ).json()
    assert linked["links"] == [[rows[0]["id"], rows[1]["id"]]]

    # 装卸客户端可能只回传 bindings；省略 links 必须保留已有关系。
    preserved = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": rows,
            "expected_digest": linked["digest"],
            "request_key": "assembly-links-3",
        },
    ).json()
    assert preserved["links"] == [[rows[0]["id"], rows[1]["id"]]]

    cleared = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": rows,
            "links": [],
            "expected_digest": preserved["digest"],
            "request_key": "assembly-links-4",
        },
    ).json()
    assert cleared["links"] == []


def test_module_updates_publishes_and_binds(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    bound = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    ).json()

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/module-updates",
        json={
            "module_id": module["module_id"],
            "base_revision": module["head"],
            "expected_head": module["head"],
            "expected_digest": bound["digest"],
            "request_key": "assembly-update-1",
            "entries": [
                {
                    "entry_id": "lin",
                    "title": "林静",
                    "body": "法医，旧识。",
                    "fields": {"roles": ["suspect"]},
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["published"] is True
    assert body["binding_updated"] is True
    revision = body["module"]["revision_id"]
    assert library.module(module["module_id"]).head == revision
    assert (
        client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()["bindings"][0]["module"][
            "revision_id"
        ]
        == revision
    )


def test_module_updates_reports_publish_failure_without_touching_bindings(
    tmp_path: Path,
) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/module-updates",
        json={
            "module_id": module["module_id"],
            "base_revision": module["head"],
            "expected_head": module["head"],
            "expected_digest": None,
            "request_key": "assembly-update-bad",
            "entries": [
                {"entry_id": "lin", "title": "林静", "body": "法医。", "fields": {"alive": False}}
            ],
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["published"] is False
    assert body["binding_updated"] is False
    assert body["error"]["code"] == "RECORD_FIELD_UNKNOWN"
    assert client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()["bindings"] == []
    assert library.module(module["module_id"]).head == module["head"]


def test_trashed_module_is_flagged_but_still_bound(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    )
    client.post(
        f"/api/module-library/modules/{module['module_id']}/trash",
        json={"expected_head": module["head"], "request_key": "assembly-trash-1"},
    )

    body = client.get(f"/api/file-workspaces/{workspace_id}/assembly").json()
    assert body["bindings"][0]["deleted"] is True
    assert "module_trashed" in body["bindings"][0]["problems"]


def test_derive_creates_a_registered_empty_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "ClueLedger" / "作品"
    monkeypatch.setattr("hastings.library.assembly_api.default_workspace_root", lambda: destination)
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    bound = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    ).json()

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/derive",
        json={
            "name": "雪山旅馆（另一条线）",
            "expected_digest": bound["digest"],
            "request_key": "derive-http-1",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["workspace"]["name"] == "雪山旅馆（另一条线）"
    new_id = body["workspace"]["id"]
    listed = {item["id"] for item in client.get("/api/file-workspaces").json()}
    assert new_id in listed

    assembly = client.get(f"/api/file-workspaces/{new_id}/assembly").json()
    assert assembly["bindings"][0]["module"]["revision_id"] == module["head"]
    assert assembly["bindings"][0]["kind"] == "characters"

    new_root = destination / "雪山旅馆（另一条线）"
    assert Path(body["workspace"]["path"]) == new_root
    assert (new_root / "正文").is_dir()
    assert not any((new_root / "正文").iterdir())
    assert not (new_root / "灵感本").exists()
    assert not (new_root / "设定集").exists()


def test_derive_rejects_a_stale_digest(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    )

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/derive",
        json={
            "name": "另一条线",
            "expected_digest": "过期指纹",
            "request_key": "derive-http-2",
        },
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSEMBLY_STALE_CONFLICT"
    assert not (tmp_path / "另一条线").exists()


def test_derive_rejects_a_name_with_separators(tmp_path: Path) -> None:
    client, workspace_id, library = _client(tmp_path)
    module = _module(client)
    bound = client.put(
        f"/api/file-workspaces/{workspace_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "assembly-http-1",
        },
    ).json()

    response = client.post(
        f"/api/file-workspaces/{workspace_id}/derive",
        json={
            "name": "子目录/另一条线",
            "expected_digest": bound["digest"],
            "request_key": "derive-http-3",
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "DERIVE_NAME_INVALID"

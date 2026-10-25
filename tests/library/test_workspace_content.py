"""作品内编辑的实际隔离、历史版本、重试与冲突契约。"""

from pathlib import Path

from tests.library.test_assembly_api import _binding, _client, _module


def test_workspace_edit_keeps_library_and_other_work_unchanged(tmp_path: Path) -> None:
    client, book_id, library = _client(tmp_path)
    module = _module(client)
    other_id = client.post(
        "/api/file-workspaces", json={"path": str(tmp_path / "另一部作品"), "create": True}
    ).json()["id"]
    assemblies = []
    for index, workspace_id in enumerate([book_id, other_id]):
        response = client.put(
            f"/api/file-workspaces/{workspace_id}/assembly",
            json={
                "bindings": [_binding(library, module)],
                "expected_digest": None,
                "request_key": f"initial-bind-{index}",
            },
        )
        assert response.status_code == 200, response.text
        assemblies.append(response.json())
    original = assemblies[0]
    binding = original["bindings"][0]
    url = f"/api/file-workspaces/{book_id}/assembly/{binding['id']}/content"
    payload = {
        "module_id": module["module_id"],
        "base_revision": module["head"],
        "name": "本作品的人物",
        "entries": [
            {
                "entry_id": "lin",
                "kind": "character",
                "title": "林静",
                "body": "## 角色小传\n\n只属于当前作品的修改。",
                "fields": {"roles": ["suspect"]},
            }
        ],
        "expected_digest": original["digest"],
        "request_key": "save-current-work-1",
    }
    saved = client.put(url, json=payload)
    assert saved.status_code == 200, saved.text
    changed = saved.json()
    revision_id = changed["bindings"][0]["module"]["revision_id"]
    assert revision_id != module["head"]
    assert changed["bindings"][0]["label"] == "本作品的人物"
    assert library.module(module["module_id"]).head == module["head"]
    assert "preview" not in library.modules()["items"][0]
    assert client.get(f"/api/file-workspaces/{other_id}/assembly").json() == assemblies[1]
    assert (
        "法医"
        in library.read_entry(
            module_id=module["module_id"], revision_id=module["head"], entry_id="lin"
        )["text"]
    )
    record = library.read_entry(
        module_id=module["module_id"], revision_id=revision_id, entry_id="lin"
    )
    assert "只属于当前作品" in record["text"]
    assert record["fields"] == {"roles": ["suspect"]}
    assert client.put(url, json=payload).json() == changed
    assert library.revisions(module_id=module["module_id"])["total"] == 2

    payload["base_revision"] = revision_id
    payload["expected_digest"] = changed["digest"]
    payload["request_key"] = "save-current-work-2"
    payload["entries"][0]["body"] = "第二次作品内修改。"
    second = client.put(url, json=payload)
    assert second.status_code == 200, second.text
    next_revision = second.json()["bindings"][0]["module"]["revision_id"]
    manifest = library.storage.read_manifest(module["module_id"], next_revision)
    assert manifest["parent"] == revision_id
    assert library.module(module["module_id"]).head == module["head"]


def test_workspace_content_conflicts_preserve_current_binding(tmp_path: Path) -> None:
    client, book_id, library = _client(tmp_path)
    module = _module(client)
    original = client.put(
        f"/api/file-workspaces/{book_id}/assembly",
        json={
            "bindings": [_binding(library, module)],
            "expected_digest": None,
            "request_key": "initial-bind-conflict",
        },
    ).json()
    binding = original["bindings"][0]
    url = f"/api/file-workspaces/{book_id}/assembly/{binding['id']}/content"
    payload = {
        "module_id": module["module_id"],
        "base_revision": module["head"],
        "name": "主角团",
        "entries": [{"entry_id": "lin", "title": "林静", "body": "第一次保存。"}],
        "expected_digest": original["digest"],
        "request_key": "conflict-content-1",
    }
    current = client.put(url, json=payload).json()
    payload["entries"][0]["body"] = "不能覆盖已保存的内容。"
    payload["request_key"] = "conflict-content-2"
    response = client.put(url, json=payload)
    assert response.status_code == 409, response.text
    assert client.get(f"/api/file-workspaces/{book_id}/assembly").json() == current
    assert library.module(module["module_id"]).head == module["head"]

    client.put(
        f"/api/file-workspaces/{book_id}/assembly",
        json={
            "bindings": [],
            "expected_digest": current["digest"],
            "request_key": "remove-content-binding",
        },
    )
    response = client.put(url, json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ASSEMBLY_TARGET_CONFLICT"

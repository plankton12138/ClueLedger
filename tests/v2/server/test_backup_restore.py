"""备份预览与同作品恢复 REST（V03-8.2）。

验收口径（计划 §6.5 补充合同 / §8）：备份可预览（清单、计数、指纹、覆盖范围）；
恢复经统一发布管线，差异先摊开、确认后才写，且**凭据绝不进包**。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile

import yaml
from fastapi.testclient import TestClient

from hastings.interfaces.exporters.v2_package_exporter import package_hash_of
from hastings.server.v2 import V2Runtime, build_v2_app

MD = """# 雪山旅馆

## 第一章 雪夜

侦探在雪夜抵达旅馆，风雪封住了山路。

## 第二章 空房

走廊尽头有一扇门，门后没有声音。
"""

#: 凭据类字面量：出现在备份包里就是泄漏（结构上不该有，测试守住回归）。
_SECRET_MARKERS = ("credential_ref", "api_key", "apikey", "secret", "bearer ", "sk-")


def _client() -> tuple[TestClient, str]:
    client = TestClient(build_v2_app(V2Runtime.memory()))
    pid = client.post("/api/v2/projects", json={"name": "备份源"}).json()["project_id"]
    return client, pid


def _import(client: TestClient, pid: str) -> None:
    preview = client.post(
        f"/api/v2/projects/{pid}/imports/previews",
        json={
            "filename": "雪山旅馆.md",
            "content_base64": base64.b64encode(MD.encode("utf-8")).decode("ascii"),
        },
    ).json()
    response = client.post(
        f"/api/v2/projects/{pid}/imports/previews/{preview['preview_id']}/confirm",
        json={"preview_hash": preview["preview_hash"]},
    )
    assert response.status_code == 200, response.text
    created = client.post(
        f"/api/v2/projects/{pid}/ledger/entries",
        json={"kind": "story.character", "fields": {"name": "姐姐"}, "request_id": "req-sis"},
    )
    assert created.status_code == 201, created.text


def _chapters(client: TestClient, pid: str) -> list[dict]:
    return client.get(f"/api/v2/projects/{pid}/manuscript").json()["chapters"]


def _export_zip(client: TestClient, pid: str) -> bytes:
    response = client.get(f"/api/v2/projects/{pid}/export")
    assert response.status_code == 200, response.text
    return response.content


def _restore(client: TestClient, pid: str, archive: bytes, **body: object):
    payload = {"content_base64": base64.b64encode(archive).decode("ascii"), **body}
    return client.post(f"/api/v2/projects/{pid}/restore", json=payload)


def test_backup_preview_lists_counts_and_coverage() -> None:
    client, pid = _client()
    _import(client, pid)
    preview = client.get(f"/api/v2/projects/{pid}/backup").json()
    assert preview["kind"] == "hastings.v2.export"
    assert preview["package_hash"]
    assert preview["counts"]["chapters"] == 2
    assert preview["counts"]["story_entities"] == 1
    coverage = preview["coverage"]
    included = {item["what"] for item in coverage["included"]}
    assert any("正文" in item for item in included)
    assert any("账本" in item for item in included)
    excluded = coverage["excluded"]
    assert any("凭据" in item["what"] for item in excluded)
    assert any("副本" in item["what"] for item in excluded)
    assert any("执行日志" in item["what"] for item in excluded)
    # 每条排除项都必须说明它在哪里、为什么不在包里
    for item in excluded:
        assert item["where"] and item["why"]


def test_restore_without_difference_is_no_change() -> None:
    client, pid = _client()
    _import(client, pid)
    archive = _export_zip(client, pid)
    result = _restore(client, pid, archive)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["status"] == "no_change"
    assert payload["commit_id"] is None
    assert "一致" in payload["message"]


def test_restore_requires_confirmation_then_restores_both_text_and_ledger() -> None:
    client, pid = _client()
    _import(client, pid)
    archive = _export_zip(client, pid)
    chapter = _chapters(client, pid)[0]

    # 备份之后改了正文与账本
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter['chapter_id']}/save",
        json={
            "idempotency_key": "after-backup",
            "full_text": "备份之后改写的正文。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    assert saved.status_code == 200, saved.text
    entries = client.get(f"/api/v2/projects/{pid}/ledger").json()["entries"]
    assert entries, "账本应至少有刚创建的人物"
    entry_id = entries[0]["id"]
    entry = client.get(f"/api/v2/projects/{pid}/ledger/entries/{entry_id}").json()
    patched = client.patch(
        f"/api/v2/projects/{pid}/ledger/entries/{entry_id}",
        json={
            "fields": {"name": "姐姐（改）"},
            "expected_revision": entry["revision_id"],
            "request_id": "after-backup-ledger",
        },
    )
    assert patched.status_code == 200, patched.text

    # 未确认：拒绝并列出差异，且不改写任何内容
    blocked = _restore(client, pid, archive)
    assert blocked.status_code == 409, blocked.text
    error = blocked.json()["error"]
    assert error["code"] == "EXPORT_RESTORE_CONFLICT"
    assert [row["chapter_id"] for row in error["details"]["chapters"]] == [chapter["chapter_id"]]
    assert error["details"]["ledger"][0]["changed_fields"] == ["name"]
    assert error["details"]["choices"] == ["confirm_overwrite", "cancel"]
    current = client.get(f"/api/v2/projects/{pid}/chapters/{chapter['chapter_id']}").json()
    assert current["text"] == "备份之后改写的正文。"

    # 确认后：正文与账本一起恢复
    result = _restore(client, pid, archive, confirm_overwrite=True)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert payload["commit_id"]
    assert payload["restored_chapters"] == [chapter["chapter_id"]]
    assert payload["restored_ledger_ids"]

    restored = client.get(f"/api/v2/projects/{pid}/chapters/{chapter['chapter_id']}").json()
    assert "侦探在雪夜抵达旅馆" in restored["text"]
    ledger = client.get(
        f"/api/v2/projects/{pid}/ledger/entries/{payload['restored_ledger_ids'][0]}"
    ).json()
    assert ledger["content"]["name"] == "姐姐"


def test_restore_after_restore_is_no_change() -> None:
    client, pid = _client()
    _import(client, pid)
    archive = _export_zip(client, pid)
    chapter = _chapters(client, pid)[0]
    saved = client.post(
        f"/api/v2/projects/{pid}/chapters/{chapter['chapter_id']}/save",
        json={
            "idempotency_key": "edit-1",
            "full_text": "改一版。",
            "expected_chapter_base": chapter["chapter_base"],
        },
    )
    assert saved.status_code == 200

    first = _restore(client, pid, archive, confirm_overwrite=True).json()
    assert first["commit_id"]
    # 恢复后内容与备份一致：再恢复同一包是合法空结果，而不是又一个新版本。
    again = _restore(client, pid, archive, confirm_overwrite=True)
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "no_change"


def test_restore_rejects_tampered_package_and_bad_base64() -> None:
    client, pid = _client()
    _import(client, pid)
    bad = _restore(client, pid, b"!!!not base64!!!")
    assert bad.status_code == 400, bad.text
    assert bad.json()["error"]["code"] == "EXPORT_PACKAGE_INVALID"

    with zipfile.ZipFile(io.BytesIO(_export_zip(client, pid))) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
    chapter_path = next(name for name in files if name.startswith("chapters/"))
    files[chapter_path] = "被改过的正文"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in files.items():
            out.writestr(name, data)
    tampered = _restore(client, pid, buffer.getvalue(), confirm_overwrite=True)
    assert tampered.status_code == 400, tampered.text
    assert tampered.json()["error"]["code"] == "EXPORT_PACKAGE_INVALID"


def test_restore_rejects_path_traversal_entries() -> None:
    client, pid = _client()
    _import(client, pid)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("../escape.txt", "x")
        zf.writestr("manifest.json", json.dumps({"kind": "hastings.v2.export"}))
    response = _restore(client, pid, buffer.getvalue(), confirm_overwrite=True)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "EXPORT_PACKAGE_INVALID"


def test_restore_reports_missing_object_instead_of_creating_it() -> None:
    client, pid = _client()
    _import(client, pid)
    archive = _export_zip(client, pid)
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        files = {name: zf.read(name) for name in zf.namelist()}
        manifest = json.loads(files["manifest.json"])

    extra = yaml.safe_dump(
        {"artifact_id": "art_missing", "kind": "story.character", "content": {"name": "新人"}}
    ).encode("utf-8")
    files["bible/characters/art_missing.yaml"] = extra
    manifest["files"].append(
        {
            "path": "bible/characters/art_missing.yaml",
            "sha256": hashlib.sha256(extra).hexdigest(),
            "kind": "story.character",
            "artifact_id": "art_missing",
        }
    )
    manifest["counts"]["story_entities"] += 1
    manifest["package_hash"] = package_hash_of(manifest)
    files["manifest.json"] = json.dumps(manifest).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as out:
        for name, data in files.items():
            out.writestr(name, data)
    response = _restore(client, pid, buffer.getvalue(), confirm_overwrite=True)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "EXPORT_RESTORE_NEW_OBJECT"


def test_export_package_contains_no_credentials() -> None:
    client, pid = _client()
    _import(client, pid)
    archive = _export_zip(client, pid)
    lowered = archive.lower()
    for marker in _SECRET_MARKERS:
        assert marker.encode() not in lowered, f"备份包出现凭据类字面量 {marker!r}"
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = zf.namelist()
        manifest = json.loads(zf.read("manifest.json"))
    allowed = {"manifest.json", "relations.json"}
    assert all(name.startswith(("chapters/", "bible/")) or name in allowed for name in names)
    assert not any("credential" in name for name in names)
    assert set(manifest) >= {"kind", "schema_version", "files", "counts", "package_hash"}

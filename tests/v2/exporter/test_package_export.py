"""Phase 17 导出：指定 Commit → 可读包（Markdown/YAML/JSON + manifest 哈希）。

覆盖：确定性（同 Commit 两次导出字节一致）、manifest 完整性（逐文件 sha256 与
package_hash 可重算）、往返（导出 → 重导入 → 内容/ID/关系逐项相等）、重导入幂等
（同包重放返回原 Commit）、损坏包显式拒绝、指定历史 Commit 导出、REST zip 端点。

往返的意义与迁移同构：导入器回答「V1 → V2 没丢东西」，导出器回答「V2 → 包 → V2
还能还原」。两者共用同一套确定性 ID 与规范化文本哈希，使「导出可用」成为可核对
的证据而不是一句承诺。
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from hastings.application.errors import ViolationError
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest
from hastings.domain.story.canon.models import Character
from hastings.domain.story.catalog import StoryCatalog
from hastings.interfaces.exporters.v2_package_exporter import (
    EXPORT_KIND,
    EXPORT_SCHEMA_VERSION,
    apply_exported_package,
    export_commit,
    package_hash_of,
    read_package,
    verify_package,
    write_package,
    zip_package,
)
from hastings.interfaces.importers.v1_workspace_importer import _text_hash

ACTOR = Actor(kind="human", id="exporter")


def _export(env, head: str | None = None):
    return export_commit(env.service, env.project, CommitId(head or env.head))


def _fresh_service():
    """独立的仓库实例：往返的语义是「恢复到全新环境」，artifact id 由
    确定性方案保证一致，但仓库按实例隔离对象所有权。"""
    from hastings.infrastructure.repository.memory import build_memory_repository
    from hastings.infrastructure.runtime import SystemClock, UuidIdGenerator

    return build_memory_repository(SystemClock(), UuidIdGenerator()).service


def _chapter_entries(package) -> list[dict]:
    return [e for e in package.manifest["files"] if e["kind"] == "manuscript.chapter"]


# --------------------------------------------------------------------------
# 导出：结构与确定性
# --------------------------------------------------------------------------
def test_export_manifest_shape_and_integrity(imported_project) -> None:
    package = _export(imported_project)

    assert package.manifest["kind"] == EXPORT_KIND
    assert package.manifest["schema_version"] == EXPORT_SCHEMA_VERSION
    assert package.manifest["commit_id"] == imported_project.head
    assert package.manifest["project_id"] == imported_project.project.value

    # manifest 列出包内除自身外的每个文件，路径不重不漏
    paths = {entry["path"] for entry in package.manifest["files"]}
    assert paths == set(package.files) - {"manifest.json"}

    # 逐文件 sha256 可重算
    for entry in package.manifest["files"]:
        if entry["path"] == "manifest.json":
            continue
        digest = hashlib.sha256(package.files[entry["path"]]).hexdigest()
        assert digest == entry["sha256"], entry["path"]

    # package_hash 可重算，且等于整包指纹
    assert package.package_hash == package_hash_of(package.manifest)


def test_export_is_deterministic(imported_project) -> None:
    first = _export(imported_project)
    second = _export(imported_project)
    assert first.files == second.files
    assert first.manifest == second.manifest
    assert first.package_hash == second.package_hash


def test_export_contains_readable_chapters_and_bible(imported_project) -> None:
    """包是可读备份：Markdown 正文 + YAML 账本，且章数与导入报告一致。"""
    package = _export(imported_project)

    chapters = _chapter_entries(package)
    assert len(chapters) == 3, "基准 fixture 有三章正文"
    for entry in chapters:
        text = package.files[entry["path"]].decode("utf-8")
        assert text.strip(), f"{entry['path']} 正文为空"
        assert entry["title"], "manifest 须携带章标题（Markdown 只存正文）"
        assert entry["order_key"], "manifest 须携带顺序键"
        assert entry["units"], "manifest 须携带段落清单（unit_id + 文本哈希）"

    bible = [e for e in package.manifest["files"] if e["path"].startswith("bible/")]
    kinds = {e["kind"] for e in bible}
    assert "story.character" in kinds, "角色账本须在包内"
    assert all(e["path"].endswith(".yaml") for e in bible), "账本须是可读 YAML"

    assert package.manifest["counts"]["chapters"] == 3
    assert package.manifest["counts"]["chars"] > 0


def test_export_historical_commit_not_head(imported_project) -> None:
    """指定历史 Commit 导出得到该时点内容：head 前移后旧 Commit 导出不变。"""
    env = imported_project
    old = _export(env)

    # head 上追加一次真实提交（新增一个角色对象）
    branch = env.service.get_branch(env.project, "main")
    extra = Character(
        name="导出测试角色",
        identity="证人",
        goal="作证",
        secret="无",
        arc="平坦",
        legacy_metadata={},
    ).to_content()
    env.service.commit(
        CommitRequest(
            project_id=env.project,
            branch_id=branch.id,
            expected_head=branch.head_commit_id,
            actor=ACTOR,
            idempotency_key="export-test-edit",
            summary="导出测试：追加角色",
            changes=(
                ArtifactChange(
                    artifact_id=ArtifactId("story_char_export_test"),
                    content=extra,
                    kind="story.character",
                ),
            ),
        )
    )

    again = export_commit(env.service, env.project, CommitId(env.head))
    assert again.files == old.files, "历史 Commit 的导出必须与当初一致"

    new_head = env.service.get_branch(env.project, "main").head_commit_id
    fresh = export_commit(env.service, env.project, new_head)
    fresh_bible = {e["path"] for e in fresh.manifest["files"] if e["path"].startswith("bible/")}
    old_bible = {e["path"] for e in old.manifest["files"] if e["path"].startswith("bible/")}
    assert fresh_bible - old_bible, "head 导出应包含新增角色"


# --------------------------------------------------------------------------
# 重导入：往返与幂等
# --------------------------------------------------------------------------
def test_roundtrip_reimport_restores_content(imported_project) -> None:
    env = imported_project
    package = _export(env)

    target_service = _fresh_service()
    target = target_service.create_project("往返目标").id
    result = apply_exported_package(
        target_service, target_service, project_id=target, files=package.files, actor=ACTOR
    )
    assert result.status == "committed"

    # 逐项核对：章集合、每章规范化正文哈希、章基础、故事对象、关系全部一致
    source_snapshot = env.service.get_snapshot(env.project, CommitId(env.head))
    target_snapshot = target_service.get_snapshot(target, CommitId(result.new_head))
    source_ms = ManuscriptCatalog.from_snapshot(source_snapshot)
    target_ms = ManuscriptCatalog.from_snapshot(target_snapshot)

    assert {e.artifact_id for e in source_ms.chapters()} == {
        e.artifact_id for e in target_ms.chapters()
    }
    for entry in source_ms.chapters():
        source_text = "\n\n".join(u.model().text for u in source_ms.units_of(entry.artifact_id))
        target_text = "\n\n".join(u.model().text for u in target_ms.units_of(entry.artifact_id))
        assert _text_hash(source_text) == _text_hash(target_text), entry.artifact_id
        assert source_ms.chapter_base(entry.artifact_id) == target_ms.chapter_base(
            entry.artifact_id
        ), "章基础（unit_id+文本）须逐位一致"

    source_story = StoryCatalog.from_snapshot(source_snapshot)
    target_story = StoryCatalog.from_snapshot(target_snapshot)
    assert {e.artifact_id for e in source_story.entries} == {
        e.artifact_id for e in target_story.entries
    }
    for entry in source_story.entries:
        mirrored = target_story.get(entry.artifact_id)
        assert mirrored is not None and mirrored.content == entry.content

    source_rels = sorted(
        (r.source.value, r.relation_type, r.target.value) for r in source_snapshot.relations
    )
    target_rels = sorted(
        (r.source.value, r.relation_type, r.target.value) for r in target_snapshot.relations
    )
    assert source_rels == target_rels, "依赖边须逐条还原"


def test_reimport_is_idempotent(imported_project) -> None:
    env = imported_project
    package = _export(env)
    target_service = _fresh_service()
    target = target_service.create_project("幂等目标").id
    first = apply_exported_package(
        target_service, target_service, project_id=target, files=package.files, actor=ACTOR
    )
    second = apply_exported_package(
        target_service, target_service, project_id=target, files=package.files, actor=ACTOR
    )
    assert second.replayed, "同一包重放应命中幂等记录"
    assert second.new_head == first.new_head


def test_reimport_refuses_non_empty_project(imported_project) -> None:
    env = imported_project
    package = _export(env)
    with pytest.raises(ViolationError) as exc:
        apply_exported_package(
            env.service,
            env.service,
            project_id=env.project,
            files=package.files,
            actor=ACTOR,
        )
    assert "非空" in exc.value.why


# --------------------------------------------------------------------------
# 包完整性：损坏显式拒绝
# --------------------------------------------------------------------------
def test_verify_rejects_tampered_file(imported_project) -> None:
    package = _export(imported_project)
    tampered = dict(package.files)
    chapter_path = _chapter_entries(package)[0]["path"]
    tampered[chapter_path] = tampered[chapter_path] + "被篡改的一段。\n".encode()
    with pytest.raises(ViolationError) as exc:
        verify_package(tampered)
    assert "sha256" in exc.value.why or "哈希" in exc.value.why


def test_verify_rejects_wrong_package_hash(imported_project) -> None:
    package = _export(imported_project)
    manifest = json.loads(package.files["manifest.json"])
    manifest["package_hash"] = "0" * 64
    tampered = dict(package.files)
    tampered["manifest.json"] = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2
    ).encode("utf-8")
    with pytest.raises(ViolationError):
        verify_package(tampered)


# --------------------------------------------------------------------------
# 磁盘表示与 zip
# --------------------------------------------------------------------------
def test_write_and_read_package_roundtrip(imported_project, tmp_path) -> None:
    package = _export(imported_project)
    directory = write_package(package, tmp_path / "pkg")
    assert read_package(directory) == package.files


def test_zip_is_deterministic_and_contains_manifest(imported_project) -> None:
    package = _export(imported_project)
    first = zip_package(package)
    second = zip_package(package)
    assert first == second, "同包两次打 zip 必须字节一致（不含时间戳漂移）"
    with zipfile.ZipFile(io.BytesIO(first)) as zf:
        assert "manifest.json" in zf.namelist()
        assert set(zf.namelist()) == set(package.files)


# --------------------------------------------------------------------------
# REST 端点
# --------------------------------------------------------------------------
def test_rest_export_endpoint_returns_zip(imported_project) -> None:
    from fastapi.testclient import TestClient

    from hastings.server.v2 import V2Runtime

    env = imported_project
    runtime = V2Runtime.memory()
    # 把夹具仓库的服务面接到运行时上（同一内存仓库实例）
    runtime.service = env.service
    app_runtime = runtime
    from hastings.server.v2 import build_v2_app

    app = build_v2_app(app_runtime)
    client = TestClient(app)
    response = client.get(f"/api/v2/projects/{env.project.value}/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["commit_id"] == env.head


def test_rest_export_unknown_project_is_404() -> None:
    from fastapi.testclient import TestClient

    from hastings.server.v2 import V2Runtime, build_v2_app

    app = build_v2_app(V2Runtime.memory())
    client = TestClient(app)
    response = client.get("/api/v2/projects/p_404/export")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "REPO_BRANCH_NOT_FOUND"

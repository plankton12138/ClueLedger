"""Phase 17 T17.1–T17.3：V1 工作区迁移测试（plan §5 / §6）。

覆盖：全文件归类与覆盖性、报告确定性、只读、阻断实体显式报错、幂等、源变化要求
重跑、非空项目拒绝、章节正文 hash、悬空引用、旧 revision 保留、运行记录归档。

两个源夹具：
- ``fixture_workspace``：基准 fixture（含两个须作者决策的阻断实体）；
- ``resolved_workspace``：作者排除阻断项后的源，用于写入/校验路径。
"""

from __future__ import annotations

import hashlib

import pytest

from hastings.application.errors import ViolationError
from hastings.bible.store import StoryWorkspace
from hastings.domain.manuscript import kinds as ms_kinds
from hastings.domain.repository.ids import CommitId, ProjectId
from hastings.interfaces.importers.v1_workspace_importer import (
    MIGRATION_OUTCOMES,
    OUTCOME_ERROR,
    OUTCOME_IGNORED,
    OUTCOME_LEGACY,
    OUTCOME_MAPPED,
    import_key,
    manifest_hash,
    plan_migration,
    scan_v1_workspace,
)

from .conftest import MigrationEnv


def _all_files(workspace: StoryWorkspace) -> set[str]:
    root = workspace.story_dir
    return {
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != "manifest.json"
    }


def _fresh_project(env: MigrationEnv) -> ProjectId:
    return env.service.create_project("迁移目标").id


# --------------------------------------------------------------------------
# T17.1 扫描
# --------------------------------------------------------------------------
def test_scan_covers_every_source_file_exactly_once(fixture_workspace: StoryWorkspace) -> None:
    report = scan_v1_workspace(fixture_workspace)
    paths = [item.path for item in report.items]
    assert len(paths) == len(set(paths)), "同一源文件被归类多次"
    assert set(paths) == _all_files(fixture_workspace), "有源文件未被归类"
    assert all(item.outcome in MIGRATION_OUTCOMES for item in report.items)
    assert not report.unexplained, "存在无理由遗漏"


def test_scan_is_deterministic(fixture_workspace: StoryWorkspace) -> None:
    assert scan_v1_workspace(fixture_workspace) == scan_v1_workspace(fixture_workspace)


def test_scan_is_read_only(fixture_workspace: StoryWorkspace) -> None:
    before = {
        rel: hashlib.sha256((fixture_workspace.story_dir / rel).read_bytes()).hexdigest()
        for rel in _all_files(fixture_workspace)
    }
    scan_v1_workspace(fixture_workspace)
    after = {
        rel: hashlib.sha256((fixture_workspace.story_dir / rel).read_bytes()).hexdigest()
        for rel in _all_files(fixture_workspace)
    }
    assert after == before, "dry-run 修改了源工作区（适配器必须只读）"


def test_outcomes_split_by_category(fixture_workspace: StoryWorkspace) -> None:
    report = scan_v1_workspace(fixture_workspace)
    by_path = {item.path: item for item in report.items}

    assert by_path["chapters/ch01.md"].outcome == OUTCOME_MAPPED
    assert by_path["chapters/.revisions/ch01/001.md"].outcome == OUTCOME_LEGACY
    assert by_path["sessions.json"].outcome == OUTCOME_IGNORED
    assert by_path["events.jsonl"].outcome == OUTCOME_IGNORED
    assert by_path["story.yaml"].outcome == OUTCOME_IGNORED
    for item in report.ignored:
        assert item.detail.strip(), f"ignored 项缺少原因：{item.path}"
    assert report.counts.chapters == 3
    assert report.counts.revisions == 1
    assert report.counts.chars > 0


def test_blocking_entities_are_reported_not_dropped(fixture_workspace: StoryWorkspace) -> None:
    """泄压阀：无法无损迁移的悬疑要素成为带三要素的 error，报告因此不通过。"""
    report = scan_v1_workspace(fixture_workspace)
    assert not report.ok, "含阻断实体的源不应被判定为可迁移"
    error_paths = {item.path for item in report.errors}
    assert error_paths == {"bible/clues.yaml", "bible/method.yaml"}
    for item in report.errors:
        assert item.detail.strip(), "error 项必须写明原因（不静默丢弃）"


def test_resolved_source_passes(
    fixture_workspace: StoryWorkspace, resolved_workspace: StoryWorkspace
) -> None:
    assert not scan_v1_workspace(fixture_workspace).ok
    assert scan_v1_workspace(resolved_workspace).ok


def test_ambiguous_mapping_surfaces_in_story_report(resolved_workspace: StoryWorkspace) -> None:
    report = scan_v1_workspace(resolved_workspace)
    # 歧义字段保留在 Phase 13 报告里，供作者决策（不静默丢弃），但不阻断迁移
    assert report.story.ambiguous_fields, "应存在须作者决策的歧义字段"
    assert report.ok, "歧义不等于错误，报告仍应通过"


def test_unexplained_new_file_fails_report(tmp_workspace: StoryWorkspace) -> None:
    (tmp_workspace.story_dir / "mystery.yaml").write_text("unexpected: true", encoding="utf-8")
    report = scan_v1_workspace(tmp_workspace)
    assert not report.ok
    assert any(
        item.path == "mystery.yaml" and item.outcome == OUTCOME_ERROR for item in report.items
    )


def test_nested_chapter_directory_is_rejected_not_silently_mapped(
    tmp_workspace: StoryWorkspace,
) -> None:
    """评审 P1：嵌套章目录不得被标成 mapped 却不导入。扫描与导入范围必须一致。"""
    from hastings.domain.story.ids import deterministic_story_id

    archive = tmp_workspace.chapters_dir / "archive"
    archive.mkdir()
    (archive / "ch99.md").write_text("# 第99章\n\n归档正文。\n", encoding="utf-8")

    report = scan_v1_workspace(tmp_workspace)
    item = next(i for i in report.items if i.path == "chapters/archive/ch99.md")
    assert item.outcome == OUTCOME_ERROR, "不支持的目录结构必须显式报错"
    assert not report.ok
    assert report.counts.chapters == 3, "嵌套章节不得计入可迁移章节数"

    plan = plan_migration(tmp_workspace)
    emitted = {c.artifact_id.value for c in plan.manuscript_changes}
    assert deterministic_story_id(ms_kinds.KIND_CHAPTER, "v1:chapter:ch99") not in emitted


def test_scan_and_import_agree_on_chapter_scope(resolved_workspace: StoryWorkspace) -> None:
    """扫描判定的 mapped 章节集合 == 提案实际导入的章节集合。"""
    report = scan_v1_workspace(resolved_workspace)
    mapped = {
        item.target
        for item in report.items
        if item.item_type == "chapter" and item.outcome == OUTCOME_MAPPED
    }
    plan = plan_migration(resolved_workspace)
    emitted = {
        c.artifact_id.value for c in plan.manuscript_changes if c.kind == ms_kinds.KIND_CHAPTER
    }
    assert emitted == mapped
    assert len(mapped) == report.counts.chapters


def test_missing_runtime_records_are_not_failures(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    """评审 P2：源里本就没有会话索引/事件日志，不应被当成校验失败。"""
    (resolved_workspace.story_dir / "sessions.json").unlink()
    (resolved_workspace.story_dir / "events.jsonl").unlink()

    report = migration_env.importer.dry_run(resolved_workspace)
    assert report.ok
    assert not any(item.path in ("sessions.json", "events.jsonl") for item in report.items)

    project = _fresh_project(migration_env)
    result = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    assert result.validation.ok, f"校验误判：{result.validation.failures}"


def test_legacy_single_session_layout_imports(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    """真正的旧单会话布局（根目录 session.jsonl，无索引无 sessions/）必须可迁移。

    旧格式定义见 session_store.legacy_session_file：多会话索引（D13）之前的工作区
    在根目录保存 session.jsonl。此前分类器只识别 sessions.json 与 sessions/*.jsonl，
    把它判为未知文件阻断迁移（评审 P2）——本用例覆盖该实际旧文件布局的完整导入。
    """
    import shutil

    root = resolved_workspace.story_dir
    (root / "sessions.json").unlink()
    shutil.rmtree(root / "sessions")
    (root / "session.jsonl").write_text(
        '{"role": "user", "content": "开始调查庄园失踪案。"}\n'
        '{"role": "assistant", "content": "先从管家问起。"}\n',
        encoding="utf-8",
    )

    report = migration_env.importer.dry_run(resolved_workspace)
    item = next(i for i in report.items if i.path == "session.jsonl")
    assert item.outcome == OUTCOME_IGNORED, "旧单会话日志应归档而非报错"
    assert item.item_type == "session_log"
    assert item.detail.strip(), "归档必须带原因"
    archived = {i.path for i in report.ignored}
    assert "session.jsonl" in archived
    assert report.ok, f"旧单会话布局的报告不应被阻断：{report.errors}"

    project = _fresh_project(migration_env)
    result = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    assert result.validation.ok, f"校验未通过：{result.validation.failures}"
    runtime = next(e for e in result.validation.entries if e.check == "runtime_records_archived")
    assert runtime.ok, "session.jsonl 须计入只读归档核对范围"
    assert (root / "session.jsonl").exists(), "迁移不得改动旧工作区源文件"


def test_session_lock_and_archive_files_are_ignored_not_errors(
    tmp_workspace: StoryWorkspace,
) -> None:
    """真实工作区核对发现（2026-10-15）：会话锁文件与压缩归档会话不得阻断迁移。

    旧格式工作区在根目录留有 ``.session.jsonl.lock``（进程互斥瞬态文件）、
    ``sessions/.session-*.jsonl.lock``、以及压缩归档 ``session-<stamp>.jsonl``——
    三者都是 Agent 运行痕迹，须带原因归档而非「未知文件」报错。
    """
    from hastings.bible.models import Clue
    from hastings.bible.store import load_ledger, save_ledger

    root = tmp_workspace.story_dir
    clues = load_ledger(tmp_workspace.clues_path, Clue)
    save_ledger(tmp_workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(tmp_workspace.method_path, [])
    (root / ".session.jsonl.lock").write_text("", encoding="utf-8")
    sessions = root / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / ".session-20261012-161507-6af0.jsonl.lock").write_text("", encoding="utf-8")
    (root / "session-20261010-230506.jsonl").write_text('{"role": "user"}\n', encoding="utf-8")

    report = scan_v1_workspace(tmp_workspace)
    by_path = {item.path: item for item in report.items}
    for path, expected_type in (
        (".session.jsonl.lock", "session_lock"),
        ("sessions/.session-20261012-161507-6af0.jsonl.lock", "session_lock"),
        ("session-20261010-230506.jsonl", "session_archive"),
    ):
        item = by_path[path]
        assert item.outcome == OUTCOME_IGNORED, f"{path} 应归档而非报错"
        assert item.item_type == expected_type
        assert item.detail.strip(), "归档必须带原因"
    assert report.ok, f"运行痕迹不应阻断迁移报告：{report.errors}"


def test_present_runtime_records_are_required_to_be_archived(
    resolved_workspace: StoryWorkspace,
) -> None:
    """反例：源里存在的运行记录必须进入 ignored 归档（校验核对实际存在的项）。"""
    report = scan_v1_workspace(resolved_workspace)
    archived = {item.path for item in report.ignored}
    expected = {
        rel
        for rel in _all_files(resolved_workspace)
        if rel in ("story.yaml", "settings.yaml", "sessions.json", "events.jsonl")
        or (rel.startswith("sessions/") and rel.endswith(".jsonl"))
    }
    assert expected <= archived


# --------------------------------------------------------------------------
# T17.2 幂等导入
# --------------------------------------------------------------------------
def test_apply_writes_single_commit_and_validates(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    report = migration_env.importer.dry_run(resolved_workspace)
    project = _fresh_project(migration_env)
    result = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    assert result.status == "committed"
    assert result.commit_id is not None
    assert not result.replayed
    assert result.validation.ok, f"校验未通过：{result.validation.failures}"
    assert result.report.counts.chapters == 3


def test_apply_is_idempotent(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    report = migration_env.importer.dry_run(resolved_workspace)
    project = _fresh_project(migration_env)
    first = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    second = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    assert second.replayed, "相同输入重试应命中幂等记录"
    assert second.commit_id == first.commit_id
    assert second.new_head == first.new_head


def test_source_change_requires_new_dry_run(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    project = _fresh_project(migration_env)
    with pytest.raises(ViolationError) as exc:
        migration_env.importer.apply(
            resolved_workspace, project_id=project, confirm_run_id="0" * 64
        )
    assert exc.value.code == "REPO_INVALID_CHANGE"
    assert exc.value.why and exc.value.fix, "三要素消息缺失"


def test_apply_refuses_non_empty_project(
    tmp_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    from hastings.bible.models import Clue
    from hastings.bible.store import load_ledger, save_ledger

    clues = load_ledger(tmp_workspace.clues_path, Clue)
    save_ledger(tmp_workspace.clues_path, [c for c in clues if not c.is_red_herring])
    save_ledger(tmp_workspace.method_path, [])

    report_a = migration_env.importer.dry_run(tmp_workspace)
    project = _fresh_project(migration_env)
    migration_env.importer.apply(tmp_workspace, project_id=project, confirm_run_id=report_a.run_id)

    chapter = tmp_workspace.chapters_dir / "ch02.md"
    chapter.write_text(chapter.read_text(encoding="utf-8") + "\n\n新增一段。", encoding="utf-8")
    report_b = migration_env.importer.dry_run(tmp_workspace)
    assert report_b.run_id != report_a.run_id
    with pytest.raises(ViolationError) as exc:
        migration_env.importer.apply(
            tmp_workspace, project_id=project, confirm_run_id=report_b.run_id
        )
    assert "非空" in exc.value.why


def test_failed_report_does_not_write(
    fixture_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    report = migration_env.importer.dry_run(fixture_workspace)
    project = _fresh_project(migration_env)
    with pytest.raises(ViolationError):
        migration_env.importer.apply(
            fixture_workspace, project_id=project, confirm_run_id=report.run_id
        )
    assert migration_env.service.get_branch(project, "main").head_commit_id is None


# --------------------------------------------------------------------------
# T17.3 校验
# --------------------------------------------------------------------------
def test_validation_covers_all_checks(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    report = migration_env.importer.dry_run(resolved_workspace)
    project = _fresh_project(migration_env)
    result = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    checks = {entry.check: entry for entry in result.validation.entries}
    for name in (
        "story_artifacts",
        "chapters",
        "no_dangling_ids",
        "legacy_revisions_retained",
        "runtime_records_archived",
    ):
        assert checks[name].ok, f"校验项未通过：{name} -> {checks[name]}"
    assert any(name.startswith("chapter_hash:") for name in checks)


def test_legacy_revisions_carried_on_chapter(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    from hastings.domain.manuscript.catalog import ManuscriptCatalog
    from hastings.domain.story.ids import deterministic_story_id

    report = migration_env.importer.dry_run(resolved_workspace)
    project = _fresh_project(migration_env)
    result = migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    snapshot = migration_env.service.get_snapshot(project, CommitId(result.new_head))
    catalog = ManuscriptCatalog.from_snapshot(snapshot)
    ch01 = deterministic_story_id(ms_kinds.KIND_CHAPTER, "v1:chapter:ch01")
    entry = catalog.chapter_entry(ch01)
    assert entry is not None
    revisions = entry.content["v1"]["revisions"]
    assert len(revisions) == 1 and revisions[0]["revision"] == 1


# --------------------------------------------------------------------------
# 幂等键与源指纹
# --------------------------------------------------------------------------
def test_manifest_hash_tracks_content(tmp_workspace: StoryWorkspace) -> None:
    before = manifest_hash(tmp_workspace)
    assert before == manifest_hash(tmp_workspace)
    (tmp_workspace.story_dir / "sessions.json").write_text('{"sessions": []}', encoding="utf-8")
    assert manifest_hash(tmp_workspace) != before


def test_import_key_binds_schema_version() -> None:
    assert import_key("abc", 1) == "v1-import:1:abc"
    assert import_key("abc", 2) != import_key("abc", 1)


def test_plan_carries_story_and_manuscript_changes(resolved_workspace: StoryWorkspace) -> None:
    plan = plan_migration(resolved_workspace)
    assert plan.report.counts.chapters == 3
    assert plan.story_proposal, "故事提案不应为空"
    chapter_changes = [c for c in plan.manuscript_changes if c.kind == ms_kinds.KIND_CHAPTER]
    assert len(chapter_changes) == 3
    assert len(plan.manuscript_changes) > 3


def test_source_dir_untouched_by_full_flow(
    resolved_workspace: StoryWorkspace, migration_env: MigrationEnv
) -> None:
    root = resolved_workspace.story_dir
    before = {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in _all_files(resolved_workspace)
    }
    report = migration_env.importer.dry_run(resolved_workspace)
    project = _fresh_project(migration_env)
    migration_env.importer.apply(
        resolved_workspace, project_id=project, confirm_run_id=report.run_id
    )
    after = {
        rel: hashlib.sha256((root / rel).read_bytes()).hexdigest()
        for rel in _all_files(resolved_workspace)
    }
    assert after == before, "迁移不应改动旧工作区（不原地修改旧文件）"

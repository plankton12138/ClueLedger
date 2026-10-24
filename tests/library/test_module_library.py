"""全局模块库：实例、不可变版本、冲突与恢复（0.8 B）。

契约：同一种类型可以有多份实例；发布推进 head 但不改旧版本；别人先发布了就冲突；
请求键让重试不产生第二版；中断后按请求键恢复已写好的版本，而不是再造一份。
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.records import parse_entry
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.materials import MaterialLedger

_KEY = "req-00000001"


def _library(tmp_path: Path) -> ModuleLibrary:
    root = tmp_path / "模块库"
    return ModuleLibrary(root, cache_file=tmp_path / "app-cache" / "library-catalog.json")


def _entry(body: str = "法医，与死者相识。") -> EntryDraft:
    return EntryDraft(
        entry_id="lin",
        kind="character",
        title="林静",
        body=body,
        fields={"roles": ["suspect"]},
    )


def _create(lib: ModuleLibrary, *, key: str = _KEY, name: str = "主角团") -> dict[str, object]:
    return lib.create_module(kind="characters", name=name, entries=[_entry()], request_key=key)


def test_same_kind_allows_multiple_instances(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    first = _create(lib, key="req-char-1")
    second = _create(lib, key="req-char-2", name="反派")

    assert first["module"]["module_id"] != second["module"]["module_id"]  # type: ignore[index]
    assert first["module"]["head"] != second["module"]["head"]  # type: ignore[index]
    page = lib.modules(kind="characters")
    assert page["total"] == 2
    assert len(page["items"]) == 2


def test_publishing_appends_an_immutable_revision(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    first = str(created["module"]["head"])  # type: ignore[index]

    second = lib.publish(
        module_id=module_id,
        base_revision=first,
        expected_head=first,
        entries=[_entry("法医，也是死者的旧识。")],
        request_key="req-publish-2",
    )

    assert second.parent == first
    assert lib.module(module_id).head == second.revision_id
    old = lib.read_entry(module_id=module_id, revision_id=first, entry_id="lin")
    new = lib.read_entry(module_id=module_id, revision_id=second.revision_id, entry_id="lin")
    assert "旧识" not in str(old["text"])
    assert "旧识" in str(new["text"])


def test_stale_head_is_rejected(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    first = str(created["module"]["head"])  # type: ignore[index]
    lib.publish(
        module_id=module_id,
        base_revision=first,
        expected_head=first,
        entries=[_entry("改过一次。")],
        request_key="req-publish-2",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        lib.publish(
            module_id=module_id,
            base_revision=first,
            expected_head=first,
            entries=[_entry("又改一次。")],
            request_key="req-publish-3",
        )
    assert excinfo.value.code == "MODULE_HEAD_STALE_CONFLICT"


def test_base_revision_must_be_the_current_head(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    first = str(created["module"]["head"])  # type: ignore[index]
    second = lib.publish(
        module_id=module_id,
        base_revision=first,
        expected_head=first,
        entries=[_entry("第二版。")],
        request_key="req-publish-2",
    )

    with pytest.raises(WorkspaceError) as excinfo:
        lib.publish(
            module_id=module_id,
            base_revision=first,
            expected_head=second.revision_id,
            entries=[_entry("基于旧版重写。")],
            request_key="req-publish-3",
        )
    assert excinfo.value.code == "MODULE_HEAD_STALE_CONFLICT"
    # 冲突不产生新版本，旧版仍是当前 head。
    assert lib.module(module_id).head == second.revision_id


def test_request_key_replays_and_refuses_other_payloads(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    first = str(created["module"]["head"])  # type: ignore[index]

    args = dict(
        module_id=module_id,
        base_revision=first,
        expected_head=first,
        entries=[_entry("同一请求。")],
        request_key="req-replay-1",
    )
    once = lib.publish(**args)  # type: ignore[arg-type]
    twice = lib.publish(**args)  # type: ignore[arg-type]
    assert once.revision_id == twice.revision_id
    assert lib.revisions(module_id=module_id)["total"] == 2

    with pytest.raises(WorkspaceError) as excinfo:
        lib.publish(
            module_id=module_id,
            base_revision=once.revision_id,
            expected_head=once.revision_id,
            entries=[_entry("换了内容。")],
            request_key="req-replay-1",
        )
    assert excinfo.value.code == "REQUEST_KEY_CONFLICT"


def test_interrupted_publish_resumes_the_same_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    first = str(created["module"]["head"])  # type: ignore[index]

    original = lib.storage.write_revision

    def interrupted(*args: object, **kwargs: object) -> bool:
        original(*args, **kwargs)  # type: ignore[arg-type]
        raise WorkspaceError(
            code="LIBRARY_WRITE_FAILED",
            where="test.interrupt",
            why="模拟版本写完但 head 还没登记",
            fix="重试同一请求",
        )

    monkeypatch.setattr(lib.storage, "write_revision", interrupted)
    with pytest.raises(WorkspaceError):
        lib.publish(
            module_id=module_id,
            base_revision=first,
            expected_head=first,
            entries=[_entry("中断后重试。")],
            request_key="req-interrupt-1",
        )
    assert lib.module(module_id).head == first

    monkeypatch.setattr(lib.storage, "write_revision", original)
    resumed = lib.publish(
        module_id=module_id,
        base_revision=first,
        expected_head=first,
        entries=[_entry("中断后重试。")],
        request_key="req-interrupt-1",
    )
    assert lib.module(module_id).head == resumed.revision_id
    # 没有留下第二份半成品版本。
    assert lib.revisions(module_id=module_id)["total"] == 2


def test_fork_creates_an_independent_module(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]

    forked = lib.fork(
        module_id=module_id,
        base_revision=head,
        name="主角团（王城线）",
        request_key="req-fork-1",
    )
    new_id = str(forked["module"]["module_id"])  # type: ignore[index]
    assert new_id != module_id
    assert forked["module"]["forked_from"] == {"module_id": module_id, "revision_id": head}  # type: ignore[index]

    lib.publish(
        module_id=new_id,
        base_revision=str(forked["module"]["head"]),  # type: ignore[index]
        expected_head=str(forked["module"]["head"]),  # type: ignore[index]
        entries=[_entry("只在变体里改。")],
        request_key="req-fork-publish",
    )
    assert lib.module(module_id).head == head
    original = lib.read_entry(module_id=module_id, revision_id=head, entry_id="lin")
    assert "变体" not in str(original["text"])


def test_trash_keeps_versions_readable_and_blocks_publishing(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]

    lib.trash(module_id=module_id, expected_head=head, request_key="req-trash-1")
    assert lib.modules()["total"] == 0
    assert lib.modules(include_deleted=True)["total"] == 1
    assert "林静" in str(
        lib.read_entry(module_id=module_id, revision_id=head, entry_id="lin")["text"]
    )

    with pytest.raises(WorkspaceError) as excinfo:
        lib.publish(
            module_id=module_id,
            base_revision=head,
            expected_head=head,
            entries=[_entry("删了还想发。")],
            request_key="req-trash-publish",
        )
    assert excinfo.value.code == "MODULE_TRASHED_CONFLICT"

    lib.restore(module_id=module_id, expected_head=head, request_key="req-restore-1")
    assert lib.modules()["total"] == 1


def test_read_entry_records_only_the_provided_range(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]
    ledger = MaterialLedger()

    view = lib.read_entry(
        module_id=module_id, revision_id=head, entry_id="lin", start=0, end=20, ledger=ledger
    )
    assert view["end"] == 20
    assert view["truncated"] is True

    path = f"library/{lib.library_id}/{module_id}/{head}/lin"
    coverage = ledger.coverage(path)
    assert coverage is not None
    # manifest 校验与目录读取不算读过：只有返回的 20 个字符入账。
    assert coverage.spans == ((0, 20),)


def test_tampered_entry_is_rejected(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]
    lib.storage.entry_path(module_id, head, "lin").write_text("被外部改过", encoding="utf-8")

    with pytest.raises(WorkspaceError) as excinfo:
        lib.read_entry(module_id=module_id, revision_id=head, entry_id="lin")
    assert excinfo.value.code == "MODULE_CONTENT_INVALID"

    with pytest.raises(WorkspaceError):
        lib.revision(module_id=module_id, revision_id=head)


def test_catalog_rebuilds_when_the_cache_is_missing_or_corrupt(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]
    cache = tmp_path / "app-cache" / "library-catalog.json"
    lib.revision(module_id=module_id, revision_id=head)  # 先建一次缓存
    cache.unlink()
    rebuilt = lib.revision(module_id=module_id, revision_id=head)
    assert [item.entry_id for item in rebuilt.entries] == ["lin"]
    assert cache.is_file()

    cache.write_text("{ 不是 JSON", encoding="utf-8")
    again = lib.revision(module_id=module_id, revision_id=head)
    assert again.view()["entries"][0]["title"] == "林静"  # type: ignore[index]


def test_same_kind_instances_are_independent(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    first = _create(lib, key="req-char-1")
    second = _create(lib, key="req-char-2", name="反派")
    first_id = str(first["module"]["module_id"])  # type: ignore[index]
    first_head = str(first["module"]["head"])  # type: ignore[index]
    second_id = str(second["module"]["module_id"])  # type: ignore[index]
    second_head = str(second["module"]["head"])  # type: ignore[index]

    lib.publish(
        module_id=first_id,
        base_revision=first_head,
        expected_head=first_head,
        entries=[_entry("只改主角团。")],
        request_key="req-char-1-publish",
    )
    assert lib.module(second_id).head == second_head


def test_workspace_observations_cannot_be_stored(tmp_path: Path) -> None:
    """生死、回收这些属于作品观察，不能写进库的公共条目。"""
    lib = _library(tmp_path)
    with pytest.raises(WorkspaceError) as excinfo:
        lib.create_module(
            kind="characters",
            name="主角团",
            entries=[
                EntryDraft(
                    entry_id="lin",
                    kind="character",
                    title="林静",
                    body="法医。",
                    fields={"alive": False},
                )
            ],
            request_key="req-alive-1",
        )
    assert excinfo.value.code == "RECORD_FIELD_UNKNOWN"


def test_cursor_is_bound_to_the_query(tmp_path: Path) -> None:
    lib = _library(tmp_path)
    _create(lib, key="req-char-1")
    _create(lib, key="req-char-2", name="反派")

    page = lib.modules(limit=1)
    assert page["next_cursor"]

    with pytest.raises(WorkspaceError) as excinfo:
        lib.modules(q="主角", limit=1, cursor=str(page["next_cursor"]))
    assert excinfo.value.code == "QUERY_CURSOR_STALE_CONFLICT"

    second = lib.modules(limit=1, cursor=str(page["next_cursor"]))
    assert len(second["items"]) == 1
    assert second["items"][0]["module_id"] != page["items"][0]["module_id"]  # type: ignore[index]


def test_record_parser_rejects_bad_headers() -> None:
    with pytest.raises(WorkspaceError) as excinfo:
        parse_entry("没有头部", module_kind="characters")
    assert excinfo.value.code == "RECORD_HEADER_INVALID"

    with pytest.raises(WorkspaceError) as excinfo:
        parse_entry(
            "---\nid: lin\nschema_version: 1\nkind: world_rule\ntitle: 林静\n---\n法医。",
            module_kind="characters",
        )
    assert excinfo.value.code == "RECORD_KIND_INVALID"

    with pytest.raises(WorkspaceError) as excinfo:
        parse_entry(
            "---\nid: lin\nschema_version: 1\nkind: character\ntitle: 林静\n---\n",
            module_kind="characters",
        )
    assert excinfo.value.code == "RECORD_CONTENT_EMPTY"


def test_concurrent_publishes_let_only_one_through(tmp_path: Path) -> None:
    """同一 head 上的两次发布：一个成功，另一个明确冲突，不静默覆盖。"""
    lib = _library(tmp_path)
    created = _create(lib)
    module_id = str(created["module"]["module_id"])  # type: ignore[index]
    head = str(created["module"]["head"])  # type: ignore[index]

    outcomes: list[tuple[str, str]] = []

    def attempt(index: int) -> None:
        try:
            revision = lib.publish(
                module_id=module_id,
                base_revision=head,
                expected_head=head,
                entries=[_entry(f"并发第 {index} 版。")],
                request_key=f"req-concurrent-{index}",
            )
            outcomes.append(("ok", revision.revision_id))
        except WorkspaceError as exc:
            outcomes.append(("err", exc.code))

    threads = [threading.Thread(target=attempt, args=(index,)) for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    kinds = sorted(kind for kind, _ in outcomes)
    assert kinds == ["err", "ok"]
    assert next(value for kind, value in outcomes if kind == "err") == (
        "MODULE_HEAD_STALE_CONFLICT"
    )
    assert lib.revisions(module_id=module_id)["total"] == 2


def test_opening_a_legacy_workspace_writes_nothing(tmp_path: Path) -> None:
    """旧作品打开只读：不建库、不改设定文件；库只在第一次作者操作时创建。"""
    root = tmp_path / "旧作品"
    workspace = Workspace.create(root)
    workspace.write(
        "设定集/线索.md", "门锁有擦痕。", expected_digest=workspace.read("设定集/线索.md").digest
    )
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}

    library = ModuleLibrary(tmp_path / "库")
    assert library.modules()["total"] == 0
    assert library.identity is None

    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert not (tmp_path / "库" / "library.json").exists()

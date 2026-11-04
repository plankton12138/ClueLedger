"""服务层结构注入与确定性对账测试（v1.1 M3，Q2-C）。

契约（q0-contracts 第 3、4 节）：目标章号只来自任务元数据；缺章默认改派最早缺章；
台账与结构「已写」标记冲突以台账为准。直接驱动 ``TurnService`` 的注入方法，
不走模型。
"""

from __future__ import annotations

from pathlib import Path

from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.files import Workspace
from hastings.workspace.materials import ContextSnapshot
from hastings.workspace.prose_ledger import LEDGER_FILENAME, ProseLedger, save_ledger
from hastings.workspace.service import TurnInfo, TurnService
from hastings.workspace.state import ConversationStore, conversation_root

_STRUCTURE = "## 铁律\n不写超自然力量\n\n## 骨架\n- 第 1 章：雨夜"


class _Provider:
    """占位 provider：注入方法不触模型，工厂不会被真正调用。"""


def _service(tmp_path: Path) -> tuple[TurnService, Workspace]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))

    def factory() -> ModelSnapshot:
        return ModelSnapshot(
            connection_id="test",
            display_name="测试连接",
            model="fake",
            provider=_Provider(),  # type: ignore[arg-type]
        )

    return TurnService(workspace, store, factory), workspace


def _info(*, selected_path: str | None = None) -> TurnInfo:
    context = None
    if selected_path is not None:
        context = ContextSnapshot(
            selected_path=selected_path, references=(), observed_digests={}
        )
    return TurnInfo(
        turn_id="t1", conversation_id="c1", started_at="now", context=context
    )


def _seed_ledger(workspace: Workspace, chapters: dict[int, str]) -> None:
    save_ledger(workspace.root / LEDGER_FILENAME, ProseLedger(chapters=chapters))


def test_no_structure_no_ledger_no_target_injects_nothing(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    structure_text, ledger_text, snapshot = service._structure_injection(_info())

    assert structure_text == ""
    assert ledger_text == ""
    assert snapshot.to_dict() == {
        "structure_injected": False,
        "iron_rules_count": 0,
        "iron_rules_degraded": False,
        "ledger_injected": False,
        "ledger_chapters": [],
        "reconcile": None,
        "modules_selected": [],
        "checklist": [],
    }


def test_structure_partition_text_and_snapshot(tmp_path: Path) -> None:
    service, workspace = _service(tmp_path)
    workspace.write("STRUCTURE.md", _STRUCTURE, expected_digest=None)

    structure_text, _, snapshot = service._structure_injection(_info())

    assert "- 不写超自然力量" in structure_text
    assert "STYLE.md" not in structure_text
    assert snapshot.structure_injected is True
    assert snapshot.iron_rules_count == 1
    assert snapshot.iron_rules_degraded is False


def test_missing_marker_is_reported_as_degraded(tmp_path: Path) -> None:
    service, workspace = _service(tmp_path)
    workspace.write("STRUCTURE.md", "# 我的书\n\n三幕结构。", expected_digest=None)

    _, _, snapshot = service._structure_injection(_info())

    assert snapshot.iron_rules_degraded is True


def test_ledger_facts_are_injected_with_priority_note(tmp_path: Path) -> None:
    service, workspace = _service(tmp_path)
    _seed_ledger(workspace, {1: "rev-aaaaaaaa-1"})

    _, ledger_text, snapshot = service._structure_injection(_info())

    assert "第 1 章｜最近入档修订 rev-aaaa" in ledger_text
    assert "以本台账为准" in ledger_text
    assert snapshot.ledger_injected is True
    assert snapshot.ledger_chapters == (1,)


def test_missing_earlier_chapter_redirects_at_assembly(tmp_path: Path) -> None:
    """台账登记了第 1 章、目标第 3 章：装配层确定性改派第 2 章之前的最早缺章。"""
    service, workspace = _service(tmp_path)
    workspace.write("正文/0003 线索.md", "第三章草稿", expected_digest=None)
    _seed_ledger(workspace, {1: "rev-1"})

    _, ledger_text, snapshot = service._structure_injection(
        _info(selected_path="正文/0003 线索.md")
    )

    assert snapshot.reconcile_action == "redirect"
    assert snapshot.reconcile_target == 3
    assert snapshot.reconcile_redirected_to == 2
    assert "改派第 2 章" in ledger_text
    assert "不要写第 3 章" in ledger_text


def test_target_reached_with_full_ledger_writes_without_note(tmp_path: Path) -> None:
    service, workspace = _service(tmp_path)
    _seed_ledger(workspace, {1: "rev-1"})

    _, ledger_text, snapshot = service._structure_injection(
        _info(selected_path="正文/0002 追查.md")
    )

    assert snapshot.reconcile_action == "write"
    assert snapshot.reconcile_redirected_to is None
    assert "改派" not in ledger_text


def test_modifying_an_archived_chapter_is_not_redirected(tmp_path: Path) -> None:
    """目标章本身已入档是合法的修改路径：对账放行，不改派。"""
    service, workspace = _service(tmp_path)
    _seed_ledger(workspace, {1: "rev-1", 2: "rev-2"})

    _, _, snapshot = service._structure_injection(
        _info(selected_path="正文/0002 追查.md")
    )

    assert snapshot.reconcile_action == "write"


def test_focus_chapter_path_counts_as_metadata_target(tmp_path: Path) -> None:
    """章节/选段焦点也是任务元数据：焦点第 4 章、台账为空 → 改派第 1 章。"""
    from hastings.workspace.materials import DiscussionFocus

    service, workspace = _service(tmp_path)
    _seed_ledger(workspace, {})
    focus = DiscussionFocus(kind="chapter", path="正文/0004 对峙.md")
    context = ContextSnapshot(
        selected_path=None, references=(), observed_digests={}, discussion_focus=focus
    )
    info = TurnInfo(turn_id="t1", conversation_id="c1", started_at="now", context=context)

    _, ledger_text, snapshot = service._structure_injection(info)

    assert snapshot.reconcile_action == "redirect"
    assert snapshot.reconcile_redirected_to == 1
    assert "改派第 1 章" in ledger_text


def test_non_chapter_paths_do_not_trigger_reconcile(tmp_path: Path) -> None:
    service, workspace = _service(tmp_path)
    _seed_ledger(workspace, {1: "rev-1"})

    _, _, snapshot = service._structure_injection(_info(selected_path="STRUCTURE.md"))

    assert snapshot.reconcile_action == ""
    assert snapshot.to_dict()["reconcile"] is None


def test_allow_skip_is_not_wired_from_metadata(tmp_path: Path) -> None:
    """跳章只能由作者明确确认（M5 流程承接）：装配层默认不放行跳章。"""
    service, workspace = _service(tmp_path)

    _, _, snapshot = service._structure_injection(
        _info(selected_path="正文/0003 线索.md")
    )

    assert snapshot.skipped_chapters == ()
    assert snapshot.reconcile_action == "redirect"

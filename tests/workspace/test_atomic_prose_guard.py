"""服务层原子性兜底测试（v1.1 M4，Q3-B）。

契约（q0-contracts 第 5、7 节）：写作回合预期正文未产出 → ``EXPECTED_PROSE_MISSING``
任务失败归因，任何部分不入档。判定全部来自任务元数据与事件收据，不解析对话原话。
直接驱动 ``TurnService`` 的判定方法，不走模型。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hastings.workspace.files import Workspace
from hastings.workspace.materials import ContextSnapshot
from hastings.workspace.service import TurnInfo, TurnService
from hastings.workspace.state import ConversationStore, conversation_root
from hastings.workspace.story_context import StoryRequest


class _Provider:
    """占位 provider：守卫方法不触模型。"""


def _service(tmp_path: Path) -> tuple[TurnService, Workspace]:
    workspace = Workspace.create(tmp_path / "雪山旅馆")
    store = ConversationStore(conversation_root(tmp_path / "state", "w1"))

    def factory() -> Any:
        raise AssertionError("守卫方法不触模型")

    return TurnService(workspace, store, factory), workspace  # type: ignore[arg-type,return-value]


class _Run:
    """RunContext 的判定面替身：守卫只读 story / story_candidate / pending_question。"""

    def __init__(self, *, purpose: str, documents: dict[str, str] | None) -> None:
        self.story = StoryRequest(purpose=purpose) if purpose else None
        self.pending_question = None
        self.documents = documents or {}

    @property
    def story_candidate(self: Any) -> Any:
        return None if not self.documents else _Candidate(self.documents)


class _Candidate:
    def __init__(self, documents: dict[str, str]) -> None:
        self.documents = documents


def _info(tmp_path: Path, service: TurnService, *, selected_path: str | None) -> TurnInfo:
    context = None
    if selected_path is not None:
        context = ContextSnapshot(
            selected_path=selected_path, references=(), observed_digests={}
        )
    info = TurnInfo(
        turn_id="t1", conversation_id="c1", started_at="now", context=context
    )
    # 直接落一条写入收据：模拟本轮模型已经交了结构/模块更新。
    service.store.append(
        info.conversation_id,
        {"type": "file_changed", "turn_id": "t1", "path": "STRUCTURE.md"},
    )
    return info


def test_writes_without_prose_is_detected(tmp_path: Path) -> None:
    """写作回合 + 章节目标 + 有写入收据 + 无正文候选 → 兜底命中。"""
    service, _ = _service(tmp_path)
    info = _info(tmp_path, service, selected_path="正文/0003 对峙.md")
    run = _Run(purpose="write", documents=None)

    assert service._expected_prose_missing(info, run) is True


def test_prose_candidate_present_passes(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    info = _info(tmp_path, service, selected_path="正文/0003 对峙.md")
    run = _Run(purpose="write", documents={"正文/0003 对峙.md": "# 第三章\n\n正文。"})

    assert service._expected_prose_missing(info, run) is False


def test_design_purpose_or_no_target_never_triggers(tmp_path: Path) -> None:
    """构思用途与无章节目标不触发；这是写作回合专属的原子性兜底。"""
    service, _ = _service(tmp_path)
    info = _info(tmp_path, service, selected_path="正文/0003 对峙.md")

    assert service._expected_prose_missing(info, _Run(purpose="design", documents=None)) is False

    no_target = TurnInfo(
        turn_id="t1", conversation_id="c1", started_at="now", context=None
    )
    service.store.append(
        no_target.conversation_id,
        {"type": "file_changed", "turn_id": "t1", "path": "STRUCTURE.md"},
    )
    missing = _Run(purpose="write", documents=None)
    assert service._expected_prose_missing(no_target, missing) is False


def test_discussion_reply_without_writes_is_not_a_failure(tmp_path: Path) -> None:
    """模型只回复讨论、没有任何写入：不是「只交文件更新不交正文」，不算失败。"""
    service, _ = _service(tmp_path)
    context = ContextSnapshot(
        selected_path="正文/0003 对峙.md", references=(), observed_digests={}
    )
    info = TurnInfo(turn_id="t1", conversation_id="c1", started_at="now", context=context)

    assert service._expected_prose_missing(info, _Run(purpose="write", documents=None)) is False
    assert service._turn_had_file_writes(info) is False


def test_pending_question_is_not_a_failure(tmp_path: Path) -> None:
    """模型已记录问题等作者回答：走 needs_input，不按兜底归因。"""
    service, _ = _service(tmp_path)
    info = _info(tmp_path, service, selected_path="正文/0003 对峙.md")
    run = _Run(purpose="write", documents=None)
    run.pending_question = "这一章的收束放在谁身上？"

    assert service._expected_prose_missing(info, run) is False


def test_revise_purpose_is_also_guarded(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)
    info = _info(tmp_path, service, selected_path="正文/0002 追查.md")

    assert service._expected_prose_missing(info, _Run(purpose="revise", documents=None)) is True


def test_write_events_from_other_turns_are_ignored(tmp_path: Path) -> None:
    """收据只认本轮事件：上一轮的写入不给这一轮兜底当依据。"""
    service, _ = _service(tmp_path)
    context = ContextSnapshot(
        selected_path="正文/0003 对峙.md", references=(), observed_digests={}
    )
    info = TurnInfo(turn_id="t2", conversation_id="c1", started_at="now", context=context)
    service.store.append(
        info.conversation_id,
        {"type": "file_changed", "turn_id": "t1", "path": "STRUCTURE.md"},
    )

    assert service._turn_had_file_writes(info) is False
    assert service._expected_prose_missing(info, _Run(purpose="write", documents=None)) is False

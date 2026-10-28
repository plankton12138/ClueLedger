"""状态发布、任务时点与写作视图的约束回归，不调用模型。"""

import pytest

from hastings.workspace.agent import RunContext, build_tools, build_turn_assembly
from hastings.workspace.character_states import (
    CharacterStateStore,
    StateSnapshot,
    build_roster,
    publish_states,
)
from hastings.workspace.clues import ClueEntry, ClueStore, publish_clues
from hastings.workspace.disclosure import (
    DisclosureStore,
    ObservedEvidence,
    ObservedFact,
    build_disclosure_state,
    publish_observed,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.glossary import GlossaryStore
from hastings.workspace.service import WriteSession
from hastings.workspace.timeline import TimelineEntry, TimelineStore, publish_timeline

PERSON = "设定集/角色小传/林静.md"
FIRST = "正文/0001 雨夜.md"
LATER = "正文/0009 揭晓.md"


@pytest.fixture
def setup(tmp_path):
    workspace = Workspace.create(tmp_path / "book")
    workspace.write(
        PERSON,
        "---\nrevealed: [9:秘密身份]\nplanned: [12:最终真相]\n---\n# 林静\n",
        expected_digest=None,
    )
    workspace.write(FIRST, "# 雨夜\n\n林静在书房。\n", expected_digest=None)
    workspace.write(LATER, "# 揭晓\n\n林静死在书房。\n", expected_digest=None)
    run = RunContext(workspace, WriteSession(workspace))
    stores = {
        "disclosure_store": DisclosureStore(tmp_path / "state"),
        "roster_store": CharacterStateStore(tmp_path / "state"),
        "timeline_store": TimelineStore(tmp_path / "state"),
        "clue_store": ClueStore(tmp_path / "state"),
        "glossary_store": GlossaryStore(tmp_path / "state"),
    }
    tools = {
        t.name: t
        for t in build_tools(workspace, run, mode="state", index_allowed=(PERSON,), **stores)
    }
    return workspace, run, stores, tools


def evidence(workspace, path=FIRST):
    src = workspace.read(path)
    start = src.text.index("\n\n") + 2
    return ObservedEvidence(path, src.digest, start, len(src.text), src.text[start:])


def payload(ev):
    return {
        "path": ev.path,
        "digest": ev.digest,
        "start": ev.start,
        "end": ev.end,
        "quote": ev.quote,
    }


def arguments(workspace, tool, ev):
    entity = {"entity": PERSON, "entity_digest": workspace.read(PERSON).digest}
    proof = payload(ev)
    return {
        "save_disclosure": {
            **entity,
            "facts": [{"fact": "书房里的身份", "chapter": 1, "evidence": proof}],
        },
        "save_character_state": {
            **entity,
            "states": [{"chapter": 1, "location": "书房", "alive": True, "evidence": proof}],
        },
        "save_timeline": {"entries": [{"chapter": 1, "when": "雨夜", "evidence": proof}]},
        "save_clues": {
            "entries": [
                {"clue": "书房里的人", "planted_chapter": 1, "status": "open", "evidence": proof}
            ]
        },
        "save_glossary_term": {
            "term": "书房",
            "kind": "place",
            "first_chapter": 1,
            "evidence": proof,
        },
    }[tool]


PUBLISHERS = (
    "save_disclosure",
    "save_character_state",
    "save_timeline",
    "save_clues",
    "save_glossary_term",
)


@pytest.mark.anyio
@pytest.mark.parametrize("tool", PUBLISHERS)
async def test_publication_requires_actual_read_range(setup, tool):
    workspace, _, stores, tools = setup
    await tools["read_file"].execute("entity", {"path": PERSON})
    await tools["read_file"].execute("prefix", {"path": FIRST, "limit": 1})
    args = arguments(workspace, tool, evidence(workspace))
    with pytest.raises(WorkspaceError, match="REFERENCE_NOT_FULLY_READ_CONFLICT"):
        await tools[tool].execute("unread", args)
    assert all(not store.entries() for store in stores.values())
    await tools["read_file"].execute("full", {"path": FIRST})
    await tools[tool].execute("read", args)
    import json

    # 旧规则的缓存不能继续当作已验证状态使用；文件内容保留，读取按空降级。
    store = next(s for s in stores.values() if s.entries())
    old = json.loads(store.file.read_text(encoding="utf-8"))
    old["recipe_version"] = 999 if tool == "save_clues" else 1
    store.file.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    if tool == "save_clues":
        # 1.0 线索包含不可丢弃的身份与关系，未知格式不能按空覆盖。
        with pytest.raises(WorkspaceError, match="CLUE_STATE_FAILED"):
            store.entries()
    else:
        assert store.entries() == ()


@pytest.mark.anyio
@pytest.mark.parametrize("tool", PUBLISHERS)
async def test_setting_and_wrong_chapter_cannot_be_published(setup, tool):
    workspace, _, stores, tools = setup
    truth = "设定集/真相.md"
    workspace.write(truth, "幕后主使是林静。", expected_digest=None)
    await tools["read_file"].execute("entity", {"path": PERSON})
    for path in (truth, LATER):
        await tools["read_file"].execute("source", {"path": path})
        src = workspace.read(path)
        ev = ObservedEvidence(path, src.digest, 0, len(src.text), src.text)
        with pytest.raises(WorkspaceError, match="STATE_EVIDENCE_INVALID"):
            await tools[tool].execute("wrong", arguments(workspace, tool, ev))
    assert all(not store.entries() for store in stores.values())


@pytest.mark.anyio
async def test_missing_alive_cannot_overwrite_death(setup):
    workspace, _, stores, tools = setup
    for path in (PERSON, FIRST):
        await tools["read_file"].execute("read", {"path": path})
    args = arguments(workspace, "save_character_state", evidence(workspace))
    args["states"][0]["alive"] = False
    await tools["save_character_state"].execute("death", args)
    del args["states"][0]["alive"]
    with pytest.raises(WorkspaceError):
        await tools["save_character_state"].execute("omit", args)
    assert build_roster(workspace, stores["roster_store"]).characters[0].dead


def test_one_stale_fact_does_not_drop_independent_valid_fact(setup):
    workspace, _, stores, _ = setup
    store = stores["disclosure_store"]
    facts = tuple(
        ObservedFact(
            PERSON, workspace.read(PERSON).digest, path, chapter, evidence(workspace, path)
        )
        for path, chapter in ((FIRST, 1), (LATER, 9))
    )
    publish_observed(workspace, store, facts)
    src = workspace.read(FIRST)
    workspace.write(FIRST, src.text + "补充。", expected_digest=src.digest)
    state = build_disclosure_state(workspace, store)
    assert state.entities[0].verified == 1
    assert store.status(workspace, facts[1]) == "valid"


def test_selected_chapter_filters_all_state_domains(setup):
    workspace, _, stores, _ = setup
    ev = evidence(workspace, LATER)
    publish_states(
        workspace,
        stores["roster_store"],
        (StateSnapshot(PERSON, workspace.read(PERSON).digest, 9, "书房", False, "", ev),),
    )
    publish_timeline(
        workspace, stores["timeline_store"], (TimelineEntry(9, "第九章深夜", "", "书房", ev),)
    )
    publish_clues(
        workspace, stores["clue_store"], (ClueEntry("第九章脚印", 9, None, "open", "", ev),)
    )
    early = build_turn_assembly(workspace, selected_path=FIRST, **stores)
    for secret in ("秘密身份", "已死亡", "第九章深夜", "第九章脚印"):
        assert secret not in early.text
    unknown = build_turn_assembly(workspace, **stores)
    assert "秘密身份" not in unknown.text
    late = build_turn_assembly(workspace, as_of_chapter=9, **stores)
    assert {"state", "roster", "timeline", "clues"} <= {p.key for p in late.parts}


@pytest.mark.anyio
async def test_writer_query_hides_plans_and_respects_task_cutoff(setup):
    workspace, run, stores, _ = setup
    run.state_chapter = 1
    tools = {t.name: t for t in build_tools(workspace, run, **stores)}
    for args in ({}, {"entity": PERSON}, {"entity": PERSON, "as_of_chapter": 9}):
        result = await tools["read_state"].execute("read", args)
        assert "最终真相" not in result.text
        assert "秘密身份" not in result.text
    run.state_chapter = None
    result = await tools["read_state"].execute("explicit", {"as_of_chapter": 9})
    assert "秘密身份" in result.text and "最终真相" not in result.text


def test_roster_stays_in_budget_with_hidden_rows(setup):
    workspace, _, stores, _ = setup
    snapshots = []
    for index in range(8):
        path = f"设定集/角色小传/角色{index}.md"
        workspace.write(path, f"# 角色{index}\n", expected_digest=None)
        snapshots.append(
            StateSnapshot(
                path,
                workspace.read(path).digest,
                1,
                "书房",
                False,
                "立场" * 30,
                evidence(workspace),
            )
        )
    publish_states(workspace, stores["roster_store"], snapshots)
    result = build_turn_assembly(workspace, selected_path=FIRST, **stores)
    part = next(p for p in result.parts if p.key == "roster")
    assert len(part.text) <= part.budget
    assert "已死亡" in part.text and "未列入" in part.text


def test_future_resolution_is_open_when_revising_early_chapter(setup):
    workspace, _, stores, _ = setup
    entry = ClueEntry(
        "门口脚印", 1, 9, "resolved", "", evidence(workspace), 9, evidence(workspace, LATER)
    )
    publish_clues(workspace, stores["clue_store"], (entry,))
    from hastings.workspace.clues import build_clues

    early = build_clues(workspace, stores["clue_store"], as_of_chapter=1)
    assert early.open[0].status == "open" and early.open[0].resolution_chapter is None
    assert stores["clue_store"].entries()[0].status == "resolved"


def test_cutoff_input_is_validated_and_preserved(setup):
    from pydantic import ValidationError

    from hastings.workspace.api import ContextInput
    from hastings.workspace.materials import freeze_context
    from hastings.workspace.service import _same_materials

    workspace, _, _, _ = setup
    for invalid in (-1, True, "3"):
        with pytest.raises(ValidationError):
            ContextInput(state_chapter=invalid)
    request = ContextInput(state_chapter=1).to_request()
    snapshot = freeze_context(workspace, request)
    assert snapshot.to_dict()["state_chapter"] == 1
    assert _same_materials(snapshot.to_dict(), request, "chat", (), (), "chat", (), ())
    assert not _same_materials(
        snapshot.to_dict(),
        ContextInput(state_chapter=9).to_request(),
        "chat",
        (),
        (),
        "chat",
        (),
        (),
    )

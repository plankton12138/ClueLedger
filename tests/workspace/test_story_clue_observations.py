"""正文候选提交后的线索观察：计划与正文证据使用同一身份。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hastings.workspace.clue_models import ClueIdentity
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.files import Workspace
from hastings.workspace.story_clue_observations import record_scene_observations
from hastings.workspace.story_clue_views import story_clue_detail
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import EMPTY_REVISION, MAIN_VARIANT_ID, StoryStore

PATH = "正文/0001 灯下.md"
TEXT = "# 灯下\n\n灯下多出一缕烟，桌上的人没有抬头。"


def _seed(workspace: Workspace, store: StoryStore) -> str:
    clue_id = "clue_smoke"
    ledger = ClueLedgerStore(store.root)

    def apply(snapshot: Any) -> dict[str, str]:
        snapshot.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title="灯下的烟",
            clue_revision="rev_smoke",
            created_at="2026-10-31T00:00:00+08:00",
            created_by="author",
        )
        return {"clue_id": clue_id}

    ledger.mutate(
        expected_revision=ledger.read().revision,
        request_key="observations-identity",
        kind="test_identity",
        payload={},
        apply=apply,
    )
    store.commit_change(
        variant_id=MAIN_VARIANT_ID,
        request_key="observations-seed",
        change=StoryChangeSet(
            id="observations-seed",
            base_revision=EMPTY_REVISION,
            operations=[
                {
                    "op": "upsert_clue_plan",
                    "clue_id": clue_id,
                    "plan": {"intention": "让读者先当成雾"},
                },
                {
                    "op": "add_node",
                    "node": {
                        "id": "n1",
                        "title": "灯下",
                        "action": "有人点烟",
                        "clue_uses": [
                            {
                                "clue_id": clue_id,
                                "roles": ["plant"],
                                "presentation": "灯下多出一缕烟",
                                "mode": "action",
                            }
                        ],
                    },
                },
            ],
        ),
    )
    return clue_id


def test_scene_observations_link_plan_and_prose(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    clue_id = _seed(workspace, store)
    workspace.write(PATH, TEXT, expected_digest=None)

    result = record_scene_observations(
        workspace,
        store.root,
        variant_id=MAIN_VARIANT_ID,
        node_ids=("n1",),
        documents={PATH: TEXT},
        task_id="candidate_test",
    )
    assert result["published"] == 1
    assert result["chapters"] == [1]

    detail = story_clue_detail(
        workspace,
        store.root,
        variant_id=MAIN_VARIANT_ID,
        clue_id=clue_id,
    )
    assert detail.plan is not None
    assert len(detail.observations) == 1
    assert detail.observations[0].text == "灯下多出一缕烟"
    assert detail.observations[0].mode == "action"
    assert detail.evidence[0].quote == "灯下多出一缕烟"


def test_missing_quote_records_no_observation(tmp_path: Path) -> None:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace)
    clue_id = _seed(workspace, store)
    workspace.write(PATH, "# 灯下\n\n没有烟。", expected_digest=None)

    result = record_scene_observations(
        workspace,
        store.root,
        variant_id=MAIN_VARIANT_ID,
        node_ids=("n1",),
        documents={PATH: "# 灯下\n\n没有烟。"},
        task_id="candidate_missing",
    )
    assert result["published"] == 0
    detail = story_clue_detail(
        workspace,
        store.root,
        variant_id=MAIN_VARIANT_ID,
        clue_id=clue_id,
    )
    assert detail.observations == []

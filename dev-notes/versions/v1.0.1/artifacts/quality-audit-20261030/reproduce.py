"""Deterministic completion-quality probes; no model or network calls.

Run from the repository root with the existing Python environment.
Only generated workspaces under data/test-tmp are changed.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_context import (
    StoryContextSnapshot,
    assemble_reader_input,
    assert_reader_isolated,
)
from hastings.workspace.story_models import StoryChangeSet
from hastings.workspace.story_store import StoryStore
from hastings.workspace.story_tools import StoryToolScope, build_story_tools

ROOT = Path(__file__).resolve().parents[4]
RUN = ROOT / "data/test-tmp/v101-quality-audit-20261030" / f"probes-{uuid4().hex}"


def commit(store: StoryStore, variant: str, key: str, operations=None, patches=None):
    snapshot = store.read().variant(variant)
    return store.commit_change(
        variant_id=variant,
        request_key=key,
        change=StoryChangeSet(
            id=key,
            base_revision=snapshot.revision if snapshot else "empty",
            operations=operations or [],
            prose_patches=patches or [],
        ),
    )


def seed(name: str):
    root = RUN / name
    workspace = Workspace.create(root / "book")
    store = StoryStore(root / "state", workspace)
    commit(
        store,
        "main",
        "seed",
        [
            {"op": "add_node", "node": {"id": "n1", "title": "入场", "action": "走进门厅"}},
            {"op": "add_node", "node": {"id": "n2", "title": "结局", "action": "保持原结局"}},
            {"op": "add_node", "node": {"id": "n3", "title": "旁支", "action": "保留旁支"}},
            {
                "op": "add_clue",
                "clue": {"id": "glove", "title": "手套", "author_truth": "管家所留"},
            },
        ],
    )
    return workspace, store


def fork(store: StoryStore, source="main", key="fork"):
    source_variant = store.require_variant(source)
    return store.create_variant(
        name=key,
        source_variant_id=source,
        source_revision=source_variant.revision,
        request_key=key,
    ).result["variant_id"]


def node_update(store: StoryStore, variant: str, node_id: str, action: str):
    node = store.require_variant(variant).nodes[node_id].model_dump(mode="json")
    return {"op": "update_node", "node_id": node_id, "node": {**node, "action": action}}


def tool(store: StoryStore, workspace: Workspace, text: str, name: str, nodes=()):
    scope = StoryToolScope(
        state_root=store.root,
        workspace=workspace,
        frozen=StoryContextSnapshot(
            variant_id="main",
            revision=store.require_variant("main").revision,
            node_ids=nodes,
            purpose="design",
        ),
        writable=True,
        author_text=text,
        operation_id="audit-operation",
        generation=1,
    )
    return next(item for item in build_story_tools(scope) if item.name == name)


def adopt(store: StoryStore, source: str, nodes=()):
    return store.adopt(
        source_variant_id=source,
        source_revision=store.require_variant(source).revision,
        target_variant_id="main",
        target_revision=store.require_variant("main").revision,
        node_ids=nodes,
        replace=True,
        request_key="adopt",
    )


def model_adoption_empty_scope():
    workspace, store = seed("adoption-scope")
    alternative = fork(store)
    clue = store.require_variant(alternative).clues["glove"].model_dump(mode="json")
    commit(
        store,
        alternative,
        "alt-change",
        [
            node_update(store, alternative, "n1", "新的入场"),
            node_update(store, alternative, "n2", "未授权的新结局"),
            {"op": "update_clue", "clue_id": "glove", "clue": {**clue, "author_truth": "客人所留"}},
        ],
    )
    selected_tool = tool(
        store, workspace, "只采用备选中选中的入场节点，保留其他内容", "adopt_story_variant", ("n1",)
    )
    result = asyncio.run(selected_tool.execute_fn("adopt-call", {"source_variant_id": alternative}))
    main = store.require_variant("main")
    return {
        "tool_ok": bool(result.details),
        "selected": ["n1"],
        "unselected_n2": main.nodes["n2"].action,
        "truth": main.clues["glove"].author_truth,
    }


def quoted_truth_command():
    workspace, store = seed("quoted-command")
    selected_tool = tool(
        store, workspace, "请解释“把手套的真相改为客人所留”这句话的意思。", "update_story"
    )
    clue = store.require_variant("main").clues["glove"].model_dump(mode="json")
    result = asyncio.run(
        selected_tool.execute_fn(
            "quote-call",
            {
                "operations": [
                    {
                        "op": "update_clue",
                        "clue_id": "glove",
                        "clue": {**clue, "author_truth": "客人所留"},
                    },
                ]
            },
        )
    )
    return {
        "tool_ok": result.details["ok"],
        "truth": store.require_variant("main").clues["glove"].author_truth,
    }


def unrelated_adoption_edge():
    _, store = seed("unrelated-edge")
    alternative = fork(store)
    commit(
        store,
        alternative,
        "alt-change",
        [
            node_update(store, alternative, "n3", "只采用这处变化"),
            {
                "op": "connect",
                "edge": {"id": "unrelated", "from_id": "n1", "to_id": "n2", "kind": "next"},
            },
        ],
    )
    receipt = adopt(store, alternative, ("n3",))
    return {
        "selected": ["n3"],
        "unrelated_edge_copied": "unrelated" in store.require_variant("main").edges,
        "plan": receipt.result["adoption_plan"],
    }


def adoption_event_times():
    _, store = seed("event-times")
    alternative = fork(store)
    commit(
        store,
        alternative,
        "alt-change",
        [
            node_update(store, alternative, "n1", "更新入场"),
            {"op": "set_event_time", "relation": {"before_node_id": "n2", "after_node_id": "n1"}},
        ],
    )
    receipt = adopt(store, alternative)
    return {
        "receipt_kind": receipt.kind,
        "source_times": [
            row.model_dump() for row in store.require_variant(alternative).event_times
        ],
        "target_times": [row.model_dump() for row in store.require_variant("main").event_times],
    }


def fork_from_alternative_prose():
    workspace, store = seed("fork-prose")
    view = workspace.write("正文/0001 入场.md", "# 入场\n\n主方案原文。", expected_digest=None)
    alternative = fork(store)
    replacement = "# 入场\n\n备选甲的不同原文。"
    commit(
        store,
        alternative,
        "alt-prose",
        patches=[
            {
                "path": view.path,
                "base_digest": view.digest,
                "start": 0,
                "end": len(view.text),
                "expected": view.text,
                "replacement": replacement,
            }
        ],
    )
    second = fork(store, alternative, "fork-from-alternative")
    before_main_edit = store.read_prose(view.path, second)[1]
    workspace.write(view.path, "# 入场\n\n主方案后来修改。", expected_digest=view.digest)
    return {
        "source_text": store.read_prose(view.path, alternative)[1],
        "fork_text": before_main_edit,
        "fork_after_main_edit": store.read_prose(view.path, second)[1],
    }


def reader_revealed_truth():
    workspace, store = seed("reader-revealed-truth")
    truth = "凶手是一直守在门口的管家"
    view = workspace.write(
        "正文/0001 揭示.md", f"# 揭示\n\n侦探说：{truth}。", expected_digest=None
    )
    commit(
        store,
        "main",
        "reader-content",
        [
            {
                "op": "update_clue",
                "clue_id": "glove",
                "clue": {
                    "id": "glove",
                    "title": "手套",
                    "author_truth": truth,
                },
            },
            {
                "op": "update_node",
                "node_id": "n1",
                "node": {
                    "id": "n1",
                    "title": "入场",
                    "action": "侦探公开结论",
                    "clue_uses": [
                        {
                            "clue_id": "glove",
                            "presentation": f"侦探说：{truth}。",
                            "mode": "narration",
                        }
                    ],
                },
            },
            {
                "op": "bind_prose",
                "binding": {
                    "id": "revealed",
                    "node_id": "n1",
                    "document_id": "revealed-document",
                    "path": view.path,
                    "digest": view.digest,
                    "start": 0,
                    "end": len(view.text),
                    "quote": view.text,
                },
            },
        ],
    )
    variant = store.require_variant("main")
    assembled = assemble_reader_input(variant, "n1", prose={view.path: (view.digest, view.text)})
    actual = "\n".join(section.text for section in assembled.sections)
    try:
        assert_reader_isolated(actual, variant, "n1")
        return {"error": None, "prefix": actual}
    except WorkspaceError as error:
        return {"error": error.code, "prefix": actual}


if __name__ == "__main__":
    cases = {
        "F1_model_adoption_empty_scope": model_adoption_empty_scope,
        "F2_quoted_truth_command": quoted_truth_command,
        "F3_unrelated_adoption_edge": unrelated_adoption_edge,
        "F4_adoption_event_times": adoption_event_times,
        "F5_fork_from_alternative_prose": fork_from_alternative_prose,
        "F6_reader_revealed_truth": reader_revealed_truth,
    }
    results = {}
    for name, probe in cases.items():
        try:
            results[name] = probe()
        except Exception as error:
            results[name] = {"probe_error": f"{type(error).__name__}: {error}"}
    inspected = [
        "src/hastings/workspace/story_tools.py",
        "src/hastings/workspace/story_authorization.py",
        "src/hastings/workspace/story_adoption.py",
        "src/hastings/workspace/story_store.py",
        "src/hastings/workspace/story_context.py",
    ]
    output = {
        "run_dir": str(RUN.relative_to(ROOT)),
        "source_sha256": {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in inspected
        },
        "results": results,
    }
    Path(__file__).with_name("results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=True, indent=2))

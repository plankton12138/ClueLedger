"""Re-run the six audit probes against the fixed code; no model or network calls.

The probes themselves live with the audit record. This script only changes their
run directory and adds the F2 observation that the write tool is no longer
offered for a discussion turn.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
AUDIT = HERE.parent / "quality-audit-20261030" / "reproduce.py"
RUN = ROOT / "data/test-tmp/v101-quality-fixes-20261030" / f"probes-{uuid4().hex}"


def load_probes() -> Any:
    spec = importlib.util.spec_from_file_location("quality_audit_probes", AUDIT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.RUN = RUN
    return module


def quoted_command(probes: Any) -> dict[str, object]:
    """F2：解释引号里的命令时，本轮不提供写入工具，真相保持不变。"""
    workspace, store = probes.seed("quoted-command-fixed")
    text = "请解释“把手套的真相改为客人所留”这句话的意思。"
    scope = probes.StoryToolScope(
        state_root=store.root,
        workspace=workspace,
        frozen=probes.StoryContextSnapshot(
            variant_id="main",
            revision=store.require_variant("main").revision,
            purpose="design",
        ),
        writable=True,
        author_text=text,
        operation_id="audit-operation",
        generation=1,
    )
    names = [item.name for item in probes.build_story_tools(scope)]
    return {
        "write_tool_available": "update_story" in names,
        "tools": names,
        "truth": store.require_variant("main").clues["glove"].author_truth,
    }


def reader_revealed_truth(probes: Any) -> dict[str, object]:
    """F6：截止前经核验的正文公开了同一句真相，试读可以开始。"""
    workspace, store = probes.seed("reader-revealed-truth-fixed")
    truth = "凶手是一直守在门口的管家"
    view = workspace.write(
        "正文/0001 揭示.md", f"# 揭示\n\n侦探说：{truth}。", expected_digest=None
    )
    probes.commit(
        store,
        "main",
        "reader-content-fixed",
        [
            {
                "op": "update_clue",
                "clue_id": "glove",
                "clue": {"id": "glove", "title": "手套", "author_truth": truth},
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
    assembled = probes.assemble_reader_input(
        variant, "n1", prose={view.path: (view.digest, view.text)}
    )
    actual = "\n".join(section.text for section in assembled.sections)
    try:
        probes.assert_reader_isolated(
            actual, variant, "n1", published=assembled.published
        )
        return {
            "error": None,
            "prefix": actual,
            "published_chars": len(assembled.published),
        }
    except probes.WorkspaceError as error:
        return {"error": error.code, "prefix": actual}


def main() -> None:
    probes = load_probes()
    results: dict[str, object] = {}
    for name, probe in (
        ("F1_model_adoption_empty_scope", probes.model_adoption_empty_scope),
        ("F3_unrelated_adoption_edge", probes.unrelated_adoption_edge),
        ("F4_adoption_event_times", probes.adoption_event_times),
        ("F5_fork_from_alternative_prose", probes.fork_from_alternative_prose),
        ("F6_reader_revealed_truth", lambda: reader_revealed_truth(probes)),
    ):
        try:
            results[name] = probe()
        except Exception as error:  # noqa: BLE001 - 记录实际结果，不隐藏失败
            results[name] = {"probe_error": f"{type(error).__name__}: {error}"}
    try:
        results["F2_quoted_truth_command"] = quoted_command(probes)
    except Exception as error:  # noqa: BLE001
        results["F2_quoted_truth_command"] = {
            "probe_error": f"{type(error).__name__}: {error}"
        }

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
    (HERE / "results.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()

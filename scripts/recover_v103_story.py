"""把 1.0.3 之前的构思状态转换为当前格式。

只处理一个状态工作区，默认只预览；加 ``--apply`` 才写文件，并在写前把
``story/story.json``、``clue-ledger.json`` 与旧快照备份到工作区下的备份目录。

    uv run python scripts/recover_v103_story.py --workspace <state-workspace>

转换不会删除作者正文或旧文件。旧变更日志与无法对应到当前操作的记录会留在备份里，
当前状态只保留可读取的结构、阶段、线索计划与账本身份。
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from hastings.workspace.clue_models import ClueIdentity, ClueLedger
from hastings.workspace.story_models import StoryDocument

__all__ = ["convert", "main"]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _convert_ledger(raw: dict[str, Any], titles: dict[str, str]) -> ClueLedger:
    data = {
        key: value
        for key, value in raw.items()
        if key
        in {
            "schema_version",
            "recipe_version",
            "workspace_lineage",
            "ledger_revision",
            "clues",
            "contributions",
            "coverage",
            "requests",
        }
    }
    data["schema_version"] = 3
    ledger = ClueLedger.model_validate(data)
    for clue_id, title in titles.items():
        if clue_id in ledger.clues:
            continue
        ledger.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title=title,
            clue_revision=_new_id("rev"),
            created_at=datetime.now().astimezone().isoformat(timespec="microseconds"),
            created_by="migration_v103",
            first_chapter=None,
        )
    return ClueLedger.model_validate(ledger.model_dump(mode="json"))


def _uses_by_clue(nodes: dict[str, Any]) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    result: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for node_id, node in nodes.items():
        for use in node.get("clue_uses", []):
            result.setdefault(str(use.get("clue_id") or ""), []).append((node_id, use))
    return result


def _convert_variant(raw: dict[str, Any]) -> dict[str, Any]:
    data = dict(raw)
    clues = data.pop("clues", {}) or {}
    nodes: dict[str, Any] = data.get("nodes", {}) or {}
    uses = _uses_by_clue(nodes)
    plans: dict[str, Any] = {}
    titles: dict[str, str] = {}
    for clue_id, clue in clues.items():
        clue_id = str(clue_id)
        title = str(clue.get("title") or clue_id).strip() or clue_id
        titles[clue_id] = title
        resolution = clue.get("resolution") or {}
        intent = resolution.get("intent") or "undecided"
        resolve_nodes = [str(item) for item in resolution.get("node_ids", [])]
        has_resolve_use = any(
            node_id in resolve_nodes for node_id, _ in uses.get(clue_id, [])
        )
        if intent == "explain" and not has_resolve_use:
            # 旧主方案只有回收节点、没有任何线索使用；没有使用就没有可推导的回收位置。
            intent = "undecided"
        elif intent == "explain":
            for node_id in resolve_nodes:
                for use in nodes.get(node_id, {}).get("clue_uses", []):
                    if str(use.get("clue_id") or "") == clue_id:
                        roles = use.setdefault("roles", [])
                        if "resolve" not in roles:
                            roles.append("resolve")
        plans[clue_id] = {
            "clue_id": clue_id,
            "author_truth": clue.get("author_truth"),
            "intention": clue.get("intention") or "",
            "resolution_intent": intent,
        }
    for node in nodes.values():
        node.pop("group_id", None)
        node.setdefault("stage_id", None)
        node.setdefault("change", None)
        for use in node.get("clue_uses", []):
            use.setdefault("roles", [])
    stages = data.get("stages") or {}
    if not stages:
        stages = {
            "stage-legacy": {
                "id": "stage-legacy",
                "title": "原有结构",
                "order": 0,
                "summary": "由旧版结构恢复，阶段待作者划分",
            }
        }
        for node in nodes.values():
            if node.get("stage_id") is None:
                node["stage_id"] = "stage-legacy"
    threads: dict[str, Any] = data.get("threads", {}) or {}
    for index, thread in enumerate(threads.values()):
        thread.setdefault("order", index)
        thread.setdefault("goal", None)
        thread.setdefault("obstacle", None)
        thread.setdefault("intended_resolution", None)
    data["stages"] = stages
    data["clue_plans"] = plans
    data["nodes"] = nodes
    data["threads"] = threads
    return data


def convert(story: dict[str, Any], ledger: dict[str, Any]) -> tuple[dict[str, Any], ClueLedger, dict[str, str]]:
    variants = story.get("variants", {}) or {}
    titles: dict[str, str] = {}
    converted: dict[str, Any] = {}
    for variant_id, raw in variants.items():
        converted_variant = _convert_variant(raw)
        converted[str(variant_id)] = converted_variant
        for clue_id, plan in converted_variant["clue_plans"].items():
            titles.setdefault(str(clue_id), str(raw.get("clues", {}).get(clue_id, {}).get("title") or clue_id))
    story_doc = {
        "schema_version": 1,
        "workspace_lineage": story.get("workspace_lineage") or _new_id("ws"),
        "revision": story.get("revision") or _new_id("rev"),
        "primary_variant_id": story.get("primary_variant_id") or "main",
        "variants": converted,
        "changes": {},
        "requests": story.get("requests", {}) or {},
    }
    converted_ledger = _convert_ledger(ledger, titles)
    # 旧请求回执可能包含已退出功能的字段；校验失败就只保留结构。
    try:
        StoryDocument.model_validate(story_doc)
    except Exception:
        story_doc["requests"] = {}
        StoryDocument.model_validate(story_doc)
    return story_doc, converted_ledger, titles


def main() -> int:
    parser = argparse.ArgumentParser(description="转换 1.0.3 之前的构思状态")
    parser.add_argument("--workspace", type=Path, required=True, help="状态工作区目录，例如 ~/.hastings/workspaces/<id>")
    parser.add_argument("--apply", action="store_true", help="实际写入转换结果；缺省只预览")
    args = parser.parse_args()
    workspace = args.workspace.expanduser().resolve()
    story_path = workspace / "story" / "story.json"
    ledger_path = workspace / "clue-ledger.json"
    story = _read_json(story_path)
    ledger = _read_json(ledger_path)
    converted_story, converted_ledger, titles = convert(story, ledger)
    variants = converted_story["variants"]
    print(f"workspace: {workspace}")
    print(f"variants: {len(variants)}")
    for variant_id, variant in variants.items():
        print(
            f"  {variant_id}: stages={len(variant['stages'])} "
            f"nodes={len(variant['nodes'])} threads={len(variant['threads'])} "
            f"plans={len(variant['clue_plans'])} edges={len(variant['edges'])}"
        )
    print(f"ledger: identities={len(converted_ledger.clues)} contributions={len(converted_ledger.contributions)}")
    print(f"clue identities added for plans: {len(titles)}")
    if not args.apply:
        print("preview only; add --apply to back up and write")
        return 0
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = workspace / f"migration-v103-backup-{stamp}"
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(story_path, backup / "story.json")
    shutil.copy2(ledger_path, backup / "clue-ledger.json")
    (workspace / "story" / "story.json").write_text(
        json.dumps(converted_story, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    ledger_path.write_text(
        json.dumps(converted_ledger.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"backup: {backup}")
    print("converted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

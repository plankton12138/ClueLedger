"""独立作品上运行真实 TurnService；评测专用变体只在串行上下文内生效。"""

from __future__ import annotations

import asyncio
import difflib
import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from eval.harness.grading import grade_attempt
from eval.harness.schema import VARIANT_PARTS, Case, History, fixture_files
from hastings.library.bindings import AssemblyService
from hastings.library.modules import ModuleLibrary
from hastings.library.records import EntryDraft
from hastings.workspace import agent
from hastings.workspace.assembly import assemble
from hastings.workspace.chapter_index import ChapterIndexStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.git import GitRepo
from hastings.workspace.ideas import IdeaStore
from hastings.workspace.materials import ContextRequest
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.questions import QuestionStore
from hastings.workspace.service import ProviderFactory, TurnService
from hastings.workspace.state import ConversationStore, conversation_root
from hastings.workspace.story_materials import CoverageStore


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def snapshot_files(workspace: Workspace) -> dict[str, dict[str, str]]:
    catalog = workspace.catalog()
    files = {}
    for entry in (*catalog.chapters, *catalog.characters, *catalog.settings):
        if entry.exists:
            view = workspace.read(entry.path)
            files[entry.path] = {"text": view.text, "digest": view.digest}
    return files


def seed_files(workspace: Workspace, files: dict[str, str]) -> None:
    for path, text in files.items():
        try:
            digest = workspace.read(path).digest
        except WorkspaceError as exc:
            if exc.code != "FILE_NOT_FOUND":
                raise
            digest = None
        workspace.write(path, text, expected_digest=digest)


def seed_history(store: ConversationStore, case: Case) -> str | None:
    history = list(case.history)
    for i in range(case.filler_turns):
        history.append(
            History(
                user=f"第{i + 1}轮讨论：谈谈旅馆场景的节奏，不执行修改。"
                + "描述环境时避免重复用词。" * 20,
                assistant="可以交替使用声音、光线与动作组织场景，暂不修改任何作品文件。" * 10,
            )
        )
    cid = None
    for i, item in enumerate(history):
        tid = f"history-{i + 1}"
        if cid is None:
            accepted = store.create_with_first_turn(text=item.user, request_key=tid, turn_id=tid)
            cid = accepted.conversation_id
        else:
            store.append(cid, {"type": "turn_started", "turn_id": tid, "text": item.user})
        store.append(cid, {"type": "assistant_message", "turn_id": tid, "text": item.assistant})
        store.append(
            cid,
            {
                "type": "run_finished",
                "turn_id": tid,
                "status": item.status,
                "changed_paths": item.changed_paths,
                "recording": "no_change",
            },
        )
    return cid


@contextmanager
def intervention(case: Case, variant: str, trace: dict[str, Any]) -> Iterator[None]:
    original_assembly, original_tools = agent.build_turn_assembly, agent.build_tools
    removed = VARIANT_PARTS[variant]
    failed = False

    def build_assembly(*args: Any, **kwargs: Any) -> Any:
        result = original_assembly(*args, **kwargs)
        if removed:
            filtered = assemble(result.profile, [p for p in result.parts if p.key not in removed])
            result = replace(filtered, skipped=filtered.skipped + result.skipped)
        trace["assemblies"].append({p.key: p.text for p in result.parts})
        return result

    def build_tools(*args: Any, **kwargs: Any) -> Any:
        tools = original_tools(*args, **kwargs)
        if not case.fail_first_read:
            return tools
        result = []
        for tool in tools:
            if tool.name != "read_file":
                result.append(tool)
                continue
            execute = tool.execute_fn

            async def read(
                tool_call_id: str,
                arguments: Any,
                signal: Any = None,
                on_update: Any = None,
                *,
                executor: Any = execute,
            ) -> Any:
                nonlocal failed
                if not failed and arguments.get("path") == case.fail_first_read:
                    failed = True
                    trace["faults"].append({"tool": "read_file", "path": case.fail_first_read})
                    raise WorkspaceError(
                        code="EVAL_READ_TEMPORARY",
                        where=f"read_file:{case.fail_first_read}",
                        why="评测注入的一次临时读取失败",
                        fix="重新读取该文件后继续任务",
                    )
                return await executor(tool_call_id, arguments, signal, on_update)

            result.append(replace(tool, execute_fn=read))
        return result

    # 此进程只串行执行；退出时自动恢复，产品代码不识别评测变体。
    with (
        patch.object(agent, "build_turn_assembly", build_assembly),
        patch.object(agent, "build_tools", build_tools),
    ):
        yield


def _idea_rows(workspace: Workspace) -> list[dict[str, object]]:
    store = IdeaStore(workspace)
    rows: list[dict[str, object]] = []
    listed = store.list(limit=100).get("items")
    for item in listed if isinstance(listed, list) else []:
        view = store.get(str(item["idea_id"]))
        rows.append(
            {
                "idea_id": view["idea_id"],
                "revision_id": view["revision_id"],
                "title": view["title"],
                "body": view["body"],
                "deleted": view["deleted"],
            }
        )
    return rows


def _module_rows(library: ModuleLibrary | None) -> list[dict[str, object]]:
    """当前库里的模块与最新版本正文；只作评测证据，不进入模型上下文。"""
    if library is None:
        return []
    rows: list[dict[str, object]] = []
    listed = library.modules(include_deleted=True, limit=200).get("items")
    for item in listed if isinstance(listed, list) else []:
        info = library.module(str(item["module_id"]))
        if not info.head:
            continue
        revision = library.revision(module_id=info.module_id, revision_id=info.head)
        entries = _revision_entries(library, info.module_id, info.head, revision)
        # 作品内修改不推进库 head：把每个版本都留作证据，评测才能看到实际绑定内容。
        revisions = [
            {
                "revision_id": revision_id,
                "entries": _revision_entries(
                    library,
                    info.module_id,
                    revision_id,
                    library.revision(module_id=info.module_id, revision_id=revision_id),
                ),
            }
            for revision_id in library.storage.revision_ids(info.module_id)
        ]
        rows.append(
            {
                "module_id": info.module_id,
                "name": info.name,
                "kind": info.kind,
                "head": info.head,
                "deleted": info.deleted,
                "entries": entries,
                "revisions": revisions,
            }
        )
    return rows


def _revision_entries(
    library: ModuleLibrary,
    module_id: str,
    revision_id: str,
    revision: Any,
) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for entry in revision.entries:
        parts: list[str] = []
        start = 0
        while True:
            detail = library.read_entry(
                module_id=module_id,
                revision_id=revision_id,
                entry_id=entry.entry_id,
                start=start,
            )
            parts.append(str(detail["text"]))
            if not detail["truncated"]:
                break
            start = int(detail["next_cursor"] or detail["end"])
        entries.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "text": "".join(parts),
            }
        )
    return entries


def capture(
    workspace: Workspace,
    service: TurnService,
    events: list[dict[str, Any]],
    before: dict[str, Any],
    tid: str,
    trace: dict[str, Any],
    library: ModuleLibrary | None = None,
) -> dict[str, Any]:
    after = snapshot_files(workspace)
    details = {}
    for event in events:
        for name in ("input_detail", "result_detail"):
            key = event.get(name)
            if key and key not in details:
                details[key] = service.activity.detail_attachment(key)
    indexes = ChapterIndexStore(service.store.root)
    coverage = CoverageStore(service.store.root)
    derived = {}
    for name in (
        "story-index.json",
        "chapter-index.json",
        "disclosure.json",
        "character-states.json",
        "timeline.json",
        "glossary.json",
        "clues.json",
        "materials-coverage.json",
        "reviews.json",
    ):
        state_file = service.store.root / name
        if state_file.exists():
            derived[name] = json.loads(state_file.read_text(encoding="utf-8"))
    diffs = {}
    for path in before.keys() | after.keys():
        old, new = before.get(path, {}).get("text", ""), after.get(path, {}).get("text", "")
        if old != new:
            diffs[path] = "".join(
                difflib.unified_diff(
                    old.splitlines(keepends=True),
                    new.splitlines(keepends=True),
                    fromfile=f"before/{path}",
                    tofile=f"after/{path}",
                )
            )
    return {
        "turn_id": tid,
        "before": before,
        "after": after,
        "diffs": diffs,
        "events": events,
        "details": details,
        "derived": derived,
        "assembly": AssemblyStore(workspace.root).read().view(),
        "modules": _module_rows(library),
        "ideas": _idea_rows(workspace),
        "index_status": {
            entry.path: record.status
            if (record := indexes.find(workspace, entry.path))
            else "missing"
            for entry in workspace.catalog().chapters
        },
        "coverage": [
            {**record.to_dict(), "freshness": coverage.status(workspace, record)}
            for record in coverage.records()
        ],
        "assemblies": list(trace["assemblies"]),
        "faults": list(trace["faults"]),
    }


async def run_attempt(
    case: Case,
    variant: str,
    repeat: int,
    *,
    factory: ProviderFactory,
    work: Path,
    out: Path,
    max_turns: int,
    timeout: float,
) -> dict[str, Any]:
    key = f"{case.id}--{variant}--{repeat}"
    folder = out / "evidence" / key
    folder.mkdir(parents=True, exist_ok=False)
    write_json(folder / "case.json", case.model_dump(mode="json"))
    workspace = Workspace.create(work / key / "作品")
    seed_files(workspace, fixture_files(case))
    library: ModuleLibrary | None = None
    if case.modules or case.module_tools:
        library = ModuleLibrary(work / key / "库")
        service_assembly = AssemblyService(library, workspace)
        for index, seed in enumerate(case.modules):
            created = library.create_module(
                kind=seed.kind,
                name=seed.name,
                entries=[
                    EntryDraft(
                        entry_id=item.entry_id,
                        kind=item.kind,
                        title=item.title,
                        body=item.body,
                        fields=item.fields,
                    )
                    for item in seed.entries
                ],
                request_key=f"eval-module-{index + 1}",
            )
            module = created["module"]
            if not isinstance(module, dict):
                raise RuntimeError("模块创建结果缺少 module 字段")
            service_assembly.bind(
                module_id=str(module["module_id"]),
                revision_id=str(module["head"]),
                label=seed.name,
                expected_digest=AssemblyStore(workspace.root).digest(),
                request_key=f"eval-bind-{index + 1}",
            )
    GitRepo(workspace.root).init()
    ideas: dict[str, dict[str, object]] = {}
    idea_store = IdeaStore(workspace)
    for index, idea_seed in enumerate(case.ideas):
        created = idea_store.create(
            title=idea_seed.title,
            body=idea_seed.body,
            tags=idea_seed.tags,
            request_key=f"eval-idea-{index + 1}",
        )
        ideas[idea_seed.title] = created
    store = ConversationStore(conversation_root(work / key / "state", "eval"))
    cid = seed_history(store, case)
    service = TurnService(workspace, store, factory, max_turns=max_turns, library=library)
    service.activity.configure(capture_inputs=True, retention_days=0)
    evidences: list[dict[str, Any]] = []
    trace: dict[str, Any] = {"assemblies": [], "faults": []}
    if case.compact_before_steps and cid:
        # 压缩失败不中断评测：那本身就是「旧投影照常可用」的证据。
        try:
            record = await service.compact(cid, reason="eval")
            trace["compaction"] = {
                "compaction_id": record.compaction_id if record is not None else None,
                "estimated_before": record.estimated_before if record is not None else None,
                "estimated_after": record.estimated_after if record is not None else None,
            }
        except Exception as exc:  # noqa: BLE001 - 记录原因即可
            trace["compaction"] = {"error": str(exc)}
    error = None
    started = time.monotonic()
    with intervention(case, variant, trace):
        for i, step in enumerate(case.steps):
            if step.compact_before and cid:
                try:
                    await service.compact(cid, reason="eval")
                    trace.setdefault("compactions", []).append("ok")
                except Exception as exc:  # noqa: BLE001 - 失败本身是证据
                    trace.setdefault("compactions", []).append(str(exc))
            seed_files(workspace, step.external_updates)
            before = snapshot_files(workspace)
            trace["assemblies"].clear()
            trace["faults"].clear()
            info = None
            try:
                material_scope = step.material_scope
                reply_to = ""
                if step.reply_to_question:
                    pending = QuestionStore(store.root).pending(cid) if cid else ()
                    if not pending:
                        raise RuntimeError("这一步要回答问题，但会话里没有待答问题")
                    reply_to = pending[-1].question_id
                info = await service.accept(
                    step.text,
                    f"eval-{i + 1}",
                    conversation_id=cid,
                    context=ContextRequest(
                        selected_path=step.selected,
                        reference_paths=tuple(step.references),
                        idea_references=tuple(
                            (
                                str(ideas[title]["idea_id"]),
                                str(ideas[title]["revision_id"]),
                            )
                            for title in step.idea_references
                            if title in ideas
                        ),
                        state_chapter=step.state_chapter,
                    ),
                    purpose=step.purpose,
                    index_paths=step.index_paths,
                    material_scope=material_scope,
                    reply_to_question=reply_to,
                )
                cid = info.conversation_id
                try:
                    await asyncio.wait_for(asyncio.shield(service.wait(info.turn_id)), timeout)
                except TimeoutError:
                    await service.stop(info.turn_id)
                    await service.wait(info.turn_id)
                    error = "步骤超过评测耗时上限，已停止"
            except Exception as exc:
                # 不输出配置对象和 provider 异常原文，业务错误仍保存在本机证据中。
                error = f"评测执行异常：{type(exc).__name__}"
            events = (
                [
                    e
                    for e in store.events(cid)
                    if info is not None and e.get("turn_id") == info.turn_id
                ]
                if cid
                else []
            )
            evidence = capture(
                workspace,
                service,
                events,
                before,
                info.turn_id if info else "",
                trace,
                library=library,
            )
            evidences.append(evidence)
            write_json(folder / f"step-{i + 1}.json", evidence)
            terminals = [e for e in events if e["type"] == "run_finished"]
            if error or not terminals:
                break
            final = terminals[-1]["status"]
            # 提问暂停是预期终态：后面还有回答步骤时继续执行。
            if final != "completed" and not (step.expect_needs_input and final == "needs_input"):
                break
    result = grade_attempt(case, evidences, key)
    if error:
        result["error"] = error
        result["status"] = "error"
    result.update(
        {
            "key": key,
            "case": case.id,
            "title": case.title,
            "family": case.family,
            "layer": case.layer,
            "variant": variant,
            "repeat": repeat,
            "duration_seconds": round(time.monotonic() - started, 3),
            "evidence": str(folder.relative_to(out)).replace("\\", "/"),
            "model_error": any(
                (e["type"] == "model_request_finished" and e.get("status") == "failed")
                or (e["type"] == "error" and str(e.get("code", "")).startswith("MODEL_"))
                for evidence in evidences
                for e in evidence["events"]
            ),
        }
    )
    return result

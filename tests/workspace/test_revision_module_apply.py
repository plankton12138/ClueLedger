"""1.2 修订学习：模块条目建议的补丁、应用与版本切换。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from hastings.library import EntryDraft, ModuleLibrary
from hastings.library.bindings import AssemblyService
from hastings.server.provider_runtime import ModelSnapshot
from hastings.workspace.chapter_write_policy import ChapterWritePolicy
from hastings.workspace.chapter_writing import save_chapter_prose
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.revision_analysis import (
    RevisionAnalysis,
    RevisionAnalysisService,
    RevisionAnalysisStore,
)
from hastings.workspace.revision_apply import apply_module_decisions
from hastings.workspace.revision_store import RevisionStore
from hastings.workspace.story_store import EMPTY_REVISION, StoryStore
from hastings.workspace.writing_actions import create_chapter
from tau_agent.messages import AssistantMessage
from tau_agent.provider_events import AssistantDoneEvent, TextDeltaEvent


class _FakeProvider:
    def __init__(self, text: str) -> None:
        self.text = text

    async def stream_response(self, **_: Any) -> AsyncIterator[Any]:
        yield TextDeltaEvent(
            content_index=0, delta=self.text, partial=AssistantMessage(content="")
        )
        yield AssistantDoneEvent(reason="stop", message=AssistantMessage(content=self.text))

    async def aclose(self) -> None:
        return None


class _Holder:
    def __init__(self) -> None:
        self.text = ""

    def __call__(self) -> ModelSnapshot:
        return ModelSnapshot(
            connection_id="test",
            display_name="测试连接",
            model="fake",
            provider=_FakeProvider(self.text),
        )


@pytest.fixture()
def holder() -> _Holder:
    return _Holder()


def _setup(tmp_path: Path) -> dict[str, Any]:
    workspace = Workspace.create(tmp_path / "book")
    store = StoryStore(tmp_path / "state", workspace, format_version=5)
    workspace.chapter_policy = ChapterWritePolicy(store)
    created = create_chapter(
        store,
        workspace,
        title="第七章",
        after_chapter_id=None,
        expected_plan_revision=EMPTY_REVISION,
        request_key="chapter:module",
    )
    draft = "# 第七章\n\n林岚很紧张，她害怕许砚继续追问。\n"
    saved = save_chapter_prose(
        store,
        workspace,
        chapter_id=created.chapter_id,
        text=draft,
        expected_digest=created.prose_digest,
        request_key="save:draft",
    )
    edited_text = "# 第七章\n\n林岚把杯沿转了半圈。「你刚才说到哪儿了？」\n"
    edited = save_chapter_prose(
        store,
        workspace,
        chapter_id=created.chapter_id,
        text=edited_text,
        expected_digest=saved.prose_digest,
        request_key="save:edited",
    )
    assert edited.revision_id

    library = ModuleLibrary(
        tmp_path / "library", cache_file=tmp_path / "cache" / "catalog.json"
    )
    created_module = library.create_module(
        kind="characters",
        name="主角团",
        entries=[
            EntryDraft(
                "lin",
                "character",
                "林岚",
                "林岚遇到追问时会直接解释自己的紧张。",
            )
        ],
        request_key="module:characters",
    )
    module = created_module["module"]
    revision = created_module["revision"]
    assert isinstance(module, dict) and isinstance(revision, dict)
    module_id = str(module["module_id"])
    base_revision = str(revision["revision_id"])
    AssemblyService(library, workspace).bind(
        module_id=module_id,
        revision_id=base_revision,
        label="主角团",
        expected_digest=None,
        request_key="bind:characters",
    )
    raw, digest = library.storage.read_entry(module_id, base_revision, "lin")
    assert "直接解释自己的紧张" in raw
    return {
        "workspace": workspace,
        "store": store,
        "library": library,
        "module_id": module_id,
        "base_revision": base_revision,
        "entry_digest": digest,
        "revision_id": edited.revision_id,
        "text": edited_text,
    }


def _payload(hunk_id: str, setup: dict[str, Any]) -> str:
    return json.dumps(
        {
            "summary": "林岚面对追问的应对方式变了。",
            "decisions": [
                {
                    "hunk_ids": [hunk_id],
                    "target": "module_entry",
                    "reason": "作者把直接解释紧张改成转移话题，人物条目需要同步。",
                    "evidence": "林岚遇到追问时会直接解释自己的紧张。",
                    "proposal": "从第七章起，林岚遇到追问时先把话题岔开。",
                    "target_label": "林岚",
                    "module_id": setup["module_id"],
                    "revision_id": setup["base_revision"],
                    "entry_id": "lin",
                    "entry_digest": setup["entry_digest"],
                    "patch_find": "直接解释自己的紧张",
                    "patch_replace": "先把话题岔开，再决定说到哪儿",
                }
            ],
        },
        ensure_ascii=False,
    )


def _ready_analysis(setup: dict[str, Any], holder: _Holder) -> RevisionAnalysis:
    workspace = setup["workspace"]
    store = setup["store"]
    detail = RevisionStore(store.root).detail(setup["revision_id"], setup["text"])
    assert detail is not None
    hunk_id = detail.hunks[0].id
    holder.text = _payload(hunk_id, setup)
    service = RevisionAnalysisService(
        state_root=store.root,
        workspace=workspace,
        provider_factory=holder,
        library=setup["library"],
    )

    async def run() -> RevisionAnalysis:
        analysis = await service.start(
            revision_id=setup["revision_id"],
            conversation_id="conv_1",
            operation_id="op_1",
            hunk_ids=[hunk_id],
            request_key="analysis:module",
        )
        for _ in range(200):
            current = service.read(analysis.id)
            if current.status in ("ready", "failed"):
                return current
            await asyncio.sleep(0.02)
        raise AssertionError("分析没有结束")

    return asyncio.run(run())


def test_module_decision_applies_as_workspace_revision(
    tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup(tmp_path)
    ready = _ready_analysis(setup, holder)

    assert ready.status == "ready", ready.error
    module_read = next(row for row in ready.trace["reads"] if row["label"] == "已装模块")
    assert "主角团 / 林岚" in module_read["detail"]
    decision = ready.decisions[0]
    assert decision.target == "module_entry"
    assert decision.patch_error == ""
    assert decision.entry_digest == setup["entry_digest"]

    result = apply_module_decisions(
        RevisionAnalysisStore(setup["store"].root),
        setup["workspace"],
        setup["library"],
        analysis_id=ready.id,
        decision_indexes=[0],
        expected_digest=ready.assembly_digest,
    )

    assert result.analysis.decisions[0].applied is True
    binding = AssemblyStore(setup["workspace"].root).read().bindings[0]
    assert binding.module.revision_id != setup["base_revision"]
    assert setup["library"].module(setup["module_id"]).head == setup["base_revision"]
    raw, _ = setup["library"].storage.read_entry(
        setup["module_id"], binding.module.revision_id, "lin"
    )
    assert "先把话题岔开" in raw
    original, _ = setup["library"].storage.read_entry(
        setup["module_id"], setup["base_revision"], "lin"
    )
    assert "直接解释自己的紧张" in original


def test_module_apply_rejects_entry_changed_after_analysis(
    tmp_path: Path, holder: _Holder
) -> None:
    setup = _setup(tmp_path)
    ready = _ready_analysis(setup, holder)
    store = RevisionAnalysisStore(setup["store"].root)
    broken = replace(
        ready,
        decisions=[replace(ready.decisions[0], entry_digest="stale")],
    )
    store.save(broken)

    with pytest.raises(Exception) as caught:
        apply_module_decisions(
            store,
            setup["workspace"],
            setup["library"],
            analysis_id=broken.id,
            decision_indexes=[0],
            expected_digest=broken.assembly_digest,
        )

    assert getattr(caught.value, "code", "") == "REVISION_APPLICATION_CONFLICT"
    binding = AssemblyStore(setup["workspace"].root).read().bindings[0]
    assert binding.module.revision_id == setup["base_revision"]

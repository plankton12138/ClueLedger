"""1.0.1 P1：正文关联与核对的失效判定，不读模型。

用例直接用共享样例里的 emoji／CRLF 正文，确保位置规则与前端对齐：码点而不是 UTF-16。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hastings.workspace.story_models import (
    Assessment,
    AssessmentRequirement,
    ProseBinding,
    StoryVariant,
    validate_story_variant,
)
from hastings.workspace.story_projection import (
    assessment_state,
    binding_state,
    normalize_prose,
    variant_summary,
)

FIXTURES = Path(__file__).parents[1] / "fixtures" / "story"


def load(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def emoji_case() -> tuple[ProseBinding, bytes]:
    payload = load("emoji-positions.json")
    return ProseBinding.model_validate(payload["binding"]), payload["content"].encode("utf-8")


def test_binding_is_valid_when_digest_and_quote_match() -> None:
    binding, raw = emoji_case()
    state = binding_state(binding, raw)
    assert state.status == "valid"
    assert normalize_prose(raw)[binding.start : binding.end] == binding.quote


def test_binding_goes_stale_when_prose_changes() -> None:
    binding, raw = emoji_case()
    edited = raw.replace("窗台".encode(), "窗沿".encode())
    state = binding_state(binding, edited)
    assert state.status == "stale"
    assert "被修改" in state.reason


def test_binding_is_missing_when_file_is_gone() -> None:
    binding, _ = emoji_case()
    state = binding_state(binding, None)
    assert state.status == "missing"
    assert "不存在" in state.reason


def test_binding_goes_stale_when_range_exceeds_the_text() -> None:
    binding, raw = emoji_case()
    shorter = ProseBinding.model_validate(
        {**binding.model_dump(mode="json"), "start": 0, "end": 2, "quote": "他把"}
    )
    assert binding_state(shorter, raw).status == "valid"
    beyond = ProseBinding.model_validate(
        {**binding.model_dump(mode="json"), "start": 500, "end": 503, "quote": "钥匙🔑"}
    )
    state = binding_state(beyond, raw)
    assert state.status == "stale"
    assert "超出" in state.reason


def test_assessment_is_valid_only_for_the_checked_structure() -> None:
    variant = StoryVariant.model_validate(load("blank-mystery.json"))
    validate_story_variant(variant)
    content = "# 第一章\n\n她按下录音笔。\n".encode()
    digest = hashlib.sha256(content).hexdigest()
    assessment = Assessment(
        id="a1",
        requirements=[AssessmentRequirement(requirement_id="r1", text="必须回应钥匙")],
        structure_revision=variant.revision,
        prose_digests={"正文/0001.md": digest},
    )
    files = {"正文/0001.md": content}
    assert assessment_state(assessment, variant, files.get).status == "valid"

    edited = Assessment.model_validate(
        {**assessment.model_dump(mode="json"), "structure_revision": "rev_other"}
    )
    state = assessment_state(edited, variant, files.get)
    assert state.status == "stale"
    assert "结构" in state.reason


def test_assessment_goes_stale_when_prose_changes() -> None:
    variant = StoryVariant.model_validate(load("blank-mystery.json"))
    content = "# 第一章\n\nold\n".encode()
    assessment = Assessment(
        id="a1",
        structure_revision=variant.revision,
        prose_digests={"正文/0001.md": hashlib.sha256(content).hexdigest()},
    )
    state = assessment_state(assessment, variant, {"正文/0001.md": b"# new\n"}.get)
    assert state.status == "stale"
    assert "正文" in state.reason


def test_summary_reports_unresolved_clues_and_stale_targets() -> None:
    variant = StoryVariant.model_validate(load("blank-mystery.json"))
    summary = variant_summary(variant, lambda _path: None)
    assert summary["nodes"] == 3
    assert summary["unresolved_clue_ids"] == ["c1", "c2"]
    assert summary["stale_binding_ids"] == []
    assert summary["stale_assessment_ids"] == []


def test_summary_lists_stale_bindings() -> None:
    payload = load("blank-mystery.json")
    binding = load("emoji-positions.json")["binding"]
    payload["bindings"] = {"b1": binding}
    variant = StoryVariant.model_validate(payload)
    summary = variant_summary(variant, lambda _path: None)
    assert summary["stale_binding_ids"] == ["b1"]

import pytest

from hastings.agents.schemas import parse_output


@pytest.mark.parametrize(
    "role,kind",
    [
        ("planner", "story_proposal"),
        ("writer", "manuscript_patch"),
        ("reviser", "manuscript_patch"),
        ("auditor", "finding_set"),
        ("style_observer", "observation_set"),
        ("coordinator", "story_proposal"),
    ],
)
def test_worker_empty_and_failure_contract(role, kind):
    assert parse_output(role, {"kind": "no_result", "reason": "没有相关内容"}).kind == "no_result"
    assert (
        parse_output(
            role, {"kind": "worker_failure", "category": "environment", "reason": "服务中断"}
        ).kind
        == "worker_failure"
    )


def test_auditor_cannot_return_manuscript_patch():
    with pytest.raises(ValueError):
        parse_output(
            "auditor",
            {
                "kind": "manuscript_patch",
                "base_commit_id": "base",
                "chapter_id": "chapter",
                "text": "越权正文",
            },
        )


def test_finding_needs_locatable_evidence():
    result = parse_output(
        "auditor",
        {
            "kind": "finding_set",
            "base_commit_id": "base",
            "findings": [
                {"artifact_ids": ["unit"], "quote": "他已经知道真相", "reason": "早于披露"}
            ],
            "editorial_notes": ["节奏还可以更快"],
        },
    )
    assert result.findings[0].artifact_ids == ["unit"]
    with pytest.raises(ValueError):
        parse_output(
            "auditor",
            {
                "kind": "finding_set",
                "base_commit_id": "base",
                "findings": [{"artifact_ids": [], "quote": "", "reason": "不够好"}],
            },
        )


def test_observer_cannot_activate_preferences():
    with pytest.raises(ValueError):
        parse_output(
            "style_observer",
            {
                "kind": "observation_set",
                "base_commit_id": "base",
                "observations": [
                    {
                        "unit_id": "u",
                        "category": "style",
                        "statement": "喜欢短句",
                        "confidence": 1,
                        "status": "active",
                    }
                ],
            },
        )

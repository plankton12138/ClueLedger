"""内容补丁不得偷偷改变身份或依赖关系；结构改动应走相应领域命令。"""

from typing import Any

from hastings.domain.changes.errors import violation
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.manuscript.models import Chapter, TextUnit
from hastings.domain.story.catalog import decode
from hastings.domain.story.errors import StoryViolation
from hastings.domain.story.mystery.models import KNOWLEDGE_MODES

# 这些字段会影响版本化依赖边或对象归属，通用内容 Patch 无权同步它们。
REFERENCE_FIELDS = frozenset(
    {
        "unit_id",
        "chapter_id",
        "beat_id",
        "node_id",
        "parent_id",
        "scene_id",
        "text_unit_ids",
        "narrative_chapter_id",
        "source_id",
        "target_id",
        "character_id",
        "fact_id",
        "at_beat_id",
        "participants",
        "preconditions",
        "depends_on",
        "constraints",
        "state_changes",
        "reader_disclosures",
        "setups",
        "payoffs",
        "points_to",
        "disclosure_plan",
        "derived_from",
        "state",
        "origin",
        "event_id",
        "place_id",
        "relation_type",
        "schema_version",
    }
)


def validate_content_patch(kind: str, old: dict[str, Any], new: dict[str, Any]) -> None:
    changed = {k for k in old.keys() | new.keys() if old.get(k) != new.get(k)}
    if changed & REFERENCE_FIELDS:
        raise violation(
            "CHANGES_PROPOSAL_OUT_OF_SCOPE",
            "changes.patch.content",
            f"内容补丁修改了身份、归属或依赖字段：{sorted(changed & REFERENCE_FIELDS)}",
            "本阶段仅修订对象内容；结构与依赖变更使用 Story/Manuscript 领域命令",
        )
    try:
        _validate_schema(kind, new)
    except (StoryViolation, ManuscriptViolation) as exc:
        raise violation("CHANGES_INVALID_COMMAND", exc.where, exc.why, exc.fix) from exc


def _validate_schema(kind: str, new: dict[str, Any]) -> None:
    if kind == "manuscript.text_unit":
        if not isinstance(new.get("text"), str):
            raise violation(
                "CHANGES_INVALID_COMMAND",
                "changes.patch.text",
                "正文不是字符串",
                "提交完整 TextUnit 内容，text 使用字符串",
            )
        TextUnit.from_content(new)
    elif kind == "manuscript.chapter":
        Chapter.from_content(new)
    elif kind.startswith(("story.", "narrative.")):
        decode(kind, new)
        if kind == "story.knowledge" and new.get("mode") not in KNOWLEDGE_MODES:
            raise violation(
                "CHANGES_INVALID_COMMAND",
                "changes.patch.mode",
                "未知认知状态",
                f"mode 使用 {sorted(KNOWLEDGE_MODES)} 之一",
            )

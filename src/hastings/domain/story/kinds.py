"""Story Domain 的 Artifact kind 与依赖边类型常量（modules/story-domain.md §2/§4）。

Canon 事实与 Narrative 结构统一存放在 Repository 的 Artifact 中，以
`story.` / `narrative.` 前缀区分。StoryCatalog 据此把 Snapshot 过滤为故事目录。
"""

from __future__ import annotations

CANON_PREFIX = "story."
NARRATIVE_PREFIX = "narrative."

# --- Canon / 三层事实 -------------------------------------------------------
KIND_STORY_PREMISE = "story.premise"
KIND_CHARACTER = "story.character"
KIND_RELATIONSHIP = "story.relationship"
KIND_PLACE = "story.place"
KIND_WORLD_RULE = "story.world_rule"
KIND_MYSTERY_TRUTH = "story.mystery_truth"
KIND_CLUE = "story.clue"
KIND_RED_HERRING = "story.red_herring"
KIND_FORESHADOW = "story.foreshadow"
KIND_TIMELINE_EVENT = "story.timeline_event"
KIND_TRUTH_FACT = "story.truth_fact"
KIND_KNOWLEDGE = "story.knowledge"
KIND_DISCLOSURE = "story.disclosure"

# --- Narrative Plan：Series → Book → Arc → Chapter → Scene → Beat -----------
KIND_SERIES = "narrative.series"
KIND_BOOK = "narrative.book"
KIND_ARC = "narrative.arc"
KIND_CHAPTER = "narrative.chapter"
KIND_SCENE = "narrative.scene"
KIND_BEAT = "narrative.beat"

NARRATIVE_NODE_KINDS = (KIND_SERIES, KIND_BOOK, KIND_ARC, KIND_CHAPTER, KIND_SCENE)

CANON_KINDS = (
    KIND_STORY_PREMISE,
    KIND_CHARACTER,
    KIND_RELATIONSHIP,
    KIND_PLACE,
    KIND_WORLD_RULE,
    KIND_MYSTERY_TRUTH,
    KIND_CLUE,
    KIND_RED_HERRING,
    KIND_FORESHADOW,
    KIND_TIMELINE_EVENT,
    KIND_TRUTH_FACT,
    KIND_KNOWLEDGE,
    KIND_DISCLOSURE,
)

KNOWN_STORY_KINDS = frozenset((*CANON_KINDS, *NARRATIVE_NODE_KINDS, KIND_BEAT))


def is_story_kind(kind: str) -> bool:
    """判断某 Repository Artifact kind 是否由 Story Domain 持有。"""
    return kind.startswith(CANON_PREFIX) or kind.startswith(NARRATIVE_PREFIX)


# --- 依赖边类型（modules/story-domain.md §4） -------------------------------
REL_CONSTRAINS = "constrains"
REL_MOTIVATES = "motivates"
REL_REVEALS = "reveals"
REL_CHANGES_STATE = "changes_state"
REL_SETS_UP = "sets_up"
REL_PAYS_OFF = "pays_off"
REL_REQUIRES = "requires"
REL_RENDERS = "renders"
REL_MENTIONS = "mentions"
# 替代关系：新事实 → 被替代的旧事实；旧事实保留，查询据此排除已替代项（不变量 3）
REL_SUPERSEDES = "supersedes"

STORY_RELATION_TYPES = frozenset(
    (
        REL_CONSTRAINS,
        REL_MOTIVATES,
        REL_REVEALS,
        REL_CHANGES_STATE,
        REL_SETS_UP,
        REL_PAYS_OFF,
        REL_REQUIRES,
        REL_RENDERS,
        REL_MENTIONS,
        REL_SUPERSEDES,
    )
)

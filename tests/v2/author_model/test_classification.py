"""T14.5 规则优先分类管线：五类编辑的判定、置信度阈值与冲突处理。

覆盖计划 §6 测试先行清单 10（格式/事实变化不进入文风）：
- 格式/错字（标点空白归一后内容一致；极少量字符不同）→ confirmed；
- 事实修订（数字/专名令牌变化）→ confirmed，且不被「文风」兜底抢走；
- 结构修订（段落增删拆分合并移动）→ confirmed；
- 语言风格（无结构/事实信号的词句改写）→ 低于阈值，保持 candidate；
- 协作反馈（作者显式评注）→ confirmed；
- 同置信度不同类别 → 冲突：类别留空 + 并列候选，保持 candidate。
"""

from __future__ import annotations

import pytest

from hastings.domain.author_model import rules
from hastings.domain.author_model.models import (
    CATEGORY_COLLABORATION,
    CATEGORY_FACT,
    CATEGORY_FORMAT,
    CATEGORY_STRUCTURE,
    CATEGORY_STYLE,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    SOURCE_RULE,
    Classification,
    EditSample,
)
from hastings.domain.manuscript import operations as ops

CHAPTER = "art_ch1"


def _sample(
    before: str,
    after: str,
    *,
    operation: str = ops.OP_REPLACE,
    note: str = "",
    unit_id: str = "art_u1",
) -> EditSample:
    return EditSample(
        unit_id=unit_id,
        chapter_id=CHAPTER,
        before=before,
        after=after,
        operation=operation,
        note=note,
    )


# ---------------------------------------------------------------------------
# 格式/错字
# ---------------------------------------------------------------------------
def test_punctuation_only_change_is_confirmed_format() -> None:
    result = rules.classify_edit(_sample("他说：“好。”", '他说:"好."'))

    assert result.category == CATEGORY_FORMAT
    assert result.status == OBSERVATION_CONFIRMED
    assert result.confidence >= rules.DEFAULT_CONFIDENCE_THRESHOLD
    assert result.signals  # 可解释：说明为什么这样判


def test_whitespace_only_change_is_confirmed_format() -> None:
    result = rules.classify_edit(_sample("他 说 好 了", "他说好了"))

    assert result.category == CATEGORY_FORMAT
    assert result.status == OBSERVATION_CONFIRMED


# ---------------------------------------------------------------------------
# 事实修订：不进入文风
# ---------------------------------------------------------------------------
def test_fact_token_change_is_confirmed_fact_not_style() -> None:
    """事实令牌（数字）变化是事实修订，压过文风兜底规则。"""
    result = rules.classify_edit(_sample("案发时间是 8 点整", "案发时间是 11 点整"))

    assert result.category == CATEGORY_FACT
    assert result.status == OBSERVATION_CONFIRMED


def test_proper_name_change_is_fact() -> None:
    result = rules.classify_edit(_sample("线索写在《朝日新闻》上", "线索写在《每日新闻》上"))

    assert result.category == CATEGORY_FACT
    assert result.status == OBSERVATION_CONFIRMED


# ---------------------------------------------------------------------------
# 结构修订
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "operation",
    [ops.OP_INSERT, ops.OP_DELETE, ops.OP_SPLIT, ops.OP_MERGE, ops.OP_MOVE],
)
def test_structural_operations_are_structure(operation: str) -> None:
    result = rules.classify_edit(_sample("旧文本", "新文本", operation=operation))

    assert result.category == CATEGORY_STRUCTURE
    assert result.status == OBSERVATION_CONFIRMED


def test_whole_paragraph_addition_is_structure() -> None:
    """replace 但旧文本为空 = 整段新增，按结构处理。"""
    result = rules.classify_edit(_sample("", "新增的一整段"))

    assert result.category == CATEGORY_STRUCTURE
    assert result.status == OBSERVATION_CONFIRMED


# ---------------------------------------------------------------------------
# 语言风格：低置信度保持 candidate
# ---------------------------------------------------------------------------
def test_style_rewrite_stays_candidate() -> None:
    result = rules.classify_edit(_sample("他缓缓地走进了房间。", "他慢慢地走进房间。"))

    assert result.category == CATEGORY_STYLE
    assert result.status == OBSERVATION_CANDIDATE  # 证据不足，不强行断言
    assert result.confidence < rules.DEFAULT_CONFIDENCE_THRESHOLD


def test_threshold_is_injectable_policy() -> None:
    """阈值是注入策略，不是散落的魔法数字：放宽后同一编辑可断言。"""
    sample = _sample("他缓缓地走进了房间。", "他慢慢地走进房间。")

    assert rules.classify_edit(sample).status == OBSERVATION_CANDIDATE
    assert rules.classify_edit(sample, threshold=0.5).status == OBSERVATION_CONFIRMED


# ---------------------------------------------------------------------------
# 协作反馈与冲突
# ---------------------------------------------------------------------------
def test_author_note_is_collaboration_feedback() -> None:
    result = rules.classify_edit(_sample("他缓缓地走。", "他走进。", note="这句太啰嗦，删掉修饰"))

    assert result.category == CATEGORY_COLLABORATION
    assert result.status == OBSERVATION_CONFIRMED


def test_equal_confidence_conflict_stays_candidate_with_alternatives() -> None:
    """同等证据支持不同类别（评注 + 结构操作）→ 不强行断言，留下并列候选。"""
    result = rules.classify_edit(_sample("甲", "乙", operation=ops.OP_MERGE, note="这两段该合并"))

    assert result.category is None
    assert result.status == OBSERVATION_CANDIDATE
    assert set(result.alternatives) == {CATEGORY_COLLABORATION, CATEGORY_STRUCTURE}
    assert result.signals


def test_no_rule_match_is_candidate_without_category() -> None:
    result = rules.classify_edit(_sample("甲", "乙"), rules=())

    assert result.category is None
    assert result.confidence == 0.0
    assert result.status == OBSERVATION_CANDIDATE


def test_classify_edits_is_positional_batch() -> None:
    results = rules.classify_edits(
        [
            _sample("他说：“好。”", '他说:"好."'),
            _sample("案发时间是 8 点整", "案发时间是 11 点整"),
        ]
    )

    assert [r.category for r in results] == [CATEGORY_FORMAT, CATEGORY_FACT]


# ---------------------------------------------------------------------------
# 值对象不变量
# ---------------------------------------------------------------------------
def test_classification_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        Classification(category="nonsense", confidence=0.9, status=OBSERVATION_CONFIRMED)
    with pytest.raises(ValueError):
        Classification(category=CATEGORY_STYLE, confidence=0.9, status="halfling")
    with pytest.raises(ValueError):
        Classification(category=CATEGORY_STYLE, confidence=1.5, status=OBSERVATION_CONFIRMED)
    with pytest.raises(ValueError):
        Classification(
            category=CATEGORY_STYLE,
            confidence=0.9,
            status=OBSERVATION_CONFIRMED,
            source="oracle",
        )


def test_default_source_is_rule() -> None:
    assert rules.classify_edit(_sample("甲", "乙")).source == SOURCE_RULE

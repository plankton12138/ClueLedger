"""规则优先的编辑分类管线（Phase 14 T14.5）。

规则是纯函数 ``(EditSample) -> RuleMatch | None``：只看编辑的前后文本、操作
种类与作者评注，无 IO、无模型调用，因此结果确定、可复现、可解释（每条匹配都带
一句「为什么这样判」的信号）。

管线取最高置信度的匹配：

- 唯一类别且达阈值 → ``confirmed``；
- 低于阈值 → ``candidate``（证据不足，等后续证据或人工确认）；
- **同等置信度出现不同类别** → 冲突，不强行断言：类别留空，并列候选记入
  ``alternatives``，交模型补充或人工裁定（四透镜③：不确定是合法状态，不是故障）。

规则优先（rule-first）：模型分类器只是补充，见
``application/workflows/author_learning.py``。

T14.6 在同一模块放**偏好归纳策略**：证据阈值与作用域解析都是纯函数与可注入策略
（``InductionPolicy``），阈值不散落成魔法数字——策略变了不必改命令代码。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from difflib import SequenceMatcher

from hastings.domain.author_model.models import (
    CATEGORY_COLLABORATION,
    CATEGORY_FACT,
    CATEGORY_FORMAT,
    CATEGORY_STRUCTURE,
    CATEGORY_STYLE,
    OBSERVATION_CANDIDATE,
    OBSERVATION_CONFIRMED,
    ORIGIN_EXPLICIT,
    PREFERENCE_ACTIVE,
    PREFERENCE_CANDIDATE,
    SCOPE_ORDER,
    AuthorExample,
    AuthorObservation,
    Classification,
    EditSample,
    PreferenceHypothesis,
)
from hastings.domain.manuscript import operations as ops

__all__ = [
    "CATEGORY_STATEMENTS",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_INDUCTION_POLICY",
    "DEFAULT_RULES",
    "DIRECTION_CONCISE",
    "DIRECTION_EXPAND",
    "InductionPolicy",
    "RuleMatch",
    "activation_status",
    "classify_edit",
    "classify_edits",
    "conflicts_with_explicit",
    "effective_examples",
    "evidence_commits",
    "evidence_observations",
    "preference_applies",
    "resolve_preferences",
    "statement_for",
    "style_direction",
    "style_evidence_conflicts",
]

# 达此置信度才算「断言」；低于阈值的匹配保持 candidate
DEFAULT_CONFIDENCE_THRESHOLD = 0.6

_RULE_COLLABORATION = 0.9
_RULE_STRUCTURE = 0.9
_RULE_STRUCTURE_EMPTY = 0.85
_RULE_FORMAT_IDENTICAL = 0.95
_RULE_FORMAT_NEAR = 0.8
_RULE_FACT = 0.75
_RULE_STYLE = 0.55

_STRUCTURAL_OPS = frozenset((ops.OP_INSERT, ops.OP_DELETE, ops.OP_SPLIT, ops.OP_MERGE, ops.OP_MOVE))

# 标点与空白：这些字符的变化不影响正文语义，属格式/错字范畴
_FORMAT_CHARS = frozenset(
    "，。、！？；：（）《》〈〉「」『』—…·～,.!?;:()[]{}<>-\u201c\u201d\u2018\u2019\"'"
)

# 事实令牌：数字（含日期/小数）、书名号与引号内的专名
_FACT_RE = re.compile(
    r"\d+(?:[.:：/\-]\d+)*|《[^》]*》|「[^」]*」|\"[^\"]*\"|\u201c[^\u201d]*\u201d"
)


@dataclass(frozen=True, slots=True)
class RuleMatch:
    """单条规则的命中：类别 + 置信度 + 一句「为什么这样判」的信号。"""

    category: str
    confidence: float
    signal: str


def _without_formatting(text: str) -> str:
    """去掉全部空白与常见标点；两侧结果相等即「只有格式变了」。"""
    return "".join(ch for ch in text if not ch.isspace() and ch not in _FORMAT_CHARS)


def _fact_tokens(text: str) -> tuple[str, ...]:
    return tuple(sorted(_FACT_RE.findall(text)))


def _similarity(before: str, after: str) -> float:
    return SequenceMatcher(a=before, b=after, autojunk=False).ratio()


def _collaboration_rule(sample: EditSample) -> RuleMatch | None:
    """协作反馈：作者保存时附了显式评注（对 Agent 行为的元反馈）。"""
    if sample.note.strip():
        return RuleMatch(CATEGORY_COLLABORATION, _RULE_COLLABORATION, "作者保存时附了显式评注")
    return None


def _structure_rule(sample: EditSample) -> RuleMatch | None:
    """结构修订：段落级的增删拆分合并移动。"""
    if sample.operation in _STRUCTURAL_OPS:
        return RuleMatch(
            CATEGORY_STRUCTURE, _RULE_STRUCTURE, f"操作 {sample.operation} 改变段落结构"
        )
    if sample.operation == ops.OP_REPLACE and (not sample.before or not sample.after):
        return RuleMatch(CATEGORY_STRUCTURE, _RULE_STRUCTURE_EMPTY, "整段新增或清空（段落级变化）")
    return None


def _format_rule(sample: EditSample) -> RuleMatch | None:
    """格式/错字：标点空白归一后内容一致，或仅极少数字符不同。"""
    if not sample.before or not sample.after or sample.before == sample.after:
        return None
    if _without_formatting(sample.before) == _without_formatting(sample.after):
        return RuleMatch(CATEGORY_FORMAT, _RULE_FORMAT_IDENTICAL, "仅标点/空白不同，正文内容一致")
    ratio = _similarity(sample.before, sample.after)
    if ratio >= 0.98 and abs(len(sample.before) - len(sample.after)) <= 2:
        return RuleMatch(
            CATEGORY_FORMAT, _RULE_FORMAT_NEAR, f"仅极少量字符不同（相似度 {ratio:.2f}）"
        )
    return None


def _fact_rule(sample: EditSample) -> RuleMatch | None:
    """事实修订：数字/日期/专名等事实令牌发生变化。"""
    if not sample.before or not sample.after:
        return None
    if _fact_tokens(sample.before) != _fact_tokens(sample.after):
        return RuleMatch(CATEGORY_FACT, _RULE_FACT, "数字/日期/专名等事实令牌发生变化")
    return None


def _style_rule(sample: EditSample) -> RuleMatch | None:
    """语言风格：有词句变化但无结构/事实信号（兜底，置信度低于阈值 → candidate）。"""
    if not sample.before or not sample.after:
        return None
    ratio = _similarity(sample.before, sample.after)
    return RuleMatch(CATEGORY_STYLE, _RULE_STYLE, f"词句改写（相似度 {ratio:.2f}）")


# 规则顺序决定并列时的信号顺序；冲突判定按类别集合，与顺序无关
DEFAULT_RULES: tuple[Callable[[EditSample], RuleMatch | None], ...] = (
    _collaboration_rule,
    _structure_rule,
    _format_rule,
    _fact_rule,
    _style_rule,
)


def classify_edit(
    sample: EditSample,
    *,
    rules: Iterable[Callable[[EditSample], RuleMatch | None]] = DEFAULT_RULES,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> Classification:
    """对一次编辑做规则分类；阈值是注入策略，不散落魔法数字。"""
    matches = [match for rule in rules if (match := rule(sample)) is not None]
    if not matches:
        return Classification(category=None, confidence=0.0, status=OBSERVATION_CANDIDATE)
    top = max(match.confidence for match in matches)
    top_matches = [match for match in matches if match.confidence == top]
    categories = sorted({match.category for match in top_matches})
    signals = tuple(match.signal for match in top_matches)
    if len(categories) > 1:
        # 同等证据支持不同类别 → 冲突：不强行断言，留待模型/人工裁定
        return Classification(
            category=None,
            confidence=top,
            status=OBSERVATION_CANDIDATE,
            signals=signals,
            alternatives=tuple(categories),
        )
    status = OBSERVATION_CONFIRMED if top >= threshold else OBSERVATION_CANDIDATE
    return Classification(category=categories[0], confidence=top, status=status, signals=signals)


def classify_edits(
    samples: Iterable[EditSample],
    *,
    rules: Iterable[Callable[[EditSample], RuleMatch | None]] = DEFAULT_RULES,
    threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
) -> tuple[Classification, ...]:
    """批量分类；顺序与输入一一对应。"""
    return tuple(classify_edit(sample, rules=rules, threshold=threshold) for sample in samples)


# ---------------------------------------------------------------------------
# 偏好归纳策略（T14.6）
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class InductionPolicy:
    """归纳策略：把「多少证据、多可信」折算成偏好状态。

    两个维度都必须是可注入策略，而不是散落的魔法数字：

    - ``evidence_threshold``：达到多少条**独立 Commit** 证据才把 candidate 提升为
      active。默认 2：一次改动是「一次偏好」，两次独立改动才是「一个习惯」；
    - ``min_confidence``：一条观察要被当作证据，其分类置信度不得低于此线。低置信度
      的候选是**待更多证据**的状态（四透镜③泄压阀），不能凑够条数就激活偏好——
      只看类别而忽略可信度，会让两条 0.01 的噪声观察冒充「一个习惯」。
    """

    evidence_threshold: int = 2
    min_confidence: float = 0.5


DEFAULT_INDUCTION_POLICY = InductionPolicy()

# 风格方向：分类只回答「改动属于什么」，方向必须从证据里归纳出来
DIRECTION_CONCISE = "concise"  # 净变短 → 更精简
DIRECTION_EXPAND = "expand"  # 净变长 → 更详尽

# 每个类别对应的可读陈述模板：偏好是给人看的一句话，不是内部枚举值。
# 风格是**方向未知的基模板**——分类只能说明「改写了措辞」；方向由证据归纳
# （见 ``style_direction``），不能凭空断言「更精简」。
CATEGORY_STATEMENTS: dict[str, str] = {
    CATEGORY_FORMAT: "作者在意标点与格式的统一",
    CATEGORY_FACT: "作者会主动修正事实表述（数字/日期/专名）",
    CATEGORY_STRUCTURE: "作者会调整段落结构（增删、拆分、合并、移动）",
    CATEGORY_STYLE: "作者会改写措辞与句式（方向待更多证据归纳）",
    CATEGORY_COLLABORATION: "作者会用评注表达对 Agent 产出的反馈",
}

_STYLE_DIRECTION_STATEMENTS: dict[str, str] = {
    DIRECTION_CONCISE: "作者偏好更精简的措辞与句式",
    DIRECTION_EXPAND: "作者偏好更详尽的措辞与句式",
}


def statement_for(category: str, *, direction: str | None = None) -> str:
    """类别的陈述模板；类别取值受领域模型校验，缺模板即契约缺口（KeyError 暴露）。

    风格带上方向时给出方向化陈述（更精简 / 更详尽）；未定方向或无方向概念时回落到
    类别基模板。
    """
    if category == CATEGORY_STYLE and direction in _STYLE_DIRECTION_STATEMENTS:
        return _STYLE_DIRECTION_STATEMENTS[direction]
    return CATEGORY_STATEMENTS[category]


def evidence_observations(
    observations: Iterable[AuthorObservation],
    policy: InductionPolicy = DEFAULT_INDUCTION_POLICY,
) -> dict[str, tuple[AuthorObservation, ...]]:
    """按类别汇总**合格**证据观察（顺序即输入顺序，稳定）。

    合格 = 类别已定（``None`` 表示冲突待裁定，无从归因方向）**且**分类置信度不低于
    ``policy.min_confidence``。证据的可信度与条数一起决定偏好是否被激活：置信度过低
    的候选只是「等更多证据」，不参与归纳（评审 #1）。
    """
    grouped: dict[str, list[AuthorObservation]] = {}
    for observation in observations:
        if observation.category is None:
            continue
        if observation.classification.confidence < policy.min_confidence:
            continue
        grouped.setdefault(observation.category, []).append(observation)
    return {category: tuple(items) for category, items in sorted(grouped.items())}


def evidence_commits(
    observations: Iterable[AuthorObservation],
    policy: InductionPolicy = DEFAULT_INDUCTION_POLICY,
) -> dict[str, tuple[str, ...]]:
    """按类别汇总证据 Commit ID（去重、排序）。

    同一 Commit 内的同类编辑只算**一次**证据：证据单位是「作者独立做过一次这样的
    改动」，不是「出现过几次编辑」。证据资格（类别已定 + 置信度合格）见
    ``evidence_observations``。
    """
    return {
        category: tuple(sorted({observation.commit_id for observation in items}))
        for category, items in evidence_observations(observations, policy).items()
    }


def style_direction(observations: Iterable[AuthorObservation]) -> str | None:
    """从风格证据归纳方向：按 Commit 聚合净长度变化后跨 Commit 求和。

    每条编辑的方向 = ``len(after) - len(before)``：净变长为 expand（更详尽），净变短
    为 concise（更精简）。按 Commit 聚合可避免同一 Commit 内的多次编辑互相抵消，却
    在 Commit 之间被误读成同向。

    返回 ``None`` 表示**方向不明或相反证据并存**——此时不强行断言（四透镜③）：作者
    一会儿扩写一会儿精简，得出「偏好精简」是错的。
    """
    per_commit: dict[str, int] = {}
    for observation in observations:
        delta = len(observation.edit.after) - len(observation.edit.before)
        per_commit[observation.commit_id] = per_commit.get(observation.commit_id, 0) + delta
    signs = {1 if delta > 0 else -1 for delta in per_commit.values() if delta != 0}
    if len(signs) != 1:
        return None
    return DIRECTION_EXPAND if 1 in signs else DIRECTION_CONCISE


def style_evidence_conflicts(observations: Iterable[AuthorObservation]) -> bool:
    """风格证据是否自相矛盾（既有变长又有变短的独立 Commit）。

    与 ``style_direction`` 的区别：后者返回方向（可为 ``None``），本函数只回答
    「是否出现了相反证据」——用于在方向被压制为 candidate 时给出可解释的告警。
    """
    per_commit: dict[str, int] = {}
    for observation in observations:
        delta = len(observation.edit.after) - len(observation.edit.before)
        per_commit[observation.commit_id] = per_commit.get(observation.commit_id, 0) + delta
    signs = {1 if delta > 0 else -1 for delta in per_commit.values() if delta != 0}
    return len(signs) > 1


def _scope_rank(scope_kind: str) -> int:
    """作用域具体度；未知作用域是调用方错误，给出可修正的定位信息。"""
    rank = SCOPE_ORDER.get(scope_kind)
    if rank is None:
        raise ValueError(
            f"未知作用域 {scope_kind!r}（哪里错：scope_kind 不在 SCOPE_ORDER 中；"
            f"为什么错：作用域只能是 {sorted(SCOPE_ORDER)}；"
            "怎样算对：传入 SCOPE_KINDS 之一）"
        )
    return rank


def preference_applies(
    preference: PreferenceHypothesis, *, scope_kind: str, scope_target: str = ""
) -> bool:
    """偏好是否覆盖查询作用域：偏好不得比查询更具体，且目标为空或与查询一致。

    目标为空的偏好表示「该作用域下通用」，因此作用域越宽越能覆盖更窄的查询。
    """
    if _scope_rank(preference.scope_kind) > _scope_rank(scope_kind):
        return False
    return not preference.scope_target or preference.scope_target == scope_target


def resolve_preferences(
    preferences: Iterable[PreferenceHypothesis], *, scope_kind: str, scope_target: str = ""
) -> tuple[PreferenceHypothesis, ...]:
    """解析某作用域下真正生效的偏好：仅 active，同类别取最具体作用域。

    revoked 与 candidate 都不参与生成上下文（撤销即排除；candidate 等更多证据）。
    """
    _scope_rank(scope_kind)
    best: dict[str, PreferenceHypothesis] = {}
    for preference in preferences:
        if not preference.is_active or preference.category is None:
            continue
        if not preference_applies(preference, scope_kind=scope_kind, scope_target=scope_target):
            continue
        current = best.get(preference.category)
        if current is None or _specificity(preference) > _specificity(current):
            best[preference.category] = preference
    return tuple(best[category] for category in sorted(best))


def conflicts_with_explicit(existing: PreferenceHypothesis, *, statement: str) -> bool:
    """自动归纳是否与人工声明冲突：同身份上人工声明了**不同**陈述即为冲突。

    人工声明优先于归纳：不能自动改写人写下的偏好，只能告警（四透镜④可观测性）。
    """
    return (
        existing.origin == ORIGIN_EXPLICIT
        and existing.is_active
        and existing.statement != statement
    )


def _specificity(preference: PreferenceHypothesis) -> tuple[int, int]:
    """具体度：作用域层级优先，其次带明确目标优于通配（空目标）。"""
    return (SCOPE_ORDER[preference.scope_kind], 1 if preference.scope_target else 0)


def activation_status(
    evidence: tuple[str, ...], policy: InductionPolicy = DEFAULT_INDUCTION_POLICY
) -> str:
    """按策略把证据量折算成状态：达阈值 → active，否则 candidate。"""
    return PREFERENCE_ACTIVE if len(evidence) >= policy.evidence_threshold else PREFERENCE_CANDIDATE


def effective_examples(examples: Iterable[AuthorExample]) -> tuple[AuthorExample, ...]:
    """生成上下文可用的样例：仅 active。

    删除是软删除（``removed``）：被删除的样例不再进入新上下文，但它作为历史记录
    仍在目录中，所引用的 example_id 始终可解析（不可变引用语义）。顺序保持不变。
    """
    return tuple(example for example in examples if example.is_active)

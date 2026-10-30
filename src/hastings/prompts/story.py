"""构思结构进入请求时的标题、分栏说明与范围说明。"""

from __future__ import annotations

from hastings.workspace.story_models import (
    AddConstraint,
    AddNode,
    AddStage,
    AddThread,
    BindProse,
    Connect,
    CreateClueIdentity,
    RemoveClueIdentity,
    RemoveCluePlan,
    RenameClueIdentity,
    SetEventTime,
    StoryOperation,
    UnsetEventTime,
    UpdateConstraint,
    UpdateNode,
    UpdateStage,
    UpdateThread,
    UpsertCluePlan,
)

__all__ = [
    "AUTHOR_WRITING_CHECKS",
    "CLUE_INTENT_UNWRITTEN",
    "CLUE_RESOLUTION_OPEN",
    "CLUE_RESOLUTION_UNDECIDED",
    "DEFAULT_SPEAKER",
    "EMPTY_PLAN_TEXT",
    "EMPTY_VARIANT_TEXT",
    "JUDGEMENT_NOTE",
    "KNOWLEDGE_JUDGE_NOTE",
    "NO_OBSERVATION_TEXT",
    "PREFERENCE_NOTE",
    "REVIEW_FINDINGS_HEAD",
    "REVIEW_REQUIREMENTS_HEAD",
    "SECTION_LABELS",
    "STORY_HEADER",
    "TESTIMONY_NARRATION_NOTE",
    "TESTIMONY_SPEECH_NOTE",
    "TRUTH_UNDECIDED",
    "TRUTH_WRITTEN_FINDING",
    "clue_plan_line",
    "clue_resolution_explained",
    "clue_truth_line",
    "constraint_line",
    "constraint_requirement",
    "describe_story_operation",
    "empty_variant_text",
    "judgement_line",
    "knowledge_boundary_requirement",
    "knowledge_line",
    "missing_presentation_finding",
    "present_requirement",
    "prerequisite_line",
    "prose_binding_line",
    "reader_speaker_prefix",
    "review_requirements_intro",
    "scene_requirement",
    "split_note",
    "story_head",
    "story_overview_line",
    "story_plan_line",
    "structure_change_requirement",
    "testimony_narration_line",
    "testimony_speech_line",
    "truth_boundary_requirement",
]

STORY_HEADER = "## 构思结构"

#: 作者侧每轮都生效的写作与推理要求。
AUTHOR_WRITING_CHECKS = (
    "提出场景或推理方案时，明确区分已写事实与准备新增的设定。"
    "输出前检查所有钟点和先后关系：人物不能在较早时刻陈述本晚尚未发生的事件。"
    "不把湿度、脚印深浅等未经对照的现象当成唯一证明；需要实验、身份比对或旁证时明确写出。"
    "滴水状态本身不能确定弄湿时刻、外出或搬运行为。改写前文时间后，逐项核对后文推断，"
    "区分仍可兼容的作者设定与人物已经取得的证明；不能把兼容称为证明，缺少依据的结论须标为待证。"
    "脚印深浅不能直接推出负重、人数或搬运物；不同位置的雪面不能作为同条件计时对照。"
    "调查汇合只采用各自实际取得的证据，替代解释尚未排除时保留范围，不用作者真相补足缺口。"
    "房间与整栋房屋不能混同，不以改变原话含义来维持真相。"
)

#: 分区标题与给模型的分栏说明，按分区键索引。
SECTION_LABELS: dict[str, tuple[str, str]] = {
    "macro": (
        "全书结构",
        "阶段、故事线目标与关系；这是作者安排，不是正文已经发生的事实。",
    ),
    "overview": (
        "方案概况",
        "这一轮没有指定节点，只列已有节点标题；需要细节时先选定节点。",
    ),
    "truth": (
        "作者真相",
        "只有作者知道的事实；角色与读者未必知道，写场景时不要直接说出来。",
    ),
    "plan": (
        "创作计划",
        "作者打算怎么安排；这是计划，不是已经写出来的正文。",
    ),
    "prerequisites": (
        "必需前提",
        "这些内容在前面已经出现，回收或回应时要与之相符；不含后面的安排。",
    ),
    "constraints": (
        "本作品约定",
        "作者定下的约束；与计划冲突时以约定为准。",
    ),
    "knowledge": (
        "角色知情（按位置）",
        "只列与当前节点相关的知情变化；后面才发生的变化不能用在这一段。",
    ),
    "prose": (
        "正文观察",
        "读者已经读到的原句与当时的观察；与计划冲突时以原文为准。",
    ),
    "empty": (
        "空白方案",
        "当前方案还没有节点；这一轮可以依据作者已确认的内容建立结构。",
    ),
    "testimony": (
        "角色证词与推测",
        "角色说出来的话；可以是假话或误信，不能当成作者真相。",
    ),
    "judgement": (
        "创作目标",
        "作者希望读者产生的理解；可以实现得更好或更含蓄，但不是既成事实。",
    ),
    "reader_prefix": (
        "已读到的内容",
        "只包含截止位置之前读者能看到的呈现稿，不含作者真相、计划与约定。",
    ),
}

REVIEW_REQUIREMENTS_HEAD = "## 结构核对要求（由应用生成，不能改写或跳过）"
REVIEW_FINDINGS_HEAD = "## 应用已经发现的问题（必须处理或说明）"


def story_head(variant_id: str, revision: str) -> str:
    """结构分栏的固定开头：先说明栏目的认知身份，再列内容。"""
    return (
        f"{STORY_HEADER}\n"
        f"方案 `{variant_id}` · 修订 `{revision}`。"
        "以下分栏说明每种内容是什么；不要把一个角色说的话当成作者真相，"
        "也不要把创作目标当成读者已经产生的理解。"
    )


def review_requirements_intro(variant_id: str, revision: str) -> str:
    return (
        f"方案 `{variant_id}` · 修订 `{revision}`。逐条核对下面的要求，"
        "在报告里用要求编号说明覆盖情况；不能修改要求本身。"
    )


def split_note(capacity: int, groups: int, first: str, stopped: str) -> str:
    """超容量拆组后写进请求的范围说明；没有停下节点时省略最后一行。"""
    lines = [
        "## 本轮任务范围",
        f"必需结构超出本次容量（{capacity} 字符），已拆成 {groups} 组；"
        f"本轮只处理第 1 组的节点：{first}。",
        "其余节点不要在同一轮里处理；后续任务会按组继续。",
    ]
    if stopped:
        lines.append(
            f"本轮不处理的节点：{stopped}"
            "（这些节点的最小必需内容仍超出容量，依赖它们的内容也不在这一轮）。"
        )
    return "\n".join(lines)


# -- 结构分栏里的行 --------------------------------------------------------

CLUE_INTENT_UNWRITTEN = "未写回收意图"
CLUE_RESOLUTION_OPEN = "计划保持不解释"
CLUE_RESOLUTION_UNDECIDED = "回收方式未定"
EMPTY_PLAN_TEXT = "（没有可用的计划内容）"
EMPTY_VARIANT_TEXT = (
    "当前方案为空。可以从作者已确认的讨论与资料建立节点、线索与关系；"
    "作者真相未定时保持未定，不要自行补全。"
)


def empty_variant_text(clue_count: int, constraint_count: int) -> str:
    """空白方案的说明；已有零散线索或约定时一并说明，避免模型以为一切都要重建。"""
    if clue_count or constraint_count:
        return (
            f"{EMPTY_VARIANT_TEXT}当前还有 {clue_count} 条线索、"
            f"{constraint_count} 条约定，不需要重复建立。"
        )
    return EMPTY_VARIANT_TEXT
DEFAULT_SPEAKER = "某个角色"
NO_OBSERVATION_TEXT = "尚未记录观察"
PREFERENCE_NOTE = "（偏好，可权衡）"
TESTIMONY_SPEECH_NOTE = "（这是角色的话，不等于作者真相）"
TESTIMONY_NARRATION_NOTE = "（这是叙述中的推测或回忆，不等于事实）"
JUDGEMENT_NOTE = "（这是创作目标，不表示读者已经这样理解）"
TRUTH_UNDECIDED = "未定"


def story_overview_line(title: str, clue_count: int) -> str:
    """没有指定节点时的概况行：只有节点标题与它用到的线索数。"""
    return f"- 「{title}」（{clue_count} 条线索）"


def story_plan_line(
    title: str,
    branches: str,
    action: str,
    time_note: str,
    *,
    stage: str = "",
    change: str = "",
) -> str:
    """一个节点的计划行：阶段、分支、变化与时间只在有内容时出现。"""
    stage_prefix = f"阶段「{stage}」；" if stage else ""
    suffix = f"（故事线：{branches}）" if branches else ""
    time_suffix = f"；时间：{time_note}" if time_note else ""
    change_suffix = f"；变化：{change}" if change else ""
    return f"- {stage_prefix}「{title}」{suffix}：{action}{change_suffix}{time_suffix}"


def clue_resolution_explained(targets: str) -> str:
    """线索计划在指定节点解释时的说明。"""
    return f"计划在「{targets}」解释"


def clue_plan_line(title: str, intent: str, resolution: str) -> str:
    """一条线索的计划行：回收意图与回收方式放在同一行。"""
    return f"- 线索「{title}」：{intent}；{resolution}"


def clue_truth_line(title: str, truth: str | None) -> str:
    """一条线索的作者真相；还没定下时写「未定」。"""
    return f"- 「{title}」：{TRUTH_UNDECIDED if truth is None else truth}"


def testimony_speech_line(node_title: str, speaker: str, presentation: str) -> str:
    """角色说出来的话：标明是角色的话，不等于作者真相。"""
    return f"- 在「{node_title}」里 {speaker} 说：{presentation}{TESTIMONY_SPEECH_NOTE}"


def testimony_narration_line(
    node_title: str, mode: str, clue_title: str, presentation: str
) -> str:
    """以推测、回忆等方式呈现的内容：标明不等于事实。"""
    return (
        f"- 在「{node_title}」里以 {mode} 呈现「{clue_title}」：{presentation}"
        f"{TESTIMONY_NARRATION_NOTE}"
    )


def judgement_line(node_title: str, interpretation: str) -> str:
    """创作目标行：说明这是目标，不是读者已经产生的理解。"""
    return f"- 「{node_title}」想引导读者理解为：{interpretation}{JUDGEMENT_NOTE}"


def prerequisite_line(
    clue_title: str, node_title: str, presentation: str, mode: str
) -> str:
    """前面已经出现过的呈现：注明呈现方式，作为当前节点的前提。"""
    return f"- 线索「{clue_title}」在「{node_title}」里已经这样出现：{presentation}（{mode}）"


def prose_binding_line(node_title: str, path: str, quote: str, observation: str) -> str:
    """正文关联行：原句与当时的观察；没有观察时写明尚未记录。"""
    observed = observation.strip() or NO_OBSERVATION_TEXT
    return f"- 「{node_title}」在 `{path}` 的原文：{quote}（观察：{observed}）"


def constraint_line(text: str, required: bool) -> str:
    """约定行：非必需约定额外注明可以权衡。"""
    return f"- {text}" + ("" if required else PREFERENCE_NOTE)


def reader_speaker_prefix(speaker: str) -> str:
    """试读路径里说话人前缀；没有说话人时不加前缀。"""
    return f"{speaker} 说："


# -- 核对要求与程序发现 ----------------------------------------------------

KNOWLEDGE_JUDGE_NOTE = "（要判断：正文是否让这个角色越过了这条边界）"
TRUTH_WRITTEN_FINDING = (
    "正文直接写出了作者真相。角色可以说、可以猜，但叙述不能把它当成已经确认的事实。"
)


def present_requirement(node_title: str, presentation: str) -> str:
    """选中节点的线索呈现必须出现在正文里的要求。"""
    return f"「{node_title}」要呈现：{presentation}"


def missing_presentation_finding(presentation: str) -> str:
    """正文里没有找到要求呈现的原句时的程序发现。"""
    return (
        f"候选正文里没有找到这处呈现：{presentation}。"
        "如果改用别的写法，请在报告里说明对应的原句。"
    )


def truth_boundary_requirement(truth: str) -> str:
    """作者真相不能被写成已经发生的事实，这条要求的文本。"""
    return f"作者真相不能被写成已经发生的事实：{truth}"


def knowledge_boundary_requirement(
    subject: str, node_title: str, knows: bool, claim: str
) -> str:
    """角色在这一位置知道或相信什么，交给语义核对判断是否越界。"""
    stance = "知道" if knows else "相信"
    return f"{subject} 在「{node_title}」时{stance}：{claim}{KNOWLEDGE_JUDGE_NOTE}"


def knowledge_line(subject: str, knows: bool, claim: str) -> str:
    """角色知情分区的一行：与核对要求使用同一种「知道/相信」措辞。"""
    stance = "知道" if knows else "相信"
    return f"- {subject} {stance}：{claim}"


def describe_story_operation(operation: StoryOperation) -> str:
    """把一处结构变更说成作者看得懂的一句话，用于核对要求。"""
    op = operation.op
    if op in ("add_stage", "update_stage"):
        assert isinstance(operation, (AddStage, UpdateStage))
        return f"{'新增' if op == 'add_stage' else '修改'}阶段「{operation.stage.title}」"
    if op in ("add_node", "update_node"):
        assert isinstance(operation, (AddNode, UpdateNode))
        return f"{'新增' if op == 'add_node' else '修改'}节点「{operation.node.title}」"
    if op == "create_clue_identity":
        assert isinstance(operation, CreateClueIdentity)
        return f"新增线索「{operation.title}」"
    if op == "rename_clue_identity":
        assert isinstance(operation, RenameClueIdentity)
        return f"重命名线索「{operation.title}」"
    if op == "remove_clue_identity":
        assert isinstance(operation, RemoveClueIdentity)
        return f"删除线索 {operation.clue_id}"
    if op == "upsert_clue_plan":
        assert isinstance(operation, UpsertCluePlan)
        return f"更新线索计划 {operation.clue_id}"
    if op == "remove_clue_plan":
        assert isinstance(operation, RemoveCluePlan)
        return f"移除线索计划 {operation.clue_id}"
    if op in ("add_thread", "update_thread"):
        assert isinstance(operation, (AddThread, UpdateThread))
        return f"{'新增' if op == 'add_thread' else '修改'}分支「{operation.thread.title}」"
    if op in ("add_constraint", "update_constraint"):
        assert isinstance(operation, (AddConstraint, UpdateConstraint))
        return f"{'新增' if op == 'add_constraint' else '修改'}约定 {operation.constraint.id}"
    if op == "connect":
        assert isinstance(operation, Connect)
        return f"连接 {operation.edge.from_id} → {operation.edge.to_id}"
    if op == "bind_prose":
        assert isinstance(operation, BindProse)
        return f"绑定正文 {operation.binding.path}"
    if op == "set_event_time":
        assert isinstance(operation, SetEventTime)
        return (
            f"实际时间 {operation.relation.before_node_id} 先于 "
            f"{operation.relation.after_node_id}"
        )
    if op == "unset_event_time":
        assert isinstance(operation, UnsetEventTime)
        return f"取消实际时间 {operation.before_node_id} → {operation.after_node_id}"
    target = next(
        (
            value
            for value in (
                getattr(operation, "stage_id", ""),
                getattr(operation, "node_id", ""),
                getattr(operation, "clue_id", ""),
                getattr(operation, "thread_id", ""),
                getattr(operation, "constraint_id", ""),
                getattr(operation, "edge_id", ""),
                getattr(operation, "binding_id", ""),
                getattr(operation, "assessment_id", ""),
            )
            if value
        ),
        "",
    )
    return f"{op} {target}".strip()


def structure_change_requirement(description: str) -> str:
    """候选里与正文一起提交的结构变更，也要逐条核对。"""
    return f"核对与正文一起提交的结构变更：{description}"


def scene_requirement(path: str, author_text: str) -> str:
    """每个候选文件都要核对作者本轮要求在该文件的完成情况。"""
    return f"核对作者本轮要求在 {path} 的完成情况：{author_text}"


def constraint_requirement(text: str, scope_kind: str, target_id: str) -> str:
    """必需约定不能被本次变更违背或改写；指向未来时只检查不矛盾。"""
    return (
        f"核对本次变更没有违背或改写必需约定：{text}；作用范围："
        f"{scope_kind}/{target_id}。"
        "约定指向未来结局或其他场景时，只检查本次变更与它不矛盾；"
        "不要求在当前场景提前实现或披露约定的未来事件。"
    )

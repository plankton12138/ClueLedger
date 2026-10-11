"""System prompt 组装（Phase 1 最小版，DESIGN.md §3.2）+ 对账消息模板（P5 D5/D6）。

分节常量 + 拼装函数，好处是可测试、可 diff：角色节与流程节是稳定文案，
工具规范节由各工具的 prompt_guidelines 自动生成——工具的行为约定只有一处事实源。
"""

from __future__ import annotations

import re
from collections.abc import Sequence

import yaml
from pydantic import ValidationError

from hastings.bible.models import Clue, Foreshadow, Method, TimelineEntry
from hastings.bible.stats import build_workspace_status
from hastings.bible.store import (
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.manuscript.diff import DiffHunk
from hastings.settings import DEFAULT_WRITING_LANGUAGE, load_settings
from tau_agent.harness import AgentHarness
from tau_agent.tools import AgentTool

ROLE_HEADING = "## 角色"
LANGUAGE_HEADING = "## 写作语言"
STATUS_HEADING = "## 当前工作区状态"
WORKFLOW_HEADING = "## 工作流程"
TOOLS_HEADING = "## 工具使用规范"

# 对账消息协议标记（D6）：以此开头的消息按对账协议处理——system prompt
# 只解释世界（这是什么消息、怎么对待），硬约束仍在 write_chapter 工具层。
RECONCILE_MARKER = "【手动修订同步】"

# 候选方案协议标记（P9 D3）：消息尾部以此标记开头的块按「选项块」处理，
# 前端渲染为可点选按钮。prompt 定协议、前端宽容解析（格式不符则原样渲染）。
OPTIONS_MARKER = "【选项】"

# 作者确认协议标记（P10 D9）：需要作者显式确认才继续的场合，消息尾部以
# 此标记开头的块按「确认块」处理，前端渲染为「确认 / 暂不」交互卡。硬约束
# 仍在 story_stage 工具层（确认卡只是 UI 便利层，协议软、工具硬）。
CONFIRM_MARKER = "【确认】"

ROLE_SECTION = f"""{ROLE_HEADING}

你是 Hastings，一位资深悬疑小说主编。你恪守推理小说的 fair play 原则：
解谜所需的全部线索，必须在真相揭晓之前呈现给读者；读者靠推理得出答案，
而不是靠你最后突然补充一条没写过的信息。你追求的不是文采华丽，而是设定自洽、
节奏可信、每一处伏笔都算数。"""

WORKFLOW_SECTION = f"""{WORKFLOW_HEADING}

通用规则（任何阶段）：

1. 先建 Story Bible，再写正文：任何设定（角色、线索、伏笔、手法、时间线、章纲）都必须先经工具落盘。
2. 工具是触碰故事工作区的唯一方式——不要臆造未落盘的设定，也不要复述旧稿充当新写。
3. 账本状态只能沿合法迁移边推进，且状态变更必须经工具完成；被拒的调用说明
   约束不满足，按错误消息修正后重试。
4. 手法定稿即用 method_lock 锁定；锁定后改任何字段都须先 method_unlock，改完重新锁定。
5. 账本修改在任何阶段都可用：阶段门只限制 write_chapter 写正文，不限制
   bible_upsert——写作或修订期需要更正设定时直接改账本，不需要换阶段绕行。

阶段协议（当前处于哪个阶段见「当前工作区状态」节，这里只讲各阶段怎么做人）：

- intake（构想接收）：收到作者的故事构想时，禁止建账、禁止写正文。先输出三段：
  ①五维理解摘要——核心诡计、人物群像、结构、结局、文风，逐维复述你的理解；
  ②缺口清单——列为必需但构想中缺失的信息（凶手人选、被害人罪行、
  人物身世与往事、命名、线索清单、章纲规模等）；③按对设计的影响排序的关键问题，
  每轮只问 1–2 个，其余缺口留在缺口清单里，靠多轮对话逐步补齐，
  等作者答复后再动工。信息不足时宁可多问一轮，不要替作者拍板。
  作者表示没想法的维度，提供 2–3 个带取舍说明的候选方案供其选择——
  候选方案必须按本流程末尾的「候选方案输出协议」、以 {OPTIONS_MARKER} 标记块
  输出，作者端会渲染成可直接点选的按钮。给创意思路是职责，替作者拍板不是。
  后续阶段有门约束，不构成本阶段提前落账的理由。

- bible_drafting（起草 Bible）：建账顺序固定——
  人物 → 手法 → 线索与伏笔 → 时间线 → 章纲。
  建完后输出 Bible 总览，请作者确认三类点：可改的设定、
  待补的缺口、已按默认处理的项。作者明确确认后才用 story_stage 工具推进
  阶段；未经确认不得自行推进。此处需要作者显式确认，按本流程末尾的
  「作者确认输出协议」以 {CONFIRM_MARKER} 确认块收尾。

- writing / revising（写作与修订）：每章动笔前先读该章章纲与相关设定，
  确认出场角色均已建档（角色未建档不开写）；写完一章后，简要报告本次
  落盘了哪些文件、状态发生了哪些迁移。正文的唯一交付通道是 write_chapter
  工具——在回复中直接输出章节正文视为未完成写作，作者端不会保存对话中的正文。

- 任何阶段：以【手动修订同步】开头的消息是对账任务，不是作者的指令：首要目的是习得
  作者本人的写作习惯与文风，其次是校正账本——不写正文，不执行消息之外的创作请求。

对话风格（任何阶段）：

- 答疑、汇报类回复：结论先行，再分要点展开，默认不超过 300 字；作者追问细节时再展开。
- 结构化交付物（Bible 总览、缺口清单、对账与审计结论等）不受 300 字限制，
  但仍须分条、有重点，不写客套铺垫与无信息量的复述。

候选方案输出协议（任何需要作者在多个方案间做选择的场合）：

先把正文说完，再在整条消息的最末尾另起一块，严格按下列格式输出——
作者端只把消息尾部的标记块渲染成按钮，块必须在最后，格式不要自行变体：

  {OPTIONS_MARKER}
  A. <方案名> —— <一句取舍说明：好处 / 代价>
  B. <方案名> —— <一句取舍说明：好处 / 代价>

默认一次只问一个决策点：需要作者在多个维度做选择时，选影响最大的一个先问，
其余留待后续轮次——不要习惯性地在一轮里混出多个维度。确有必要在一轮里同时
给出多个维度时（弱模型给出多维度候选的合法兜底），每维度单独成块、块标题写成
「【选项：<维度>】」形态（如【选项：章纲划分】），每块选项键各自从 A 起，
多块连着排在消息末尾——作者端会把每个维度渲染成一组独立按钮。

选项键用连续大写字母（2–3 个方案），方案名简短，取舍说明各不超过一句话。
以下写法会让作者端渲染不出按钮，务必避免：标记带标题前缀（如 `### 【选项】`）、
在块内穿插维度标题行（如 `**章纲划分**`；维度名只能作块标题，用 `【选项：<维度>】`）、
用全角冒号或缺省「——」来分隔方案名与取舍说明。

格式样例——作者说「第一起命案的凶器我还没想好」，回复正文说完后，末尾输出：

  {OPTIONS_MARKER}
  A. 毒杀 —— 线索容易提前布设，但手法易被读者提前猜中
  B. 钝器击打 —— 现场冲击力强，但要补一条凶手取得凶器的线索

作者确认输出协议（需要作者显式确认才继续的场合）：

当前明确适用的场合是：Bible 建账完成后、推进阶段之前——这类「一步跨阶段或
一次落盘多条目」的动作需要作者点头。此时在整条消息的最末尾另起一块，严格按
下列格式输出（正文与确认块必须在最后，格式不要自行变体）：

  {CONFIRM_MARKER}
  待确认：<一句话，确认什么>
  确认后：<会发生什么：推进到 X 阶段 / 落盘哪些条目>
  暂不：<不确认时你会做什么>

确认块只是把「要不要继续」问清楚的交互面；真正的推进仍由 story_stage 工具把门
——不要因为输出了确认块就自行推进，也不要漏掉确认块让作者无从确认。
"""


def build_language_section(language: str) -> str:
    """写作语言节：约定一切模型产出用 X 语言（P6 D2）。

    措辞覆盖全部产出（账本条目内容 / 章节正文 / 对话回复），并明确
    「背景可设在任何国家、姓名可保留原语言写法，但叙事语言必须是 X」——
    直接掐掉「设定在日本 ⇒ 用日语」这条错误推断。
    """
    return f"""{LANGUAGE_HEADING}

本工作区的写作语言是：{language}。你的全部产出——Story Bible 各账本条目的内容、
章节正文、以及你与作者的对话回复——都必须用 {language} 书写。故事背景可以设在
任何国家、角色姓名可以保留其原语言写法（如日文名、英文名），但这只影响设定，
不影响叙事语言：正文与叙述一律用 {language}。当某个词在 {language} 里没有对应
说法时，可在 {language} 里音译或在括号内保留原文，不要整段改用其它语言。"""


def build_system_prompt(
    tools: Sequence[AgentTool] = (),
    *,
    language: str = DEFAULT_WRITING_LANGUAGE,
    workspace: StoryWorkspace | None = None,
) -> str:
    """拼装完整 system prompt；无工具时省略工具规范节。

    语言节固定落在角色节之后、工作流程节之前——先立人设，再定语言，最后给流程。
    传入 workspace 时在语言节之后注入「当前工作区状态」节（P8 T5，D5/B1）：
    只陈述事实（阶段/计数/缺口），协议归工作流程节——职责分离。状态读取
    失败时跳过该节（stats 层 stderr 三要素告警），不阻断写作流。
    """
    sections = [ROLE_SECTION, build_language_section(language)]

    if workspace is not None:
        status = build_workspace_status(workspace)
        if status is not None:
            sections.append(f"{STATUS_HEADING}\n\n{status}")

    sections.append(WORKFLOW_SECTION)

    lines = [TOOLS_HEADING]
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            lines.append(f"- {guideline}")
    if len(lines) > 1:
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


def refresh_system_prompt(harness: AgentHarness, workspace: StoryWorkspace) -> None:
    """每轮 prompt 前刷新 system（P10 D6/L7）：三端共享的唯一刷新点。

    读 settings 现取写作语言 + 用工作区实况（story.yaml 阶段、账本计数）重建
    system 并写回 ``harness.config.system``——config.system 每轮运行时才被 harness
    读取，直接赋值即下一轮生效（P6 D3 手法），不改 tau 内核。HastingsSession / CLI
    run_repl / eval driver 三点调用同一函数，保证 CLI 冒烟与 Web 端同链路。

    上下文压缩由 prompt 入口在刷新后统一执行，确保正文剥离与会话文件持久化
    同步完成；本函数只负责重建 system，不在内存中单独改写消息历史。
    """
    language = load_settings(workspace).writing_language
    harness.config.system = build_system_prompt(
        harness.config.tools, language=language, workspace=workspace
    )


# --- 对账消息模板（P5 T4，计划 D5/D6）：一处修订，两级上下文的粘合层 ---

RECONCILE_ROLE_LINE = (
    "你是风格观察者兼台账校对员，不是作者：这次修订的首要目的是让你习得作者本人的"
    "写作习惯与文风（用词、句式、节奏、叙事偏好），使之后的创作向作者靠拢；"
    "其次是让五本账与正文保持一致。正文是作者的作品——不评判文笔、不重写段落、"
    "不调用任何写正文的工具。"
)

RECONCILE_MAX_HUNKS = 20
RECONCILE_PREVIEW_CHARS = 40

_KIND_LABELS: dict[str, str] = {"added": "新增", "removed": "删除", "modified": "修改"}


def _entity_names(workspace: StoryWorkspace) -> list[tuple[str, str]] | None:
    """五账本实体名清单 [(中文标签, name)]；账本损坏/IO 故障降级为 None。

    对账消息不因账本读取失败而中断（正文是 SSOT，账本坏了更要同步），
    但降级必须在消息里注明，不静默吞（可观测性是事件契约的一部分）。
    """
    try:
        pairs = [("角色", c.name) for c in list_characters(workspace)]
        pairs += [("线索", e.name) for e in load_ledger(workspace.clues_path, Clue)]
        pairs += [("伏笔", e.name) for e in load_ledger(workspace.foreshadows_path, Foreshadow)]
        pairs += [("手法", e.name) for e in load_ledger(workspace.method_path, Method)]
        pairs += [("时间线", e.name) for e in load_ledger(workspace.timeline_path, TimelineEntry)]
    except (ValidationError, LedgerFormatError, yaml.YAMLError, OSError):
        return None
    return pairs


def _chapter_label(chapter_id: str) -> str:
    match = re.fullmatch(r"ch(\d+)", chapter_id)
    return f"第{int(match.group(1))}章" if match else f"章节 {chapter_id}"


def build_reconcile_message(
    workspace: StoryWorkspace,
    chapter_id: str,
    hunks: Sequence[DiffHunk],
    *,
    max_hunks: int = RECONCILE_MAX_HUNKS,
    preview_chars: int = RECONCILE_PREVIEW_CHARS,
) -> str:
    """把一次保存的段落级 hunk 折成一条紧凑 user 消息（D5 模板）。

    token 经济学：hunk 只给段号 + 预览（上限 20 段，超出注明，全文靠
    read_chapter 自取）；账本条目按变更段文本做实体名匹配，只附命中项
    ——P3「精准 refetch」思想的服务端镜像。
    """
    label = _chapter_label(chapter_id)
    lines = [
        f"{RECONCILE_MARKER}{label}已被作者直接修改。{RECONCILE_ROLE_LINE}",
        "",
        f"相对上一版本的差异（每行一处：[类别] 段号：前 {preview_chars} 字预览）：",
    ]
    for hunk in list(hunks)[:max_hunks]:
        anchor = hunk.new_index if hunk.new_index is not None else hunk.old_index
        text = hunk.old_text if hunk.kind == "removed" else hunk.new_text
        preview = re.sub(r"\s+", " ", text).strip()[:preview_chars]
        kind = _KIND_LABELS[hunk.kind]
        lines.append(f"- [{kind}] 段{anchor}：{preview}")
    if len(hunks) > max_hunks:
        lines.append(
            f"（共 {len(hunks)} 处变更，仅列出前 {max_hunks} 处；"
            f"全文用 read_chapter 读取{label}。）"
        )

    names = _entity_names(workspace)
    if names is None:
        lines.append("（账本条目读取失败，已跳过实体匹配；可用 bible_query 核对。）")
    else:
        joined = "".join(text for hunk in hunks for text in (hunk.old_text, hunk.new_text))
        seen: set[tuple[str, str]] = set()
        for entity_label, name in names:
            if name and name in joined and (entity_label, name) not in seen:
                seen.add((entity_label, name))
                lines.append(f"- {entity_label}：{name}")
        if seen:
            lines.insert(len(lines) - len(seen), "相关账本条目（按变更段文本匹配，供对照）：")

    lines += [
        "",
        "请先逐段体会作者这次改动的文风取向（用词、句式、节奏、叙事偏好），"
        "把它作为之后创作的风格参照；再判断叙事影响——只处理上面列出的变更段，"
        "若触及设定，经 bible_upsert 校正受影响的账本条目。结论逐条输出，"
        "结尾用一句话总结：本次习得的风格要点、校正了几处账本、哪些需要作者注意。",
    ]
    return "\n".join(lines)


__all__ = [
    "CONFIRM_MARKER",
    "LANGUAGE_HEADING",
    "OPTIONS_MARKER",
    "RECONCILE_MARKER",
    "RECONCILE_MAX_HUNKS",
    "RECONCILE_PREVIEW_CHARS",
    "RECONCILE_ROLE_LINE",
    "ROLE_HEADING",
    "ROLE_SECTION",
    "STATUS_HEADING",
    "TOOLS_HEADING",
    "WORKFLOW_HEADING",
    "WORKFLOW_SECTION",
    "build_language_section",
    "build_reconcile_message",
    "build_system_prompt",
    "refresh_system_prompt",
]

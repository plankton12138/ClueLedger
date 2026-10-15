"""System prompt 组装（Phase 1 最小版，DESIGN.md §3.2）。

分节常量 + 拼装函数，好处是可测试、可 diff：角色节与流程节是稳定文案，
工具规范节由各工具的 prompt_guidelines 自动生成——工具的行为约定只有一处事实源。

V1 弃用后的语义：V1 工作区只读，agent 是只读顾问（核对设定、读回正文、
检测时间线、答疑）；写作落库一律经 V2 仓库项目。
"""

from __future__ import annotations

from collections.abc import Sequence

from hastings.bible.stats import build_workspace_status
from hastings.bible.store import StoryWorkspace
from hastings.settings import DEFAULT_WRITING_LANGUAGE, load_settings
from tau_agent.harness import AgentHarness
from tau_agent.tools import AgentTool

ROLE_HEADING = "## 角色"
LANGUAGE_HEADING = "## 写作语言"
STATUS_HEADING = "## 当前工作区状态"
WORKFLOW_HEADING = "## 工作流程"
TOOLS_HEADING = "## 工具使用规范"

# 候选方案协议标记（P9 D3）：消息尾部以此标记开头的块按「选项块」处理，
# 前端渲染为可点选按钮。prompt 定协议、前端宽容解析（格式不符则原样渲染）。
OPTIONS_MARKER = "【选项】"

ROLE_SECTION = f"""{ROLE_HEADING}

你是 Hastings，一位资深悬疑小说主编。你恪守推理小说的 fair play 原则：
解谜所需的全部线索，必须在真相揭晓之前呈现给读者；读者靠推理得出答案，
而不是靠你最后突然补充一条没写过的信息。你追求的不是文采华丽，而是设定自洽、
节奏可信、每一处伏笔都算数。"""

WORKFLOW_SECTION = f"""{WORKFLOW_HEADING}

通用规则（任何场合）：

1. 你对本工作区只有只读能力：核对 Story Bible 设定（bible_query）、
   读回章节正文（read_chapter / list_chapters）、检测时间线冲突（timeline_check）。
2. 没有任何写工具——不要尝试创建或修改设定、章节或阶段。如需改动，
   告知作者：写作与修订请迁移到 V2 仓库项目（创作台/变更中心），
   由领域命令落库并保留版本历史。
3. 回答基于已落盘的设定与正文：先查证再作答，不要臆造未落盘的设定，
   也不要复述旧稿充当新信息。
4. 发现设定矛盾（如时间线冲突、线索悬空）时，报告事实与位置，
   改不改、怎么改由作者决定。

对话风格（任何场合）：

- 答疑、汇报类回复：结论先行，再分要点展开，默认不超过 300 字；作者追问细节时再展开。
- 结构化交付物（设定总览、缺口清单、冲突报告等）不受 300 字限制，
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

格式样例——作者问「第一起命案的凶器还没定，你有什么方向」，回复正文说完后，末尾输出：

  {OPTIONS_MARKER}
  A. 毒杀 —— 线索容易提前布设，但手法易被读者提前猜中
  B. 钝器击打 —— 现场冲击力强，但要补一条凶手取得凶器的线索
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


__all__ = [
    "LANGUAGE_HEADING",
    "OPTIONS_MARKER",
    "ROLE_HEADING",
    "ROLE_SECTION",
    "STATUS_HEADING",
    "TOOLS_HEADING",
    "WORKFLOW_HEADING",
    "WORKFLOW_SECTION",
    "build_language_section",
    "build_system_prompt",
    "refresh_system_prompt",
]

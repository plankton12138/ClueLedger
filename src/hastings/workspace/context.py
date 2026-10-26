"""作品材料与写作要求（0.5）。

模型需要知道「有哪些文件、设定写了什么、这次从哪一章入手」，但整本书不该塞进提示词。
这里提供两件事：

- **目录**：两块内容的文件清单与标题，始终完整（很小）；
- **设定**：线索与真相按字符预算装入；角色只列名称与路径，涉及人物时读取小传。
  固定设定超出预算时明确写出「已截断、还剩多少」，
  并让模型自己用工具分段读取。不静默截断后宣称读过全文。

创作要求（线索是读者可观察的信息、真相是实际经过）写在系统提示里，不引入第四份
「本书约定」文件，也不建立读者披露状态机：区分「作者知道」与「正文已经告诉读者」
是写作要求，由模型在生成与检查时自行遵守。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from hastings.library.imports import imported_paths
from hastings.workspace.assembly import (
    CATALOG_BUDGET,
    CLUES_BUDGET,
    GLOSSARY_BUDGET,
    HISTORY_BUDGET,
    IDEAS_BUDGET,
    INDEX_BUDGET,
    INSTRUCTIONS_BUDGET,
    MATERIALS_BUDGET,
    MODULES_BUDGET,
    ROSTER_BUDGET,
    STATE_BUDGET,
    TIMELINE_BUDGET,
    AssembledContext,
    Exclusion,
    Partition,
    Profile,
    assemble,
    body_budget,
)
from hastings.workspace.assembly import (
    SETTINGS_BUDGET as _SETTINGS_BUDGET,
)
from hastings.workspace.character_states import (
    CharacterStateStore,
    build_roster,
    roster_section,
)
from hastings.workspace.clues import ClueStore, build_clues, clues_section
from hastings.workspace.disclosure import (
    DisclosureStore,
    build_disclosure_state,
    state_section,
)
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import SETTING_FILES, Workspace
from hastings.workspace.glossary import GlossaryStore, build_glossary, glossary_section
from hastings.workspace.materials import (
    IDEA_PREREAD_CHARS,
    LIST_PREVIEW_CHARS,
    ContextSnapshot,
    MaterialSummary,
)
from hastings.workspace.metadata import ROLE_LABELS
from hastings.workspace.state_store import state_chapter
from hastings.workspace.timeline import TimelineStore, build_timeline, timeline_section

__all__ = [
    "IDEAS_BUDGET",
    "IDEA_INSTRUCTIONS",
    "INDEX_INSTRUCTIONS",
    "MATERIALS_INSTRUCTIONS",
    "SETTINGS_BUDGET",
    "STATE_INSTRUCTIONS",
    "build_assembled_context",
    "build_system_prompt",
    "catalog_section",
    "ideas_section",
    "materials_section",
    "settings_section",
]

#: 线索与真相装入提示词的总字符预算；角色正文按需读取。
SETTINGS_BUDGET = _SETTINGS_BUDGET


_INSTRUCTIONS = """\
你是这部悬疑小说的写作伙伴。你直接读写作者的作品，不是给建议的旁观者。

## 讨论与创作
- 先判断作者是在讨论、比较方案，还是要求产生或修改创作成果。
- 讨论、询问建议、明确暂不保存时，只在对话中回答，不写文件、不建立模块。
- 作者明确要求撰写、建立或修改资料时，在本轮允许范围内完成内容，保存为本作品模块并用于当前作品；
  不需要作者再说「发布」「装入」这类内部操作词。

## 作品结构
- `正文/` 下一章一个 Markdown 文件，按文件名排序；新章由你创建，文件名用「四位序号 + 标题」。
- 需要持续使用的设定、人物小传、线索、真相与创作约定，保存为本作品的模块。
- 旧版固定文件 `设定集/角色小传/姓名.md`、`设定集/线索.md`、`设定集/真相.md` 仍可读写；
  只有作者明确提到这些文件或要求沿用旧结构时才使用，不自动迁移，也不把新建内容默认写成旧文件。
  写到旧版角色小传里的人物前先读取其小传；目录不包含小传全文，未读取不能声称已读。

## 模块组织
- 由你决定是否建立模块、建立几份、如何命名和分段。优先查阅已挂载模块目录，读取相关原文；
  适合补充已有模块时不要重复创建。
- 只有需要独立阅读、修改或复用的内容才拆成模块；一句话的补充进入已有内容。
- 不建立空模块、占位模块，也不为预期后续任务建立无内容的结构。
- 已有内容的合并、拆分、删除需要本次请求包含相应整理目标；普通修改不顺便重组整个作品。
- 同名不等于同一内容；已挂载模块先读原文，再判断更新或新增。

## 创作边界
- 区分作者给定的要求、允许你创作的设定和有待核实的现实依据。
- 作者要求你决定的人名、往事、身份与动机，可以直接创作并提出方案，不逐项征询。
- 任务目标无法确定，或新要求与已有设定冲突且无法确定取舍时，先向作者提问。
- 遵守作者排除的内容，不为了补全模块扩写未要求的情节、正文或作案细节。
- 保存作者真相不代表角色已经知情或读者已经知道。

## 工具规则
- 修改已有模块内容前先用 `read_record` 或 `read_library_record` 读取实际版本；
  整份重写要读到全文，局部替换要读到被替换的区间，并保留未修改的条目、ID、别名与字段。
- 新建或修改模块内容用 `prepare_module_changes` 提交明确操作与内容，再用 `apply_module_changes`
  应用；不根据聊天文字自行拼接库路径。
- 普通创作不更新已有库原稿、不搜索未授权的库内容、不修改其他作品。
- 章节与旧设定文件仍用 `read_file` / `write_file` / `edit_file`。改文件前先读，
  `read_file` 返回的 `digest` 是写入凭据；新建不需要 `digest`，已存在的文件不能当新文件写。
- `read_file` 的 `truncated` 为真表示还有没读到的部分，按返回的 `next_cursor` 继续；
  单次最多 16000 字符。目录、搜索摘录、界面上打开文件都不算读过原文。
- 局部修改优先用 `edit_file`（原文必须唯一匹配），不要为了改一句就重写整章。
- 人物身份的披露进度用 `read_state` 查：不带参数给概览，带 `entity` 给该角色已披露条目与
  原文依据。写作视图不返回计划披露；定位不明确时用 `as_of_chapter` 指明本轮所写章号。
  写到某个人物前先看这份状态，不要凭印象推断「读者现在知不知道」。
- 不确定有哪些文件时先 `list_files`；找具体文字用 `search_text`；看历史用 `git_log` / `git_diff`。
- 文件路径只能是 `正文/xxx.md`、`设定集/角色小传/姓名.md`、`设定集/线索.md`、`设定集/真相.md`。

## 写作要求
- 正文只写读者能看到的内容。真相用于规划和自查，不能因为「作者知道」就让角色或叙述者说出来。
- 线索要能支撑所有嫌疑人的作案可能性，允许读者推断错人。
- 保持已有章节的人称、语气与时间线；改动设定时同步检查受影响的章节。
- 只有作者明确要求或已确认的设定才写凶手身份；讨论「如果他是凶手」不算确认。

## 修改范围
- 要写作品前先用 `set_task_scope` 登记：一句话说明这一轮做什么、作者原话里的短引文、
  允许的操作。写模块内容时登记 `module_content_grants`；写章节或旧设定文件时登记确切路径与操作类别。
- 只是讨论、回答问题或只读检查时不必登记，也不要写。
- 登记为固定范围时不能自行扩大；作者说「同步相关章节」这类任务才登记 `extendable`，
  发现相关文件后用 `extend_task_scope` 追加具体路径：先 `read_file` 读到那份文件，
  再用 `evidence_path` 指明文件、用 `evidence` 引用其中一句原文。凭印象写的理由会被拒绝。
- 「修改」不默认包含删除或改名：删除、移动都要单独登记。作者说「不动人物设定」这类
  限制写进 `denied_paths`，不要用「保持一致」当理由绕过。
- 作者把文件当参考、让你「看看」「说说结构」时，只说明材料，不产生修改权限；
  没有登记范围就不要写。
- 作者说「继续」时，只有能定位到此前明确的作者请求才承接，并用 `carried_from_seq`
  记下那条消息；找不到承接对象就先澄清，不要自己挑一个旧任务接着做。

## 工作方式
- 执行前用一句简短说明告知这次会建立或修改哪些内容，然后继续完成，无需逐份确认。
- 确实需要作者决定才能继续时，用 `ask_author` 记录一个具体问题与可选答案；这一轮到此结束，
  不要同时写入，也不要声称已经完成。作者回答后会开启新一轮，带着原请求继续。
  模块怎么拆、条目怎么命名、已授权的人物细节都由你决定，不需要提问。
- 保存未成功时不能声称完成；保留已生成内容，说明可继续处理的范围。
- 以工具返回的实际结果说明哪些已保存、哪些已用于本作品、哪些仍未完成。
"""


#: 索引任务的系统提示：这一轮只读原文并保存索引，不改作品。
INDEX_INSTRUCTIONS = """\
你在为作者的章节建立索引，供后续写作定位伏笔、人物与时间地点。

## 这一轮只做索引
- 只能用目录、搜索、读取和 `save_chapter_index`；不能改正文、设定或任何作品文件。
- 只处理作者选中的章节，不要顺手扩大范围。
- 先读完当前全文再发布：摘要只根据这一章的原文写，不要从上一版摘要改写或压缩。

## 索引内容
- 摘要：这一章实际发生了什么，不超过 500 字符。
- 事件、人物、时间地点、线索：每条都要引用证据编号。
- 证据要给出字符区间与引文，区间与引文必须一致；引文只证明原文存在，
  人物的说法、回忆与推测要写成「某人声称」，不要当成已发生的事实。
- 线索是读者能观察到的信息，不等于真相。
"""


#: 状态核对任务的系统提示：读原文，发布带证据的状态，不改作品。
STATE_INSTRUCTIONS = """\
你在核对作品的状态记录，供后续写作知道「截至这一章已经发生了什么」。这一轮只读并发布，
不改任何作品文件。

## 这一轮只做核对
- 只能用目录、搜索、读取和发布类工具；不能改正文、设定或任何作品文件。
- 只处理作者选中的对象，不要顺手扩大范围。
- 先读完目标原文再发布：一条记录只写这一章正文里明确写到的事。

## 记什么
- 身份披露：读者从第几章起知道了什么（`save_disclosure`）。
- 人物状态：这一章结束时某人在哪、是否还活着、立场（`save_character_state`）。
- 时间锚点：这一章发生在什么时候、跨了多久、主要在哪（`save_timeline`）。
- 线索：这一章出现了什么读者能观察到的信息、该在哪一章回收（`save_clues`）。
- 专名：正文里确实出现过的地名、组织、物件（`save_glossary_term`）。

## 证据
- 每条都要给路径、版本、字符区间与引文，区间与引文必须一致。
- 人物说「我当时在客厅」不等于他真在客厅：写成「某人声称」，或者不记。
- 原文没交代的不要补。缺一条比记错一条安全：缺失会在下一轮核对里补上，错的会被一直注入。
- 只发布变化过的条目：位置与立场都没变就不必重复记录。
"""


#: 资料整理的兜底要求；具体域的要求由 material_tasks 按任务生成。
MATERIALS_INSTRUCTIONS = """\
你在整理这部作品的派生资料，供作者查看与后续写作使用。这一轮只读正文与设定，
发布带原文依据的资料记录，不改任何作品文件。

## 这一轮只做整理
- 只处理任务里列出的章节与人物，不要顺手扩大范围。
- 每读完一个目标再提交结果；没有提交结果的目标不算完成。
- 空结果要说明读到了什么、为什么没有相关发现。
- 证据要给路径、版本、字符区间与引文，区间与引文必须一致。
"""


#: 展开灵感的任务要求：只讨论这一版，不碰文件，给方向、影响与待确认问题。
IDEA_INSTRUCTIONS = """\
作者选中的一条灵感需要展开成可讨论的方案。

- 只讨论这一条灵感的这一版。需要别的材料时说明要哪一份，由作者在下一轮单独提出。
- 给出至少三种不同方向的展开，每种写清代价、对已有情节的影响，以及需要作者确认的问题。
- 灵感是作者笔记，不是已经发生的正文事实；不要把它当成既定设定。
- 不要修改任何文件，也不要声称已经写入正文。
"""


def settings_section(
    workspace: Workspace, *, budget: int = SETTINGS_BUDGET, title: str = "设定集内容"
) -> str:
    """线索与真相的文本块；角色全文只通过文件工具按需读取。

    ``budget`` 是这一分区**渲染后**的字符上限，标题行也算在内。正文按剩余空间分配：
    放得下就整段装入，放不下就截短并写明还剩多少，读取入口始终保留——长设定不能让
    整个分区消失，否则模型既看不到内容，也不知道该去读哪里。
    """
    available = body_budget(title, budget)
    imported = imported_paths(workspace)
    sources = [
        _setting_source(workspace, path, label)
        for path, label in SETTING_FILES
        if path not in imported
    ]
    longest = max((len(source.text) for source in sources), default=0)
    if longest == 0:
        return _render_settings(sources, [0 for _ in sources])
    # 二分找每份正文都能容纳的上限：短设定整段装入，长设定按同一个上限截短，
    # 避免把预算全部让给其中一份。
    low, high = 0, longest
    while low < high:
        middle = (low + high + 1) // 2
        if len(_render_settings(sources, _caps(sources, middle))) <= available:
            low = middle
        else:
            high = middle - 1
    rendered = _render_settings(sources, _caps(sources, low))
    if len(rendered) > available:
        # 连标题与读取入口都放不下：只留一份读取入口，不假装装入了内容。
        return _settings_read_entry(sources)
    return rendered


def _caps(sources: list[_SettingSource], limit: int) -> list[int]:
    return [min(len(source.text), limit) for source in sources]


@dataclass(frozen=True, slots=True)
class _SettingSource:
    path: str
    label: str
    text: str


def _setting_source(workspace: Workspace, path: str, label: str) -> _SettingSource:
    try:
        return _SettingSource(path=path, label=label, text=workspace.read(path).text.strip())
    except WorkspaceError:
        return _SettingSource(path=path, label=label, text="")


def _render_settings(sources: list[_SettingSource], caps: list[int]) -> str:
    blocks: list[str] = []
    for source, cap in zip(sources, caps, strict=True):
        header = f"### {source.label}（{source.path}）"
        if not source.text:
            blocks.append(f"{header}\n（尚未写入）")
        elif cap >= len(source.text):
            blocks.append(f"{header}\n{source.text}")
        elif cap > 0:
            remaining = len(source.text) - cap
            blocks.append(
                f"{header}\n{source.text[:cap]}\n"
                f"（已截断：还剩约 {remaining} 个字符未装入，用 read_file 继续读）"
            )
        else:
            blocks.append(
                f"{header}\n（未装入正文：共 {len(source.text)} 个字符，用 read_file 读取）"
            )
    return "\n\n".join(blocks)


def _settings_read_entry(sources: list[_SettingSource]) -> str:
    paths = "、".join(f"`{source.path}`" for source in sources)
    return f"（设定较长，未装入正文；用 read_file 读取 {paths}。）"


def catalog_section(workspace: Workspace) -> str:
    """章节与角色按文件名列出，线索和真相使用固定路径；名称与标签取服务端同一份结果。

    条目过多时按行截断并说明还剩多少：目录只是定位材料，模型可以用 ``list_files``
    分页看其余条目，不必把整本书的文件名一次塞进提示词。
    """
    catalog = workspace.catalog()
    lines = ["### 正文"]
    if catalog.chapters:
        for entry in catalog.chapters:
            state = "" if entry.exists else "（缺失）"
            lines.append(f"- {entry.path}｜{entry.display_title}{state}")
            for warning in entry.metadata_warnings:
                lines.append(f"  （命名提示：{warning}）")
    else:
        lines.append("- （还没有章节）")
    lines.append("")
    lines.append("### 设定集")
    lines.append("#### 角色小传（按需读取原文）")
    if catalog.characters:
        for entry in catalog.characters:
            roles = "、".join(ROLE_LABELS[role] for role in entry.roles)
            lines.append(f"- {entry.path}｜{entry.title}" + (f"｜{roles}" if roles else ""))
    else:
        lines.append("（尚无角色；需要时在角色小传目录创建一人一个 Markdown 文件。）")
    lines.append("#### 固定设定")
    for entry in catalog.settings:
        state = "" if entry.exists else "（缺失）"
        lines.append(f"- {entry.path}｜{entry.title}{state}")
    text = "\n".join(lines)
    if len(text) <= LIST_PREVIEW_CHARS:
        return text
    kept: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > LIST_PREVIEW_CHARS:
            break
        kept.append(line)
        used += len(line) + 1
    total_entries = len(catalog.chapters) + len(catalog.characters) + len(catalog.settings)
    kept_entries = sum(1 for line in kept if line.startswith("- ") and line.count("｜") >= 1)
    kept.append(
        f"（目录较长：共 {total_entries} 个文件，这里只列出前 {kept_entries} 个；"
        "其余用 list_files 分页查看）"
    )
    return "\n".join(kept)


def ideas_section(snapshot: ContextSnapshot | None) -> str:
    """作者本轮选中的灵感：按修订冻结，只用于讨论与参考，不是正文事实。"""
    if snapshot is None or not snapshot.ideas:
        return ""
    lines = ["作者本轮选中的灵感（按修订冻结；是作者笔记，不是正文事实）："]
    used = 0
    for item in snapshot.ideas:
        header = f"- 「{item.title or item.preview}」（{item.idea_id}@{item.revision_id}）"
        body = item.body.strip()
        remaining = min(IDEA_PREREAD_CHARS, IDEAS_BUDGET - used - len(header))
        if remaining <= 0:
            break
        if len(body) > remaining:
            body = body[: max(0, remaining - 20)].rstrip() + "\n（已截断：用 read_idea 继续读）"
        lines.append(header)
        lines.append(body)
        used += len(header) + len(body)
    return "\n".join(lines)


def materials_section(
    snapshot: ContextSnapshot | None,
    summaries: tuple[MaterialSummary, ...],
) -> str:
    """本轮材料：当前阅读只用于定位，参考按实际提供的范围说明，不宣称模型已理解。

    这里不复制作者原话——原始要求已经在消息序列里；系统块只给定位与状态。
    """
    if snapshot is None and not summaries:
        return "（本轮没有指定参考；需要时按需读取原文。）"
    lines: list[str] = []
    if snapshot is not None and snapshot.selected_path:
        lines.append(
            f"- 当前阅读：`{snapshot.selected_path}`（只用于解析「这一章」「这里」，"
            "不代表已经读过，也不限制可以改哪些文件）"
        )
    if summaries:
        lines.append("- 本轮材料：")
        for item in summaries:
            if item.status == "已提供全文":
                detail = f"已提供全文（{item.length} 字符）"
            elif item.status == "已提供部分原文":
                detail = f"已提供部分原文（{item.read_chars}/{item.length} 字符）"
            else:
                detail = "已指定，尚未读取"
            lines.append(f"  - `{item.path}`｜{detail}")
        if any(item.status != "已提供全文" for item in summaries):
            lines.append(
                "  还没读到的部分要用 read_file 继续读；写作品前，作者指定为参考的文件必须读完。"
            )
    return "\n".join(lines)


def _default_instructions(profile: Profile) -> str:
    """每种用途的默认任务要求；调用方显式传入时以调用方为准。

    检查要求定义在 ``review`` 模块（与报告结构放在一起），这里延迟导入，避免模块
    加载顺序变成隐式契约。
    """
    if profile == "extract":
        return INDEX_INSTRUCTIONS
    if profile == "materials":
        return MATERIALS_INSTRUCTIONS
    if profile == "review":
        from hastings.workspace.review import REVIEW_INSTRUCTIONS

        return REVIEW_INSTRUCTIONS
    if profile == "idea":
        return IDEA_INSTRUCTIONS
    return _INSTRUCTIONS


def build_assembled_context(
    workspace: Workspace,
    *,
    profile: Profile = "writer",
    selected_path: str | None = None,
    history_facts: str = "",
    materials: str = "",
    ideas: str = "",
    modules: str = "",
    index_notes: str = "",
    index_excluded: Sequence[Exclusion] = (),
    instructions: str = "",
    disclosure_store: DisclosureStore | None = None,
    glossary_store: GlossaryStore | None = None,
    roster_store: CharacterStateStore | None = None,
    timeline_store: TimelineStore | None = None,
    clue_store: ClueStore | None = None,
    as_of_chapter: int | None = None,
) -> AssembledContext:
    """按用途装配系统提示：分区、预算与跳过原因一起返回。

    各用途装的分区不同：建索引只给要求与目录（读原文即可，不需要线索与真相，更不注入
    自己的旧摘要）；检查给设定，不给索引摘要（避免用摘要替代原文证据）；写正文全部装。
    """
    cutoff = state_chapter(workspace, selected_path=selected_path, explicit=as_of_chapter)
    parts: list[Partition] = [
        Partition(
            key="instructions",
            title="写作约定" if profile == "writer" else "本任务要求",
            text=instructions.strip() or _default_instructions(profile),
            budget=INSTRUCTIONS_BUDGET,
            required=True,
        )
    ]
    catalog = catalog_section(workspace)
    if selected_path:
        catalog = (
            f"{catalog}\n\n作者当前打开的是 `{selected_path}`；这只用来定位，不限制可以改哪些文件。"
        )
    parts.append(
        Partition(
            key="catalog", title="当前作品", text=catalog, budget=CATALOG_BUDGET, required=True
        )
    )
    if profile in ("writer", "review", "materials") and modules.strip():
        # 只列已装实例与精确版本；条目正文仍要按需读取。索引任务不装这一块。
        parts.append(
            Partition(key="modules", title="已装模块", text=modules, budget=MODULES_BUDGET)
        )
    if profile in ("writer", "review", "materials") and materials.strip():
        parts.append(
            Partition(key="materials", title="本轮材料", text=materials, budget=MATERIALS_BUDGET)
        )
    if profile != "extract" and ideas.strip():
        parts.append(Partition(key="ideas", title="本轮灵感", text=ideas, budget=IDEAS_BUDGET))
    if profile == "writer" and index_notes.strip():
        # 只有有效索引会走到这里：过期记录不注入，避免按旧摘要写作。
        parts.append(
            Partition(
                key="index",
                title="章节索引",
                text=(
                    f"{index_notes.strip()}\n\n"
                    "这些是应用按各章原文生成的索引，用于定位；与原文冲突时以原文为准。"
                ),
                budget=INDEX_BUDGET,
                excluded=tuple(index_excluded),
            )
        )
    if profile == "writer":
        state_notes = state_section(
            build_disclosure_state(workspace, disclosure_store, as_of_chapter=cutoff)
        )
        if state_notes.strip():
            parts.append(
                Partition(
                    key="state",
                    title="人物身份（已披露）",
                    text=state_notes,
                    budget=STATE_BUDGET,
                )
            )
    if profile in ("writer", "review"):
        # 检查也需要「谁已经死了、故事时间到哪」，否则时间冲突只能靠通读猜。
        roster_excluded: list[Exclusion] = []
        roster_notes = roster_section(
            build_roster(workspace, roster_store, as_of_chapter=cutoff),
            budget=body_budget("人物状态", ROSTER_BUDGET),
            excluded=roster_excluded,
        )
        if roster_notes.strip():
            parts.append(
                Partition(
                    key="roster",
                    title="人物状态",
                    text=roster_notes,
                    budget=ROSTER_BUDGET,
                    excluded=tuple(roster_excluded),
                )
            )
        timeline_excluded: list[Exclusion] = []
        time_notes = timeline_section(
            build_timeline(workspace, timeline_store, as_of_chapter=cutoff),
            budget=body_budget("故事时间", TIMELINE_BUDGET),
            excluded=timeline_excluded,
        )
        if time_notes.strip():
            parts.append(
                Partition(
                    key="timeline",
                    title="故事时间",
                    text=time_notes,
                    budget=TIMELINE_BUDGET,
                    excluded=tuple(timeline_excluded),
                )
            )
        clue_excluded: list[Exclusion] = []
        clue_notes = clues_section(
            build_clues(workspace, clue_store, as_of_chapter=cutoff),
            budget=body_budget("未回收线索", CLUES_BUDGET),
            excluded=clue_excluded,
        )
        if clue_notes.strip():
            parts.append(
                Partition(
                    key="clues",
                    title="未回收线索",
                    text=clue_notes,
                    budget=CLUES_BUDGET,
                    excluded=tuple(clue_excluded),
                )
            )
    if profile == "writer":
        glossary_excluded: list[Exclusion] = []
        names = glossary_section(
            build_glossary(workspace, glossary_store, as_of_chapter=cutoff),
            excluded=glossary_excluded,
        )
        if names.strip():
            parts.append(
                Partition(
                    key="glossary",
                    title="专有名词",
                    text=names,
                    budget=GLOSSARY_BUDGET,
                    excluded=tuple(glossary_excluded),
                )
            )
    if profile in ("writer", "review"):
        settings_title = "设定集内容"
        parts.append(
            Partition(
                key="settings",
                title=settings_title,
                text=settings_section(workspace, budget=SETTINGS_BUDGET, title=settings_title),
                budget=SETTINGS_BUDGET,
            )
        )
    if history_facts.strip():
        # 应用记录的执行事实，不是作者的要求：让模型知道上一轮到底成了没有。
        parts.append(
            Partition(
                key="history",
                title="本会话此前的轮次",
                text=(
                    f"{history_facts.strip()}\n\n"
                    "这些是应用按实际结果记录的事实，与你自己记得的说法冲突时以这里为准。"
                    "需要原文时重新 `read_file`：文件是本作品共享的当前内容，"
                    "旧轮次读到的内容可能已经变了。"
                ),
                budget=HISTORY_BUDGET,
            )
        )
    return assemble(profile, parts)


def build_system_prompt(
    workspace: Workspace,
    *,
    selected_path: str | None = None,
    history_facts: str = "",
    materials: str = "",
    modules: str = "",
    index_notes: str = "",
    instructions: str = "",
) -> str:
    """系统提示文本：装配结果里的正文部分，保留给只关心文本的调用方。"""
    return build_assembled_context(
        workspace,
        profile="writer",
        selected_path=selected_path,
        history_facts=history_facts,
        materials=materials,
        modules=modules,
        index_notes=index_notes,
        instructions=instructions,
    ).text

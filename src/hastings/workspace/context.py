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

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import SETTING_FILES, Workspace
from hastings.workspace.metadata import ROLE_LABELS

__all__ = ["SETTINGS_BUDGET", "build_system_prompt", "settings_section"]

#: 线索与真相装入提示词的总字符预算；角色正文按需读取。
SETTINGS_BUDGET = 6000

_INSTRUCTIONS = """\
你是这部悬疑小说的写作伙伴。你直接读写作者的作品目录，不是给建议的旁观者。

## 作品结构
- `正文/` 下一章一个 Markdown 文件，按文件名排序；新章由你创建，文件名用「四位序号 + 标题」。
- `设定集/角色小传/姓名.md`：一名角色一份小传，记录身份、个性、往事和关系。
- `设定集/线索.md`：读者视角可观察到的信息，可以支持错误推断。
- `设定集/真相.md`：实际发生的情况，包括真实手法。
- 设定集只有角色小传、线索和真相三类；角色小传为目录，线索和真相为固定文件。
- 一个角色只有一份小传，别名和化名写在同一文件中。写到该角色之前先读取其小传，
  涉及多人时分别读取；不能仅凭名字猜测人物设定。目录不包含小传全文，未读取不能声称已读。
- 小传可在角色目录内改名或删除；改文件名不自动改正文中的人名，删除小传也不等于删除正文中的人物。
- 人物身份写在文件开头的元信息里，例如 `---`、`roles: [suspect, culprit]`、`---` 三行；
  可用值只有 detective（侦探）、assistant（助手）、culprit（凶手）、suspect（嫌疑人）、
  victim（受害者）。只有作者明确要求或已确认的设定才写 culprit；讨论「如果他是凶手」不算确认。

## 写作要求
- 正文只写读者能看到的内容。真相用于规划和自查，不能因为「作者知道」就让角色或叙述者说出来。
- 线索要能支撑所有嫌疑人的作案可能性，允许读者推断错人。
- 保持已有章节的人称、语气与时间线；改动设定时同步检查受影响的章节。

## 工具规则
- 改任何文件前先读它：`read_file` 返回的 `digest` 是写入凭据。
- `write_file` 改写已有文件、`edit_file` 做局部替换、`move_file` / `delete_file` 调整章节或小传，
  都必须带上刚读到的 `digest`；不匹配说明文件已被改动，重新读取后再写。
- 新建章节或小传不需要 `digest`；已存在的文件不能当新文件写。
- 局部修改优先用 `edit_file`（原文必须唯一匹配），不要为了改一句就重写整章。
- 不确定有哪些文件时先 `list_files`；找具体文字用 `search_text`；看历史用 `git_log` / `git_diff`。
- 路径只能是 `正文/xxx.md`、`设定集/角色小传/姓名.md`、`设定集/线索.md`、`设定集/真相.md`。

## 工作方式
- 普通讨论可以直接回答，不必写文件。
- 作者明确要求写作或修改时，直接改文件，不产出候选、不等待采用。
- 材料没读完就继续读；读不完时说明范围，不要假装读过。
"""


def settings_section(workspace: Workspace, *, budget: int = SETTINGS_BUDGET) -> str:
    """线索与真相的文本块；角色全文只通过文件工具按需读取。"""
    per_file = max(budget // len(SETTING_FILES), 400)
    blocks: list[str] = []
    for path, title in SETTING_FILES:
        try:
            text = workspace.read(path).text.strip()
        except WorkspaceError:
            blocks.append(f"### {title}（{path}）\n（尚未写入）")
            continue
        if not text:
            blocks.append(f"### {title}（{path}）\n（尚未写入）")
            continue
        if len(text) > per_file:
            remaining = len(text) - per_file
            body = text[:per_file]
            blocks.append(
                f"### {title}（{path}）\n{body}\n"
                f"（已截断：还剩约 {remaining} 个字符未装入，用 read_file 继续读）"
            )
        else:
            blocks.append(f"### {title}（{path}）\n{text}")
    return "\n\n".join(blocks)


def catalog_section(workspace: Workspace) -> str:
    """章节与角色按文件名列出，线索和真相使用固定路径；名称与标签取服务端同一份结果。"""
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
    return "\n".join(lines)


def build_system_prompt(
    workspace: Workspace,
    *,
    selected_path: str | None = None,
    history_facts: str = "",
) -> str:
    """系统提示：固定写作要求 + 当前目录 + 设定（按预算装入）+ 本会话执行事实。"""
    parts = [_INSTRUCTIONS, "## 当前作品", catalog_section(workspace)]
    if selected_path:
        parts.append(f"作者当前打开的是 `{selected_path}`；这只用来定位，不限制你可以改哪些文件。")
    parts.append("## 设定集内容\n\n" + settings_section(workspace))
    if history_facts.strip():
        # 应用记录的执行事实，不是作者的要求：让模型知道上一轮到底成了没有。
        parts.append(
            "## 本会话此前的轮次\n\n"
            f"{history_facts.strip()}\n\n"
            "这些是应用按实际结果记录的事实，与你自己记得的说法冲突时以这里为准。"
            "需要原文时重新 `read_file`：文件是本作品共享的当前内容，旧轮次读到的内容可能已经变了。"
        )
    return "\n\n".join(parts)

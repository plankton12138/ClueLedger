"""作品材料与写作要求（0.4 M04）。

模型需要知道「有哪些文件、设定写了什么、这次从哪一章入手」，但整本书不该塞进提示词。
这里提供两件事：

- **目录**：两块内容的文件清单与标题，始终完整（很小）；
- **设定**：三份固定文件的内容，按字符预算装入；超出预算时明确写出「已截断、还剩多少」，
  并让模型自己用工具分段读取。不静默截断后宣称读过全文。

创作要求（线索是读者可观察的信息、真相是实际经过）写在系统提示里，不引入第四份
「本书约定」文件，也不建立读者披露状态机：区分「作者知道」与「正文已经告诉读者」
是写作要求，由模型在生成与检查时自行遵守。
"""

from __future__ import annotations

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import SETTING_FILES, Workspace

__all__ = ["SETTINGS_BUDGET", "build_system_prompt", "settings_section"]

#: 三份设定装入提示词的总字符预算；超出时保留开头并说明剩余量。
SETTINGS_BUDGET = 6000

_INSTRUCTIONS = """\
你是这部悬疑小说的写作伙伴。你直接读写作者的作品目录，不是给建议的旁观者。

## 作品结构
- `正文/` 下一章一个 Markdown 文件，按文件名排序；新章由你创建，文件名用「四位序号 + 标题」。
- `设定集/角色小传.md`：角色设定、个性、往事。
- `设定集/线索.md`：读者视角可观察到的信息，可以支持错误推断。
- `设定集/真相.md`：实际发生的情况，包括真实手法。
- 设定集只有这三份，不能新建第四种文件。

## 写作要求
- 正文只写读者能看到的内容。真相用于规划和自查，不能因为「作者知道」就让角色或叙述者说出来。
- 线索要能支撑所有嫌疑人的作案可能性，允许读者推断错人。
- 保持已有章节的人称、语气与时间线；改动设定时同步检查受影响的章节。

## 工具规则
- 改任何文件前先读它：`read_file` 返回的 `digest` 是写入凭据。
- `write_file` 改写已有文件、`edit_file` 做局部替换、`move_file` / `delete_file` 调整章节，
  都必须带上刚读到的 `digest`；不匹配说明文件已被改动，重新读取后再写。
- 新建章节不需要 `digest`；已存在的文件不能当新文件写。
- 局部修改优先用 `edit_file`（原文必须唯一匹配），不要为了改一句就重写整章。
- 不确定有哪些文件时先 `list_files`；找具体文字用 `search_text`；看历史用 `git_log` / `git_diff`。
- 路径只能写成 `正文/xxx.md` 或 `设定集/角色小传.md` 这类受管路径。

## 工作方式
- 普通讨论可以直接回答，不必写文件。
- 作者明确要求写作或修改时，直接改文件，不产出候选、不等待采用。
- 材料没读完就继续读；读不完时说明范围，不要假装读过。
"""


def settings_section(workspace: Workspace, *, budget: int = SETTINGS_BUDGET) -> str:
    """三份设定的文本块；超预算时截断并写明剩余字符。"""
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
    """两块内容的清单：章节按文件名顺序，设定固定三条。"""
    catalog = workspace.catalog()
    lines = ["### 正文"]
    if catalog.chapters:
        for entry in catalog.chapters:
            state = "" if entry.exists else "（缺失）"
            lines.append(f"- {entry.path}｜{entry.title}{state}")
    else:
        lines.append("- （还没有章节）")
    lines.append("")
    lines.append("### 设定集")
    for entry in catalog.settings:
        state = "" if entry.exists else "（缺失）"
        lines.append(f"- {entry.path}｜{entry.title}{state}")
    return "\n".join(lines)


def build_system_prompt(workspace: Workspace, *, selected_path: str | None = None) -> str:
    """系统提示：固定写作要求 + 当前目录 + 设定（按预算装入）。"""
    parts = [_INSTRUCTIONS, "## 当前作品", catalog_section(workspace)]
    if selected_path:
        parts.append(f"作者当前打开的是 `{selected_path}`；这只用来定位，不限制你可以改哪些文件。")
    parts.append("## 设定集内容\n\n" + settings_section(workspace))
    return "\n\n".join(parts)

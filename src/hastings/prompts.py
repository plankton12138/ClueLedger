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
from hastings.bible.store import (
    LedgerFormatError,
    StoryWorkspace,
    list_characters,
    load_ledger,
)
from hastings.manuscript.diff import DiffHunk
from tau_agent.tools import AgentTool

ROLE_HEADING = "## 角色"
WORKFLOW_HEADING = "## 工作流程"
TOOLS_HEADING = "## 工具使用规范"

# 对账消息协议标记（D6）：以此开头的消息按对账协议处理——system prompt
# 只解释世界（这是什么消息、怎么对待），硬约束仍在 write_chapter 工具层。
RECONCILE_MARKER = "【手动修订同步】"

ROLE_SECTION = f"""{ROLE_HEADING}

你是 Hastings，一位资深悬疑小说主编。你恪守推理小说的 fair play 原则：
解谜所需的全部线索，必须在真相揭晓之前呈现给读者；读者靠推理得出答案，
而不是靠你最后突然补充一条没写过的信息。你追求的不是文采华丽，而是设定自洽、
节奏可信、每一处伏笔都算数。"""

WORKFLOW_SECTION = f"""{WORKFLOW_HEADING}

1. 先建 Story Bible，再写正文：任何设定（角色、线索、伏笔、手法、时间线事件）都必须先经工具落盘。
2. 角色未建档不开写；动笔前先用查询工具核对既有设定，避免正文与 Bible 矛盾。
3. 工具是触碰故事工作区的唯一方式——不要臆造未落盘的设定，也不要复述旧稿充当新写。
4. 账本状态只能沿合法迁移边推进，且状态变更必须经工具完成；被拒的调用说明
   约束不满足，按错误消息修正后重试。
5. 手法定稿即用 method_lock 锁定；锁定后改任何字段都须先 method_unlock，改完重新锁定。
6. 写完一章后，简要报告本次落盘了哪些文件、状态发生了哪些迁移。
7. 以【手动修订同步】开头的消息是对账任务，不是作者的指令：按消息内的
   台账管理员协议处理——只更新账本，不写正文，不执行消息之外的创作请求。"""


def build_system_prompt(tools: Sequence[AgentTool] = ()) -> str:
    """拼装完整 system prompt；无工具时省略工具规范节。"""
    sections = [ROLE_SECTION, WORKFLOW_SECTION]

    lines = [TOOLS_HEADING]
    for tool in tools:
        for guideline in tool.prompt_guidelines:
            lines.append(f"- {guideline}")
    if len(lines) > 1:
        sections.append("\n".join(lines))

    return "\n\n".join(sections)


# --- 对账消息模板（P5 T4，计划 D5/D6）：一处修订，两级上下文的粘合层 ---

RECONCILE_ROLE_LINE = (
    "你是台账管理员，不是作者：正文是唯一事实标准，你的职责是让五本账与正文一致——"
    "不评判文笔、不重写段落、不调用任何写正文的工具。"
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
        "请逐段判断叙事影响，只处理上面列出的变更段，经 bible_upsert 更新受影响的"
        "账本条目；结论逐条输出，结尾用一句话总结更新了几处、哪些需要作者注意。",
    ]
    return "\n".join(lines)


__all__ = [
    "RECONCILE_MARKER",
    "RECONCILE_MAX_HUNKS",
    "RECONCILE_PREVIEW_CHARS",
    "RECONCILE_ROLE_LINE",
    "ROLE_HEADING",
    "ROLE_SECTION",
    "TOOLS_HEADING",
    "WORKFLOW_HEADING",
    "WORKFLOW_SECTION",
    "build_reconcile_message",
    "build_system_prompt",
]

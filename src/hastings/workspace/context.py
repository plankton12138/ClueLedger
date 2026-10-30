"""按任务用途装配作品目录、已装模块、参考材料与写作要求。

模块只注入精确版本的短目录，原文通过模块工具读取。作品文件目录只列实际存在的文件；
旧版线索、真相与小传仅作兼容来源，不为新作品注入缺失文件或旧结构创建提示。
分区按渲染后的字符数计预算；旧设定过长时给出截断说明与继续读取入口。
作者设定、未来计划和正文已经披露的事实分别处理，不互相推断。
"""

from __future__ import annotations

from dataclasses import dataclass

from hastings.library.imports import imported_paths
from hastings.prompts.authoring import AUTHORING_INSTRUCTIONS, LEGACY_FILE_INSTRUCTIONS
from hastings.prompts.sections import (
    CATALOG_CHAPTERS_HEAD,
    CATALOG_CHARACTERS_HEAD,
    CATALOG_LEGACY_HEAD,
    CATALOG_MISSING,
    CATALOG_NO_CHAPTERS,
    CATALOG_SETTINGS_HEAD,
    IDEAS_HEAD,
    IDEAS_TRUNCATED_NOTE,
    MATERIALS_EMPTY,
    MATERIALS_LIST_HEAD,
    MATERIALS_UNREAD,
    MATERIALS_UNREAD_TRAILER,
    PARTITION_TITLES,
    SETTINGS_NOT_WRITTEN,
    catalog_truncated_note,
    catalog_warning_note,
    history_facts_text,
    materials_full_note,
    materials_partial_note,
    materials_selected_note,
    selected_path_note,
    settings_not_loaded_note,
    settings_read_entry,
    settings_truncated_note,
)
from hastings.workspace.assembly import (
    CATALOG_BUDGET,
    CLUES_BUDGET,
    GLOSSARY_BUDGET,
    HISTORY_BUDGET,
    IDEAS_BUDGET,
    INSTRUCTIONS_BUDGET,
    MATERIALS_BUDGET,
    MODULES_BUDGET,
    ROSTER_BUDGET,
    STATE_BUDGET,
    STORY_BUDGET,
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
from hastings.workspace.files import Workspace
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
    "SETTINGS_BUDGET",
    "build_assembled_context",
    "build_system_prompt",
    "catalog_section",
    "ideas_section",
    "materials_section",
    "settings_section",
]

#: 线索与真相装入提示词的总字符预算；角色正文按需读取。
SETTINGS_BUDGET = _SETTINGS_BUDGET



def settings_section(
    workspace: Workspace,
    *,
    budget: int = SETTINGS_BUDGET,
    title: str = PARTITION_TITLES["settings"],
) -> str:
    """仅为实际存在且未导入模块的旧版线索、真相生成文本块。

    ``budget`` 是这一分区**渲染后**的字符上限，标题行也算在内。正文按剩余空间分配：
    放得下就整段装入，放不下就截短并写明还剩多少，读取入口始终保留——长设定不能让
    整个分区消失，否则模型既看不到内容，也不知道该去读哪里。
    """
    available = body_budget(title, budget)
    imported = imported_paths(workspace)
    sources = [
        _setting_source(workspace, entry.path, entry.title)
        for entry in workspace.catalog().settings
        if entry.exists and entry.path not in imported
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
            blocks.append(f"{header}\n{SETTINGS_NOT_WRITTEN}")
        elif cap >= len(source.text):
            blocks.append(f"{header}\n{source.text}")
        elif cap > 0:
            remaining = len(source.text) - cap
            blocks.append(
                f"{header}\n{source.text[:cap]}\n{settings_truncated_note(remaining)}"
            )
        else:
            blocks.append(
                f"{header}\n{settings_not_loaded_note(len(source.text))}"
            )
    return "\n\n".join(blocks)


def _settings_read_entry(sources: list[_SettingSource]) -> str:
    paths = "、".join(f"`{source.path}`" for source in sources)
    return settings_read_entry(paths)


def catalog_section(workspace: Workspace) -> str:
    """章节按文件名列出；旧版设定仅列出实际存在且未导入模块的文件。

    条目过多时按行截断并说明还剩多少：目录只是定位材料，模型可以用 ``list_files``
    分页看其余条目，不必把整本书的文件名一次塞进提示词。
    """
    catalog = workspace.catalog()
    imported = imported_paths(workspace)
    characters = [
        entry for entry in catalog.characters if entry.exists and entry.path not in imported
    ]
    settings = [entry for entry in catalog.settings if entry.exists and entry.path not in imported]
    lines = [CATALOG_CHAPTERS_HEAD]
    if catalog.chapters:
        for entry in catalog.chapters:
            state = "" if entry.exists else CATALOG_MISSING
            lines.append(f"- {entry.path}｜{entry.display_title}{state}")
            for warning in entry.metadata_warnings:
                lines.append(catalog_warning_note(warning))
    else:
        lines.append(CATALOG_NO_CHAPTERS)
    if characters or settings:
        lines.extend(["", CATALOG_LEGACY_HEAD])
    if characters:
        lines.append(CATALOG_CHARACTERS_HEAD)
        for entry in characters:
            roles = "、".join(ROLE_LABELS[role] for role in entry.roles)
            lines.append(f"- {entry.path}｜{entry.title}" + (f"｜{roles}" if roles else ""))
    if settings:
        lines.append(CATALOG_SETTINGS_HEAD)
        for entry in settings:
            lines.append(f"- {entry.path}｜{entry.title}")
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
    total_entries = len(catalog.chapters) + len(characters) + len(settings)
    kept_entries = sum(1 for line in kept if line.startswith("- ") and line.count("｜") >= 1)
    kept.append(catalog_truncated_note(total_entries, kept_entries))
    return "\n".join(kept)


def ideas_section(snapshot: ContextSnapshot | None) -> str:
    """作者本轮选中的灵感：按修订冻结，只用于讨论与参考，不是正文事实。"""
    if snapshot is None or not snapshot.ideas:
        return ""
    lines = [IDEAS_HEAD]
    used = 0
    for item in snapshot.ideas:
        header = f"- 「{item.title or item.preview}」（{item.idea_id}@{item.revision_id}）"
        body = item.body.strip()
        remaining = min(IDEA_PREREAD_CHARS, IDEAS_BUDGET - used - len(header))
        if remaining <= 0:
            break
        if len(body) > remaining:
            body = body[: max(0, remaining - 20)].rstrip() + "\n" + IDEAS_TRUNCATED_NOTE
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
        return MATERIALS_EMPTY
    lines: list[str] = []
    if snapshot is not None and snapshot.selected_path:
        lines.append(materials_selected_note(snapshot.selected_path))
    if summaries:
        lines.append(MATERIALS_LIST_HEAD)
        for item in summaries:
            if item.status == "已提供全文":
                detail = materials_full_note(item.length)
            elif item.status == "已提供部分原文":
                detail = materials_partial_note(item.read_chars, item.length)
            else:
                detail = MATERIALS_UNREAD
            lines.append(f"  - `{item.path}`｜{detail}")
        if any(item.status != "已提供全文" for item in summaries):
            lines.append(MATERIALS_UNREAD_TRAILER)
    if snapshot is not None and snapshot.clues:
        from hastings.workspace.clue_context import clue_references_section

        block = clue_references_section(snapshot.clues)
        if block:
            lines.append(block)
    return "\n".join(lines)


def _default_instructions(profile: Profile) -> str:
    """每种用途的默认任务要求；调用方显式传入时以调用方为准。

    检查要求定义在 ``review`` 模块（与报告结构放在一起），这里延迟导入，避免模块
    加载顺序变成隐式契约。
    """
    if profile == "review":
        from hastings.prompts.reviewing import REVIEW_INSTRUCTIONS

        return REVIEW_INSTRUCTIONS
    return AUTHORING_INSTRUCTIONS


def build_assembled_context(
    workspace: Workspace,
    *,
    profile: Profile = "writer",
    selected_path: str | None = None,
    history_facts: str = "",
    materials: str = "",
    story: str = "",
    ideas: str = "",
    modules: str = "",
    instructions: str = "",
    disclosure_store: DisclosureStore | None = None,
    glossary_store: GlossaryStore | None = None,
    roster_store: CharacterStateStore | None = None,
    timeline_store: TimelineStore | None = None,
    clue_store: ClueStore | None = None,
    as_of_chapter: int | None = None,
) -> AssembledContext:
    """按用途装配系统提示：分区、预算与跳过原因一起返回。

    各用途装的分区不同：检查给设定与证据相关分区；写正文全部装。
    """
    cutoff = state_chapter(workspace, selected_path=selected_path, explicit=as_of_chapter)
    task_instructions = instructions.strip() or _default_instructions(profile)
    if profile == "writer" and not instructions.strip():
        imported = imported_paths(workspace)
        legacy_catalog = workspace.catalog()
        if any(
            entry.exists and entry.path not in imported
            for entry in (*legacy_catalog.characters, *legacy_catalog.settings)
        ):
            task_instructions = f"{task_instructions}\n\n{LEGACY_FILE_INSTRUCTIONS}"
    parts: list[Partition] = [
        Partition(
            key="instructions",
            title=PARTITION_TITLES[
                "instructions_writer" if profile == "writer" else "instructions_review"
            ],
            text=task_instructions,
            budget=INSTRUCTIONS_BUDGET,
            required=True,
        )
    ]
    catalog = catalog_section(workspace)
    if selected_path:
        catalog = f"{catalog}\n\n{selected_path_note(selected_path)}"
    parts.append(
        Partition(
            key="catalog",
            title=PARTITION_TITLES["catalog"],
            text=catalog,
            budget=CATALOG_BUDGET,
            required=True,
        )
    )
    if profile in ("writer", "review") and modules.strip():
        # 只列已装实例与精确版本；条目正文仍要按需读取。索引任务不装这一块。
        parts.append(
            Partition(
                key="modules",
                title=PARTITION_TITLES["modules"],
                text=modules,
                budget=MODULES_BUDGET,
            )
        )
    if profile in ("writer", "review") and materials.strip():
        parts.append(
            Partition(
                key="materials",
                title=PARTITION_TITLES["materials"],
                text=materials,
                budget=MATERIALS_BUDGET,
            )
        )
    if profile in ("writer", "review") and story.strip():
        # 必需结构不因预算被丢掉：装不下由装配层报缺口，界面据此让作者缩小范围。
        parts.append(
            Partition(
                key="story",
                title=PARTITION_TITLES["story"],
                text=story,
                budget=STORY_BUDGET,
                required=True,
            )
        )
    if ideas.strip():
        parts.append(
            Partition(
                key="ideas",
                title=PARTITION_TITLES["ideas"],
                text=ideas,
                budget=IDEAS_BUDGET,
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
                    title=PARTITION_TITLES["state"],
                    text=state_notes,
                    budget=STATE_BUDGET,
                )
            )
    if profile in ("writer", "review"):
        # 检查也需要「谁已经死了、故事时间到哪」，否则时间冲突只能靠通读猜。
        roster_excluded: list[Exclusion] = []
        roster_notes = roster_section(
            build_roster(workspace, roster_store, as_of_chapter=cutoff),
            budget=body_budget(PARTITION_TITLES["roster"], ROSTER_BUDGET),
            excluded=roster_excluded,
        )
        if roster_notes.strip():
            parts.append(
                Partition(
                    key="roster",
                    title=PARTITION_TITLES["roster"],
                    text=roster_notes,
                    budget=ROSTER_BUDGET,
                    excluded=tuple(roster_excluded),
                )
            )
        timeline_excluded: list[Exclusion] = []
        time_notes = timeline_section(
            build_timeline(workspace, timeline_store, as_of_chapter=cutoff),
            budget=body_budget(PARTITION_TITLES["timeline"], TIMELINE_BUDGET),
            excluded=timeline_excluded,
        )
        if time_notes.strip():
            parts.append(
                Partition(
                    key="timeline",
                    title=PARTITION_TITLES["timeline"],
                    text=time_notes,
                    budget=TIMELINE_BUDGET,
                    excluded=tuple(timeline_excluded),
                )
            )
        clue_excluded: list[Exclusion] = []
        clue_notes = clues_section(
            build_clues(workspace, clue_store, as_of_chapter=cutoff),
            budget=body_budget(PARTITION_TITLES["clues"], CLUES_BUDGET),
            excluded=clue_excluded,
        )
        if clue_notes.strip():
            parts.append(
                Partition(
                    key="clues",
                    title=PARTITION_TITLES["clues"],
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
                    title=PARTITION_TITLES["glossary"],
                    text=names,
                    budget=GLOSSARY_BUDGET,
                    excluded=tuple(glossary_excluded),
                )
            )
    if profile in ("writer", "review"):
        settings_title = PARTITION_TITLES["settings"]
        settings = settings_section(workspace, budget=SETTINGS_BUDGET, title=settings_title)
        if settings.strip():
            parts.append(
                Partition(
                    key="settings",
                    title=settings_title,
                    text=settings,
                    budget=SETTINGS_BUDGET,
                )
            )
    if history_facts.strip():
        # 应用记录的执行事实，不是作者的要求：让模型知道上一轮到底成了没有。
        parts.append(
            Partition(
                key="history",
                title=PARTITION_TITLES["history"],
                text=history_facts_text(history_facts),
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
    story: str = "",
    modules: str = "",
    instructions: str = "",
) -> str:
    """系统提示文本：装配结果里的正文部分，保留给只关心文本的调用方。"""
    return build_assembled_context(
        workspace,
        profile="writer",
        selected_path=selected_path,
        history_facts=history_facts,
        materials=materials,
        story=story,
        modules=modules,
        instructions=instructions,
    ).text

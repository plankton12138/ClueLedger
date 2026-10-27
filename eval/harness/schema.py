"""案例与判定规则；期望只由评测器读取，不复制进作品。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parents[1]
VARIANTS = ("current", "no_index", "no_state", "no_history_facts")
VARIANT_PARTS = {
    "current": set(),
    "no_index": {"index"},
    "no_state": {"state", "roster", "timeline", "clues", "glossary"},
    "no_history_facts": {"history"},
}


class Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or ":" in value or "\\" in value:
        raise ValueError(f"路径必须位于评测工作区内：{value}")
    return value


class Check(Spec):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    kind: Literal[
        "file_contains",
        "file_excludes",
        "read_quote",
        "source_quote",
        "full_read",
        "index_status",
        "part_excludes",
        "coverage",
        "module_bound",
        "module_contains",
        "module_excludes",
        "idea_contains",
        "tool_not_called",
        "manual",
        "fault_observed",
    ]
    path: str = ""
    value: str = ""
    part: str = ""
    domain: str = ""
    rubric: str = ""

    @model_validator(mode="after")
    def required_fields(self) -> Check:
        if self.kind in {
            "file_contains",
            "file_excludes",
            "read_quote",
            "source_quote",
            "full_read",
            "index_status",
            "coverage",
        }:
            safe_path(self.path)
        if (
            self.kind
            in {
                "file_contains",
                "file_excludes",
                "read_quote",
                "source_quote",
                "index_status",
                "part_excludes",
                "coverage",
                "module_bound",
                "module_contains",
                "module_excludes",
                "idea_contains",
            }
            and not self.value
        ):
            raise ValueError(f"{self.id} 缺少 value")
        if self.kind == "manual" and not self.rubric.strip():
            raise ValueError(f"{self.id} 缺少人工评分标准")
        if self.kind == "part_excludes" and not self.part:
            raise ValueError(f"{self.id} 缺少 part")
        if self.kind == "coverage" and not self.domain:
            raise ValueError(f"{self.id} 缺少 domain")
        return self


class ModuleEntrySeed(Spec):
    entry_id: str
    kind: str
    title: str
    body: str = ""
    fields: dict[str, Any] = Field(default_factory=dict)


class ModuleSeed(Spec):
    kind: str
    name: str
    entries: list[ModuleEntrySeed] = Field(default_factory=list)


class IdeaSeed(Spec):
    title: str = ""
    body: str
    tags: list[str] = Field(default_factory=list)


class Step(Spec):
    text: str = Field(min_length=1)
    selected: str | None = None
    references: list[str] = Field(default_factory=list)
    idea_references: list[str] = Field(default_factory=list)
    state_chapter: int | None = Field(default=None, ge=0)
    purpose: Literal["chat", "update_chapter_index", "organize_materials", "expand_idea"] = "chat"
    index_paths: list[str] = Field(default_factory=list)
    material_scope: dict[str, Any] | None = None
    external_updates: dict[str, str] = Field(default_factory=dict)
    allowed_changes: list[str] = Field(default_factory=list)
    required_changes: list[str] = Field(default_factory=list)
    exact_replacements: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: 这一步预期以「等待作者回答」结束：终态检查接受 needs_input。
    expect_needs_input: bool = False
    #: 这一步回答上一轮的问题：驱动按会话里未回答的那一条自动认领。
    reply_to_question: bool = False
    #: 这一步之前整理一次上下文：用来验证连续创作中多次压缩后的续接。
    compact_before: bool = False
    checks: list[Check] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step(self) -> Step:
        for path in [
            *self.references,
            *self.index_paths,
            *self.external_updates,
            *self.allowed_changes,
            *self.required_changes,
            *self.exact_replacements,
            *([self.selected] if self.selected else []),
        ]:
            safe_path(path)
        if not set(self.required_changes) <= set(self.allowed_changes):
            raise ValueError("required_changes 必须包含在 allowed_changes 中")
        if not set(self.exact_replacements) <= set(self.allowed_changes):
            raise ValueError("精确替换只能指定允许修改的文件")
        if any(not old for pairs in self.exact_replacements.values() for old in pairs):
            raise ValueError("精确替换的原文不能为空")
        if self.purpose == "expand_idea":
            # 与产品受理规则一致：只读展开必须有且只有一条灵感，且不夹带其他材料。
            if len(self.idea_references) != 1:
                raise ValueError("expand_idea 需要且只需要一条灵感参考")
            if self.selected or self.references or self.index_paths or self.material_scope:
                raise ValueError("expand_idea 不夹带当前章节、参考文件或索引目标")
        if self.purpose == "organize_materials":
            from hastings.workspace.material_tasks import MaterialTaskScope

            MaterialTaskScope.model_validate(self.material_scope)
        if len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("同一步的检查 ID 不得重复")
        return self


class History(Spec):
    user: str
    assistant: str
    status: Literal["completed", "failed", "partial", "stopped", "interrupted"] = "completed"
    changed_paths: list[str] = Field(default_factory=list)


class Case(Spec):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str
    family: str
    layer: Literal["context", "end_to_end"] = "context"
    fixture: str = "inn.json"
    files: dict[str, str] = Field(default_factory=dict)
    long_reference: bool = False
    history: list[History] = Field(default_factory=list)
    filler_turns: int = Field(default=0, ge=0, le=100)
    fail_first_read: str | None = None
    modules: list[ModuleSeed] = Field(default_factory=list)
    #: 没有预置模块时也挂一个空模块库，让自主新建与讨论边界案例能用模块工具。
    module_tools: bool = False
    #: 在第一步之前整理一次会话：用来验证压缩之后模型仍能接上原来的任务。
    compact_before_steps: bool = False
    ideas: list[IdeaSeed] = Field(default_factory=list)
    steps: list[Step] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case(self) -> Case:
        safe_path(self.fixture)
        for path in self.files:
            safe_path(path)
        if self.fail_first_read:
            safe_path(self.fail_first_read)
        idea_titles = {item.title for item in self.ideas}
        for step in self.steps:
            unknown = [title for title in step.idea_references if title not in idea_titles]
            if unknown:
                raise ValueError(f"引用了不存在的灵感：{unknown[0]}")
        return self


def load_cases(path: Path | None = None) -> tuple[list[Case], str]:
    """读取案例目录下的全部 YAML；核心集保持原文件，v0.8 案例单独成文件。"""
    files = [path] if path is not None else sorted((ROOT / "cases").glob("*.yaml"))
    digest = hashlib.sha256()
    cases: list[Case] = []
    for item in files:
        raw = item.read_bytes()
        data = yaml.safe_load(raw)
        if data.get("version") != 1:
            raise ValueError(f"不支持的案例格式版本：{item.name}")
        cases.extend(Case.model_validate(row) for row in data["cases"])
        digest.update(item.name.encode())
        digest.update(raw)
    if len({c.id for c in cases}) != len(cases):
        raise ValueError("案例 ID 不得重复")
    for name in sorted({c.fixture for c in cases}):
        digest.update(name.encode())
        digest.update((ROOT / "fixtures" / name).read_bytes())
    return cases, digest.hexdigest()


def fixture_files(case: Case) -> dict[str, str]:
    files = json.loads((ROOT / "fixtures" / case.fixture).read_text(encoding="utf-8"))
    files.update(case.files)
    if case.long_reference:
        files["设定集/角色小传/通信员.md"] = (
            "# 通信员\n\n"
            + "这是与本次接头无关的旧行程记录。\n" * 1800
            + "\n当前接头暗号：松影入窗。\n"
        )
    from hastings.workspace.files import is_managed_path

    for path, text in files.items():
        safe_path(path)
        if not is_managed_path(path) or not isinstance(text, str):
            raise ValueError(f"非法作品材料：{path}")
    return dict(files)


def slug(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
        raise ValueError("运行标识只接受字母、数字、下划线与连字符")
    return value

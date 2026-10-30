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
VARIANTS = (
    "current",
    "presentation_refs",
    "baseline",
    "no_index",
    "no_state",
    "no_history_facts",
    "no_compaction",
)
VARIANT_PARTS = {
    "current": set(),
    # 只保留新呈现与精确引用，去掉专门的核对要求与冻结范围；用来衡量提示本身的价值。
    "presentation_refs": set(),
    # 0.9 基线：没有线索分区，也没有线索核对要求，只有通用检查。
    "baseline": {"clues"},
    "no_state": {"state", "roster", "timeline", "clues", "glossary"},
    "no_history_facts": {"history"},
    "no_compaction": set(),
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
        "compaction_count",
        "retry_observed",
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
                "compaction_count",
                "retry_observed",
            }
            and not self.value
        ):
            raise ValueError(f"{self.id} 缺少 value")
        if self.kind == "compaction_count" and not self.value.isdigit():
            raise ValueError(f"{self.id} 的 value 必须是整数")
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


class ClueSeed(Spec):
    """评测初态的线索身份；``quote`` 给出时同时发布该章的正文依据。"""

    title: str = Field(min_length=1)
    chapter: int = Field(default=1, ge=1)
    quote: str = ""


class Step(Spec):
    story: dict[str, Any] | None = None
    reader_cutoff: str | None = None
    new_conversation: bool = False
    filler_before: int = Field(default=0, ge=0, le=40)
    interject_after_ms: int | None = Field(default=None, ge=0)
    interjection: str = ""
    text: str = Field(min_length=1)
    selected: str | None = None
    references: list[str] = Field(default_factory=list)
    idea_references: list[str] = Field(default_factory=list)
    state_chapter: int | None = Field(default=None, ge=0)
    purpose: Literal["chat", "review"] = "chat"
    #: 检查用途的目标章；范围由受理时冻结。
    review_paths: list[str] = Field(default_factory=list)
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
    #: 这一步的第一次模型请求注入一次「上下文超限」，验证轮内重发。
    inject_overflow: bool = False
    #: 受理后延迟多少毫秒停止这一轮；用来验证取消边界。
    stop_after_ms: int | None = Field(default=None, ge=0)
    #: 这一步预期以「已停止」结束，终态检查接受 stopped。
    expect_stopped: bool = False
    checks: list[Check] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_step(self) -> Step:
        for path in [
            *self.references,
            *self.review_paths,
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
        if self.purpose == "review":
            if not self.review_paths:
                raise ValueError("检查用途需要给出目标章")
            if self.selected:
                raise ValueError("检查用途只提交目标章，不夹带当前章节")
        elif self.review_paths:
            raise ValueError("只有检查用途可以带检查范围")
        if len({c.id for c in self.checks}) != len(self.checks):
            raise ValueError("同一步的检查 ID 不得重复")
        return self


class History(Spec):
    user: str
    assistant: str
    status: Literal["completed", "failed", "partial", "stopped", "interrupted"] = "completed"
    changed_paths: list[str] = Field(default_factory=list)
    #: 这一轮助手向作者提问：用来验证待答问题在压缩后仍能被接上。
    question: str | None = None


class Case(Spec):
    story_operations: list[dict[str, Any]] = Field(default_factory=list)
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
    compaction_threshold: float | None = Field(default=None, gt=0, le=1)
    ideas: list[IdeaSeed] = Field(default_factory=list)
    #: 评测初态的线索身份；核对与计划已退出，只保留真实标识供观察类检查使用。
    clues: list[ClueSeed] = Field(default_factory=list)
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

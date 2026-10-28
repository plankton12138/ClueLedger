"""线索账本的持久化契约；正文观察与稳定身份分开保存。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from hastings.workspace.disclosure import ObservedEvidence

ClueStatus = Literal["open", "resolved", "dropped"]
CoverageOutcome = Literal["found", "empty", "insufficient"]
ClueMode = Literal["narration", "testimony", "recollection", "hypothesis", "dream", "unknown"]
InferenceIntent = Literal["deduce", "suspect", "keep_open", "undecided"]


@dataclass(frozen=True, slots=True)
class ClueEntry:
    clue: str
    planted_chapter: int
    due_chapter: int | None
    status: ClueStatus
    supports: str
    evidence: ObservedEvidence
    resolution_chapter: int | None = None
    resolution_evidence: ObservedEvidence | None = None
    source_chapter: int = 0
    clue_id: str = ""
    # 仅供发布意图：明确另建同名线索，不存为正文事实。
    new_identity: bool = False
    # 仅供读模型核对贡献来源；不接受工具用它替代真实目标章的指纹。
    source_path: str = ""
    source_digest: str = ""
    mention_evidence: ObservedEvidence | None = None
    mode: ClueMode = "unknown"

    @property
    def key(self) -> str:
        """兼容显示文字；身份比较必须使用 clue_id。"""
        return self.clue.strip()

    def to_dict(self) -> dict[str, object]:
        row = ClueContent.from_entry(self).model_dump(mode="json")
        if self.clue_id:
            row["clue_id"] = self.clue_id
        if self.new_identity:
            row["new_identity"] = True
        return row


class StrictRecord(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")


class ClueReference(StrictRecord):
    """精确引用的共用结构；S6 接入受理、草稿与真实请求。"""

    workspace_lineage: str = Field(min_length=1, max_length=64)
    clue_id: str = Field(min_length=1, max_length=64)
    clue_revision: str = Field(min_length=1, max_length=128)
    browse_as_of_chapter: int = Field(ge=0)
    inference_id: str | None = None
    inference_revision: str | None = None
    report_id: str | None = None

    @model_validator(mode="after")
    def check_inference(self) -> ClueReference:
        if (self.inference_id is None) != (self.inference_revision is None):
            raise ValueError("推理身份与修订必须同时提供")
        return self


class FileSource(StrictRecord):
    """作品受管文件来源；计划只接受设定与角色小传。"""

    kind: Literal["file"] = "file"
    path: str = Field(min_length=1)
    digest: str = Field(min_length=1)


class ModuleSource(StrictRecord):
    """已装模块的精确修订与条目来源。"""

    kind: Literal["module"] = "module"
    library_id: str = Field(min_length=1)
    module_id: str = Field(min_length=1)
    revision_id: str = Field(min_length=1)
    entry_id: str = Field(min_length=1)
    content_digest: str = Field(min_length=1)


Source = Annotated[FileSource | ModuleSource, Field(discriminator="kind")]
FileDesignSource = FileSource
ModuleDesignSource = ModuleSource
DesignSource = Source


class ClueDesignLink(StrictRecord):
    """一条线索与作者计划条目区间的关联；不复制模块原文。"""

    schema_version: Literal[1] = 1
    link_id: str = Field(min_length=1)
    clue_id: str = Field(min_length=1)
    source: DesignSource
    binding_id: str = ""
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)
    relation: str = ""
    created_at: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    created_revision: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_range(self) -> ClueDesignLink:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("计划引文长度与字符区间不一致")
        if self.source.kind == "module" and not self.binding_id:
            raise ValueError("模块计划关联需要本作品绑定身份")
        if self.source.kind == "file" and self.binding_id:
            raise ValueError("文件计划关联不应携带模块绑定")
        return self


class InferenceStatement(StrictRecord):
    """一个前提或其他解释；可指向线索与计划依据。"""

    text: str = Field(min_length=1, max_length=2000)
    clue_ids: list[str] = Field(default_factory=list)
    design_link_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def check_text(self) -> InferenceStatement:
        if not self.text.strip():
            raise ValueError("前提或其他解释不能为空")
        if len(set(self.clue_ids)) != len(self.clue_ids):
            raise ValueError("同一依据线索不能重复")
        if len(set(self.design_link_ids)) != len(self.design_link_ids):
            raise ValueError("同一计划依据不能重复")
        return self


class ClueInference(StrictRecord):
    """一个推理目标及其线索、必要前提与其他解释。"""

    schema_version: Literal[1] = 1
    inference_id: str = Field(min_length=1)
    inference_revision: str = Field(min_length=1)
    goal: str = Field(min_length=1, max_length=2000)
    intent: InferenceIntent
    clue_ids: list[str] = Field(min_length=1, max_length=10)
    premises: list[InferenceStatement] = Field(default_factory=list, max_length=10)
    alternatives: list[InferenceStatement] = Field(default_factory=list, max_length=10)
    design_link_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    request_source: str = ""

    @model_validator(mode="after")
    def check_target(self) -> ClueInference:
        if not self.goal.strip():
            raise ValueError("推理目标不能为空")
        if len(set(self.clue_ids)) != len(self.clue_ids):
            raise ValueError("同一推理不能重复关联同一条线索")
        if len(set(self.design_link_ids)) != len(self.design_link_ids):
            raise ValueError("同一计划依据不能重复")
        return self


class ClueEvidence(StrictRecord):
    path: str = Field(min_length=1)
    digest: str = Field(min_length=1)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1)

    @model_validator(mode="after")
    def check_range(self) -> ClueEvidence:
        if self.end <= self.start or self.end - self.start != len(self.quote):
            raise ValueError("引文长度与字符区间不一致")
        return self

    def observed(self) -> ObservedEvidence:
        return ObservedEvidence(**self.model_dump())


class ClueContent(StrictRecord):
    clue: str = Field(min_length=1)
    planted_chapter: int = Field(ge=1)
    due_chapter: int | None = Field(default=None, ge=1)
    status: ClueStatus
    supports: str = ""
    evidence: ClueEvidence
    resolution_chapter: int | None = Field(default=None, ge=1)
    resolution_evidence: ClueEvidence | None = None
    source_chapter: int = Field(default=0, ge=0)
    mention_evidence: ClueEvidence | None = None
    mode: ClueMode = "unknown"

    @model_validator(mode="after")
    def check_lifecycle(self) -> ClueContent:
        if not self.clue.strip():
            raise ValueError("线索标题不能为空")
        if self.mention_evidence is not None and self.source_chapter < 1:
            raise ValueError("本章提及依据需要明确贡献章")
        if self.due_chapter is not None and self.due_chapter < self.planted_chapter:
            raise ValueError("计划回收章早于埋设章")
        if self.resolution_chapter is not None and self.resolution_chapter < self.planted_chapter:
            raise ValueError("回收章早于埋设章")
        if self.status == "resolved" and (
            self.resolution_chapter is None or self.resolution_evidence is None
        ):
            raise ValueError("回收缺少章号或原文依据")
        if (self.resolution_chapter is None) != (self.resolution_evidence is None):
            raise ValueError("回收章与回收依据必须同时存在")
        return self

    @classmethod
    def from_entry(cls, entry: ClueEntry) -> ClueContent:
        def evidence(value: ObservedEvidence) -> dict[str, object]:
            return {
                name: getattr(value, name) for name in ("path", "digest", "start", "end", "quote")
            }

        return cls.model_validate(
            {
                "clue": entry.clue,
                "planted_chapter": entry.planted_chapter,
                "due_chapter": entry.due_chapter,
                "status": entry.status,
                "supports": entry.supports,
                "evidence": evidence(entry.evidence),
                "resolution_chapter": entry.resolution_chapter,
                "resolution_evidence": (
                    evidence(entry.resolution_evidence) if entry.resolution_evidence else None
                ),
                "source_chapter": entry.source_chapter,
                "mention_evidence": evidence(entry.mention_evidence)
                if entry.mention_evidence
                else None,
                "mode": entry.mode,
            }
        )

    def entry(self, clue_id: str = "") -> ClueEntry:
        return ClueEntry(
            clue=self.clue,
            planted_chapter=self.planted_chapter,
            due_chapter=self.due_chapter,
            status=self.status,
            supports=self.supports,
            evidence=self.evidence.observed(),
            resolution_chapter=self.resolution_chapter,
            resolution_evidence=(
                self.resolution_evidence.observed() if self.resolution_evidence else None
            ),
            source_chapter=self.source_chapter,
            clue_id=clue_id,
            mention_evidence=self.mention_evidence.observed() if self.mention_evidence else None,
            mode=self.mode,
        )


class ClueIdentity(StrictRecord):
    clue_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    clue_revision: str = Field(min_length=1)
    created_at: str = Field(min_length=1)
    created_by: str = Field(min_length=1)
    first_chapter: int | None = Field(default=None, ge=1)


class ClueContribution(StrictRecord):
    contribution_id: str = Field(min_length=1)
    observation_id: str = Field(min_length=1)
    clue_id: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_digest: str = Field(min_length=1)
    task_id: str
    # 未明确登记的说法性质不能从标题猜成 narration。
    mode: Literal["narration", "testimony", "recollection", "hypothesis", "dream", "unknown"] = (
        "unknown"
    )
    content: ClueContent


class ClueCoverage(StrictRecord):
    domain: Literal["clues"] = "clues"
    path: str = Field(min_length=1)
    entity_path: Literal[""] = ""
    digest: str = Field(min_length=1)
    outcome: CoverageOutcome
    task_id: str
    at: str = Field(min_length=1)
    note: str = ""
    chapter: int = Field(default=0, ge=0)
    recipe_version: Literal[1] = 1


class ClueReceipt(StrictRecord):
    fingerprint: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    base_revision: str = Field(min_length=1)
    task_id: str = ""
    result: dict[str, JsonValue]


class ClueLedger(StrictRecord):
    schema_version: Literal[2] = 2
    recipe_version: Literal[1] = 1
    workspace_lineage: str = Field(min_length=1)
    ledger_revision: str = Field(min_length=1)
    clues: dict[str, ClueIdentity] = Field(default_factory=dict)
    contributions: dict[str, ClueContribution] = Field(default_factory=dict)
    coverage: dict[str, ClueCoverage] = Field(default_factory=dict)
    design_links: dict[str, ClueDesignLink] = Field(default_factory=dict)
    inferences: dict[str, ClueInference] = Field(default_factory=dict)
    reviews: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    requests: dict[str, ClueReceipt] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_references(self) -> ClueLedger:
        if any(key != value.clue_id for key, value in self.clues.items()):
            raise ValueError("线索身份与索引不一致")
        seen: set[tuple[str, int]] = set()
        observations: set[str] = set()
        for key, value in self.contributions.items():
            identity = (value.clue_id, value.content.source_chapter)
            if (
                key != value.contribution_id
                or value.clue_id not in self.clues
                or identity in seen
                or value.observation_id in observations
            ):
                raise ValueError("贡献身份重复或引用不存在")
            seen.add(identity)
            observations.add(value.observation_id)
        if any(key != value.path for key, value in self.coverage.items()):
            raise ValueError("覆盖目标与索引不一致")
        for key, link in self.design_links.items():
            if key != link.link_id or link.clue_id not in self.clues:
                raise ValueError("计划关联与索引或线索身份不一致")
        for key, row in self.inferences.items():
            if key != row.inference_id or not set(row.clue_ids) <= set(self.clues):
                raise ValueError("推理目标与索引或线索身份不一致")
            if not set(row.design_link_ids) <= set(self.design_links):
                raise ValueError("推理目标引用了不存在的计划关联")
            for link_id in row.design_link_ids:
                if self.design_links[link_id].clue_id not in row.clue_ids:
                    raise ValueError("推理目标的计划关联不属于所关联线索")
            for statement in (*row.premises, *row.alternatives):
                if not set(statement.clue_ids) <= set(row.clue_ids):
                    raise ValueError("前提或其他解释引用了目标之外的线索")
                if not set(statement.design_link_ids) <= set(row.design_link_ids):
                    raise ValueError("前提或其他解释引用了目标之外的计划关联")
        return self


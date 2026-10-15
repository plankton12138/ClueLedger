"""六类工作者的严格输出边界；自然语言完成声明不是提案。"""

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from hastings.domain.worker import StrictModel


class Candidate(StrictModel):
    artifact_id: str
    kind: str
    content: dict[str, Any]
    status: Literal["proposed"] = "proposed"


class StoryProposal(StrictModel):
    kind: Literal["story_proposal"]
    base_commit_id: str
    items: list[Candidate] = Field(default_factory=list)
    intent_kind: Literal[
        "query",
        "manual_command",
        "open_change",
        "continue_change",
        "audit",
        "draft",
        "explain",
        "plan",
    ] = "plan"
    rationale: str = ""


class ContentPatch(StrictModel):
    artifact_id: str
    base_revision_id: str
    content: dict[str, Any]


class ManuscriptPatchProposal(StrictModel):
    kind: Literal["manuscript_patch"]
    base_commit_id: str
    patches: list[ContentPatch] = Field(default_factory=list)
    chapter_id: str | None = None
    text: str | None = None
    rationale: str = ""

    @model_validator(mode="after")
    def content_present(self) -> "ManuscriptPatchProposal":
        if not self.patches and (not self.chapter_id or not self.text or not self.text.strip()):
            raise ValueError(
                "提供带基础版本的 patches 或 chapter_id + 非空 text；无修改使用 no_result"
            )
        if self.patches and self.text is not None:
            raise ValueError("patches 与整章 text 只能选择一种")
        return self


class SemanticItem(StrictModel):
    artifact_id: str
    reason: str
    confidence: float = Field(ge=0, le=1)


class ImpactSupplement(StrictModel):
    kind: Literal["impact_supplement"]
    base_commit_id: str
    items: list[SemanticItem]


class EvidenceFinding(StrictModel):
    artifact_ids: list[str] = Field(min_length=1)
    quote: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    suggestion: str = ""


class FindingSet(StrictModel):
    kind: Literal["finding_set"]
    base_commit_id: str
    findings: list[EvidenceFinding] = Field(default_factory=list)
    editorial_notes: list[str] = Field(default_factory=list)


class Observation(StrictModel):
    unit_id: str
    category: Literal["style", "fact", "structure", "formatting", "typo", "uncertain"]
    statement: str
    confidence: float = Field(ge=0, le=1)


class ObservationSet(StrictModel):
    kind: Literal["observation_set"]
    base_commit_id: str
    observations: list[Observation]


class ClarificationRequest(StrictModel):
    kind: Literal["clarification"]
    questions: list[str] = Field(min_length=1, max_length=3)


class NoResult(StrictModel):
    kind: Literal["no_result"]
    reason: str = Field(min_length=1)


class WorkerFailure(StrictModel):
    kind: Literal["worker_failure"]
    category: Literal["violation", "environment"]
    reason: str
    errors: list[dict[str, Any]] = Field(default_factory=list)
    raw_outputs: list[Any] = Field(default_factory=list)


AgentOutput = Annotated[
    StoryProposal
    | ManuscriptPatchProposal
    | ImpactSupplement
    | FindingSet
    | ObservationSet
    | ClarificationRequest
    | NoResult
    | WorkerFailure,
    Field(discriminator="kind"),
]
OUTPUT: TypeAdapter[AgentOutput] = TypeAdapter(AgentOutput)
ROLE_OUTPUTS = {
    "coordinator": {"story_proposal"},
    "planner": {"story_proposal"},
    "writer": {"manuscript_patch", "story_proposal"},
    "reviser": {"manuscript_patch", "story_proposal", "impact_supplement"},
    "auditor": {"finding_set"},
    "style_observer": {"observation_set"},
}


def parse_output(role: str, value: Any) -> AgentOutput:
    parsed = OUTPUT.validate_python(value)
    common = {"clarification", "no_result", "worker_failure"}
    if role not in ROLE_OUTPUTS or parsed.kind not in ROLE_OUTPUTS[role] | common:
        raise ValueError(f"{role} 无权输出 {parsed.kind}；按当前角色输出提案或澄清/空结果")
    return parsed


def output_schema(role: str) -> dict[str, Any]:
    """仅发送本角色的分支和传递引用，避免工具面重复灌入所有角色的 Schema。"""
    schema = OUTPUT.json_schema()
    allowed = ROLE_OUTPUTS[role] | {"clarification", "no_result", "worker_failure"}
    mapping = schema["discriminator"]["mapping"]
    schema["discriminator"]["mapping"] = {k: v for k, v in mapping.items() if k in allowed}
    refs = set(schema["discriminator"]["mapping"].values())
    schema["oneOf"] = [{"$ref": ref} for ref in sorted(refs)]
    definitions = schema.pop("$defs")
    selected: dict[str, Any] = {}

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            if "$ref" in value:
                name = value["$ref"].rsplit("/", 1)[-1]
                if name not in selected:
                    selected[name] = definitions[name]
                    visit(definitions[name])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(schema)
    schema["$defs"] = selected
    return schema

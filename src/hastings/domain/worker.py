"""工作者的可重放输入与最小能力；不持有模型或仓库写接口。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

WorkerRole = Literal["coordinator", "planner", "writer", "reviser", "auditor", "style_observer"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunCapability(StrictModel):
    role: WorkerRole
    project_id: str
    base_commit_id: str
    readable_ids: list[str]
    writable_ids: list[str] = Field(default_factory=list)
    allowed_tools: list[str] = Field(default_factory=list)
    creation_kinds: list[str] = Field(default_factory=list)
    creation_prefix: str = ""
    max_new_artifacts: int = Field(default=12, ge=0, le=32)


class SelectedInput(StrictModel):
    artifact_id: str
    revision_id: str
    kind: str
    reason: str
    content: dict[str, Any]


class Omission(StrictModel):
    artifact_id: str
    reason: str
    refetch_tool: str = "artifact_read"


class ContextPackage(StrictModel):
    context_id: str
    task_kind: WorkerRole
    project_id: str
    repository_commit_id: str
    target_ids: list[str]
    selected_inputs: list[SelectedInput] = Field(default_factory=list)
    author_preferences: list[dict[str, Any]] = Field(default_factory=list)
    author_examples: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    omissions: list[Omission] = Field(default_factory=list)
    instruction: str = ""
    evidence: dict[str, Any] = Field(default_factory=dict)
    token_budget: int = Field(gt=0)
    estimated_tokens: int = 0
    capability: RunCapability


ROLE_TOOLS: dict[str, tuple[str, ...]] = {
    "coordinator": (
        "workspace_snapshot",
        "artifact_read",
        "artifact_search",
        "change_read",
        "change_open",
    ),
    "planner": (
        "workspace_snapshot",
        "artifact_read",
        "artifact_search",
        "dependency_trace",
        "canon_patch_propose",
        "plan_patch_propose",
    ),
    "writer": (
        "artifact_read",
        "artifact_search",
        "author_profile_read",
        "manuscript_patch_propose",
    ),
    "reviser": (
        "artifact_read",
        "repository_diff",
        "dependency_trace",
        "change_read",
        "canon_patch_propose",
        "plan_patch_propose",
        "manuscript_patch_propose",
    ),
    "auditor": ("artifact_read", "artifact_search", "dependency_trace", "audit_read"),
    "style_observer": ("repository_diff", "author_profile_read", "observation_propose"),
}

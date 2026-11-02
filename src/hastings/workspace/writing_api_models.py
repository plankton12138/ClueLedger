"""写作工作台路由的请求体（1.0.9 P2）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hastings.workspace.creative_candidates import CandidateBase, CreativeTarget
from hastings.workspace.writing_actions import InformationMoveInput

__all__ = [
    "BookIntentRequest",
    "ChapterCreateRequest",
    "ChapterPlanRequest",
    "ChapterStartRequest",
    "CreativeAssistRequest",
    "ProseSaveRequest",
]


class BookIntentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    expected_plan_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class ChapterCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    after_chapter_id: str | None = Field(default=None, max_length=64)
    expected_plan_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class ChapterPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)
    intent: str = Field(min_length=1, max_length=4000)
    information_moves: list[InformationMoveInput] | None = None
    expected_chapter_revision: str = Field(min_length=1, max_length=64)
    expected_plan_revision: str = Field(min_length=1, max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class ChapterStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_story_revision: str = Field(min_length=1, max_length=64)
    expected_chapter_revision: str = Field(min_length=1, max_length=64)
    attach_path: str | None = Field(default=None, max_length=512)
    attach_digest: str | None = Field(default=None, max_length=128)
    request_key: str = Field(min_length=1, max_length=200)


class ProseSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    expected_digest: str = Field(min_length=1, max_length=128)
    expected_chapter_revision: str = Field(min_length=1, max_length=64)
    draft_revision: str = Field(default="", max_length=100)
    candidate_id: str | None = Field(default=None, max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class CreativeAssistRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=40)
    target: CreativeTarget
    base: CandidateBase = Field(default_factory=CandidateBase)
    selection: dict[str, object] | None = None
    author_note: str | None = Field(default=None, max_length=4000)
    draft_text: str | None = Field(default=None, max_length=20000)
    request_key: str = Field(min_length=1, max_length=200)

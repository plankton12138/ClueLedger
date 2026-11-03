"""写作工作台路由的请求体（1.0.9 P2；P6 移除章节构思请求）。"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from hastings.workspace.creative_candidates import CandidateBase, CreativeTarget

__all__ = [
    "BookIntentRequest",
    "StartupConfirmRequest",
    "ChapterCreateRequest",
    "IndexConfirmRequest",
    "CreativeAssistRequest",
    "ProseSaveRequest",
]


class StartupConfirmRequest(BaseModel):
    """起步设定卡确认（v1.1 M1）：不带 expected 修订，由服务端按当前状态核对。"""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(min_length=1, max_length=200)


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


class ProseSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    expected_digest: str = Field(min_length=1, max_length=128)
    draft_revision: str = Field(default="", max_length=100)
    candidate_id: str | None = Field(default=None, max_length=64)
    request_key: str = Field(min_length=1, max_length=200)


class IndexConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: str = Field(min_length=1, max_length=64)
    expected_prose_digest: str = Field(min_length=1, max_length=128)
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

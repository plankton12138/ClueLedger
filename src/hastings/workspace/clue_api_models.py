"""线索写接口的请求结构；与账本模型保持同名同字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from hastings.workspace.clue_models import DesignSource, InferenceIntent


class CreateClueRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    expected_revision: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)


class LinkDesignRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    expected_revision: str = Field(min_length=1, max_length=200)
    source: DesignSource
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=20000)
    relation: str = Field(default="", max_length=500)


class UnlinkDesignRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    expected_revision: str = Field(min_length=1, max_length=200)
    link_id: str = Field(min_length=1, max_length=64)


class InferenceStatementRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    clue_ids: list[str] = Field(default_factory=list)
    design_link_ids: list[str] = Field(default_factory=list)


class SaveInferenceRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    expected_revision: str = Field(min_length=1, max_length=200)
    inference_id: str | None = Field(default=None, max_length=64)
    inference_revision: str | None = Field(default=None, max_length=128)
    clue_ids: list[str] = Field(min_length=1, max_length=10)
    goal: str = Field(min_length=1, max_length=2000)
    intent: InferenceIntent
    premises: list[InferenceStatementRequest] = Field(default_factory=list, max_length=10)
    alternatives: list[InferenceStatementRequest] = Field(default_factory=list, max_length=10)
    design_link_ids: list[str] = Field(default_factory=list, max_length=50)

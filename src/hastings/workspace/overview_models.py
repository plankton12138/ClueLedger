"""故事总览的确定来源与派生契约；不改变作者模块格式。"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION = 1
RECIPE_VERSION = 2


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ModuleSource(StrictModel):
    binding_id: str
    library_id: str
    module_id: str
    revision_id: str
    entry_id: str
    digest: str


class Evidence(StrictModel):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def ordered(self) -> Evidence:
        if self.end <= self.start:
            raise ValueError("依据结束位置必须大于开始位置")
        return self


class FacetDraft(StrictModel):
    kind: Literal["character", "clue"]
    name: str = Field(min_length=1, max_length=80)
    summary: str = Field(max_length=500)
    evidence: Evidence


def facet_id(source: ModuleSource, facet: FacetDraft) -> str:
    # 同一句话可以同时说明两个人物；优先用引文中的对象名称区间作锚点。
    # 身份仍来自确定来源位置，不合并另一处或另一版本的同名对象。
    local = facet.evidence.quote.find(facet.name)
    start = facet.evidence.start + local if local >= 0 else facet.evidence.start
    end = start + len(facet.name) if local >= 0 else facet.evidence.end
    return (
        "facet_"
        + fingerprint(
            [
                source.model_dump(),
                facet.kind,
                start,
                end,
            ]
        )[:24]
    )


class FacetRef(StrictModel):
    source: ModuleSource
    facet_id: str


class RelationDraft(StrictModel):
    # 端点使用同一提交中对象的数组下标，由服务端换为确定对象引用。
    left: int = Field(ge=0)
    right: int = Field(ge=0)
    label: str = Field(min_length=1, max_length=120)
    evidence: Evidence


class ModuleResult(StrictModel):
    outcome: Literal["found", "empty", "insufficient"]
    facets: list[FacetDraft] = Field(default_factory=list, max_length=100)
    relations: list[RelationDraft] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def consistent(self) -> ModuleResult:
        if self.outcome != "found" and (self.facets or self.relations):
            raise ValueError("空结果或信息不足不能同时提交对象")
        if self.outcome == "found" and not self.facets:
            raise ValueError("发现结果需要至少一个对象")
        return self


class LinkDraft(StrictModel):
    ref: FacetRef
    event_id: str
    text: str = Field(min_length=1, max_length=300)
    evidence: Evidence


class WrittenRelation(StrictModel):
    left: FacetRef
    right: FacetRef
    label: str = Field(min_length=1, max_length=120)
    event_id: str
    evidence: Evidence


class LinkResult(StrictModel):
    outcome: Literal["found", "empty", "insufficient"]
    links: list[LinkDraft] = Field(default_factory=list, max_length=100)
    relations: list[WrittenRelation] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def consistent(self) -> LinkResult:
        if (self.outcome == "found") != bool(self.links or self.relations):
            raise ValueError("关联结果与覆盖状态不一致")
        return self


class StartUpdate(StrictModel):
    request_key: str = Field(min_length=8, max_length=120)
    observed_generation: str = Field(min_length=1)
    as_of_chapter: int = Field(ge=0)

"""线索写接口的请求结构；与账本模型保持同名同字段。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateClueRequest(BaseModel):
    request_key: str = Field(min_length=8, max_length=120)
    expected_revision: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=200)

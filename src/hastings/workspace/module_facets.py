"""作品内派生贡献：逐目标原子替换，校验失败保留旧结果。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hastings.library.storage import atomic_write_text
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.overview_models import (
    RECIPE_VERSION,
    SCHEMA_VERSION,
    Evidence,
    ModuleResult,
    ModuleSource,
    facet_id,
    fingerprint,
)


def overview_error(code: str, why: str) -> WorkspaceError:
    return WorkspaceError(
        code=code,
        where="故事总览",
        why=why,
        fix="刷新总览并按当前原文重新更新",
    )


def check_quote(text: str, evidence: Evidence) -> None:
    if evidence.end > len(text) or text[evidence.start : evidence.end] != evidence.quote:
        raise overview_error("OVERVIEW_EVIDENCE_INVALID", "依据区间与原文引文不一致")


class OverviewStore:
    def __init__(self, root: Path):
        self.root = root / "overview"

    def path(self, target_id: str) -> Path:
        # 目标 ID 不直接作为文件路径，HTTP 输入也不能穿越目录。
        return self.root / (fingerprint(target_id) + ".json")

    def read(self, target_id: str) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path(target_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
            return None
        return value

    def valid(self, target: dict[str, Any]) -> dict[str, Any] | None:
        value = self.read(target["id"])
        if (
            value
            and value.get("signature") == target["signature"]
            and value.get("outcome") != "insufficient"
        ):
            return value
        return None

    def publish(self, target: dict[str, Any], result: dict[str, Any], turn_id: str) -> None:
        payload = {
            **result,
            "target_id": target["id"],
            "signature": target["signature"],
            "source": target.get("source"),
            "schema_version": SCHEMA_VERSION,
            "recipe_version": RECIPE_VERSION,
            "turn_id": turn_id,
        }
        self.root.mkdir(parents=True, exist_ok=True)
        destination = (
            self.path(target["id"] + ":attempt")
            if result.get("outcome") == "insufficient"
            else self.path(target["id"])
        )
        atomic_write_text(destination, json.dumps(payload, ensure_ascii=False))

    def command(self, key: str) -> dict[str, Any] | None:
        try:
            value = json.loads(
                (self.root / "commands" / (fingerprint(key) + ".json")).read_text(encoding="utf-8")
            )
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def record_noop(self, request: dict[str, Any], response: dict[str, Any]) -> None:
        directory = self.root / "commands"
        directory.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            directory / (fingerprint(request["request_key"]) + ".json"),
            json.dumps({"request": request, "response": response}, ensure_ascii=False),
        )

    def generation(self, targets: list[dict[str, Any]]) -> str:
        return fingerprint(
            [
                (target["id"], self.read(target["id"]), self.read(target["id"] + ":attempt"))
                for target in targets
            ]
        )


def module_projection(source: ModuleSource, raw: object, text: str) -> dict[str, Any]:
    try:
        result = ModuleResult.model_validate(raw)
    except ValidationError as exc:
        raise overview_error(
            "OVERVIEW_RESULT_INVALID", "模块派生结果的字段或覆盖状态不符合要求"
        ) from exc
    facets = []
    for facet in result.facets:
        check_quote(text, facet.evidence)
        facets.append(
            {
                **facet.model_dump(),
                "ref": {"source": source.model_dump(), "facet_id": facet_id(source, facet)},
            }
        )
    ids = [item["ref"]["facet_id"] for item in facets]
    if len(set(ids)) != len(ids):
        raise overview_error("OVERVIEW_EVIDENCE_INVALID", "同一依据重复提交了同类对象")
    relations = []
    for relation in result.relations:
        check_quote(text, relation.evidence)
        if max(relation.left, relation.right) >= len(facets) or relation.left == relation.right:
            raise overview_error("OVERVIEW_REF_UNAVAILABLE", "关系端点不属于本次提交的不同对象")
        if any(facets[i]["kind"] != "character" for i in (relation.left, relation.right)):
            raise overview_error("OVERVIEW_REF_UNAVAILABLE", "人物关系只能引用人物对象")
        relations.append(
            {
                "left": facets[relation.left]["ref"],
                "right": facets[relation.right]["ref"],
                "label": relation.label,
                "evidence": relation.evidence.model_dump(),
                "nature": "declared",
            }
        )
    return {"outcome": result.outcome, "facets": facets, "relations": relations}

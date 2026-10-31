"""任务内草稿：保存增量操作，正式构思与线索账本只在完整提交时改变。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.module_assembly import ModuleRef
from hastings.workspace.story_changes import StoryInvalid, _apply
from hastings.workspace.story_models import StoryChangeSet, StoryVariant, validate_story_variant


def validation_issues(
    exc: ValidationError, operations: list[Any], *, batch_start: int = 0
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for error in exc.errors(include_input=False, include_url=False)[:20]:
        loc = error["loc"]
        index = (
            loc[1] if len(loc) > 1 and loc[0] == "operations" and isinstance(loc[1], int) else None
        )
        operation = operations[index] if index is not None and index < len(operations) else {}
        object_id = (
            operation.get("id")
            or operation.get("temp_id")
            or operation.get("clue_id")
            or next(
                (v.get("id") for v in operation.values() if isinstance(v, dict) and v.get("id")), ""
            )
            if isinstance(operation, dict)
            else ""
        )
        issues.append(
            {
                "code": "STORY_CHANGE_INVALID",
                "operation_index": index,
                "batch_index": index - batch_start
                if index is not None and index >= batch_start
                else None,
                "object_id": object_id,
                "field": ".".join(map(str, loc)),
                "related_ids": [],
                "message": error["msg"],
                "suggestion": (
                    "将此字段移入 node 对象，保留原字段内容"
                    if isinstance(operation, dict)
                    and operation.get("op") in ("add_node", "update_node")
                    and len(loc) == 4
                    and loc[3] in operation
                    and error["type"] == "extra_forbidden"
                    else "只修正对应对象的字段，保留知情时点、结局和约定强度"
                ),
            }
        )
    return issues


def candidate_data(variant: StoryVariant, change: StoryChangeSet) -> dict[str, Any]:
    data = variant.model_dump(mode="json")
    for index, operation in enumerate(change.operations):
        try:
            _apply(data, operation)
        except StoryInvalid as exc:
            raise WorkspaceError(
                code="STORY_GRAPH_INVALID",
                where=f"story.draft.operations.{index}",
                why=str(exc),
                fix="修正这一项操作后再检查草稿",
                details={"operation_index": index},
            ) from exc
    return data


def check_candidate(data: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    # 线索冲突一起报告，并给出对象标识，避免逐次重发整份结构。
    for clue_id, plan in data["clue_plans"].items():
        resolvers = [
            n["id"]
            for n in data["nodes"].values()
            if any(u["clue_id"] == clue_id and "resolve" in u["roles"] for u in n["clue_uses"])
        ]
        if (plan["resolution_intent"] == "explain") != bool(resolvers):
            issues.append(
                {
                    "code": "STORY_GRAPH_INVALID",
                    "operation_index": None,
                    "object_id": clue_id,
                    "field": "resolution_intent",
                    "related_ids": resolvers,
                    "message": "解释计划尚无回收节点"
                    if not resolvers
                    else "开放或待定计划同时安排了回收",
                    "suggestion": "核对线索作用与结局安排，只修正对应对象，不改变已确定的结局",
                }
            )
    try:
        variant = StoryVariant.model_validate(data)
        validate_story_variant(variant)
    except ValueError as exc:
        issues.append(
            {
                "code": "STORY_GRAPH_INVALID",
                "operation_index": None,
                "object_id": "",
                "field": "candidate",
                "related_ids": [],
                "message": str(exc)[:2000],
                "suggestion": "补齐草稿中的引用和实际事件时间关系，不移动知情生效时点",
            }
        )
    return issues[:20]


class StoryDraftStore:
    def __init__(self, root: Path, operation_id: str, variant_id: str) -> None:
        key = hashlib.sha256(f"{operation_id}:{variant_id}".encode()).hexdigest()
        self.path = root / "story" / "drafts" / f"{key}.json"
        self.lock_path = self.path.with_suffix(".lock")

    def read(self) -> dict[str, Any] | None:
        if not self.path.exists():
            return None
        return dict(json.loads(self.path.read_text(encoding="utf-8")))

    def prepare(
        self,
        *,
        variant: StoryVariant,
        operation_id: str,
        generation: int,
        operations: list[Any],
        replacements: dict[int, Any],
        expected: str = "",
        module_refs: tuple[ModuleRef, ...] = (),
    ) -> dict[str, Any]:
        with locked(self.lock_path):
            prior = self.read()
            frozen_modules = [m.view() for m in module_refs]
            if prior and (
                prior["generation"] > generation or prior.get("module_refs", []) != frozen_modules
            ):
                raise WorkspaceError(
                    code="STORY_SOURCE_CONFLICT",
                    where="story.draft",
                    why="任务代次或冻结模块版本已变化",
                    fix="保留原草稿，在当前来源范围内核对后继续",
                )
            if prior and (prior["base_revision"] != variant.revision or prior.get("committed")):
                raise WorkspaceError(
                    code="STORY_DRAFT_CONFLICT",
                    where="story.draft",
                    why="草稿依据的结构已变化或草稿已提交",
                    fix="读取正式结构，使用新任务重新安排修改",
                )
            if prior and expected != prior["digest"]:
                raise WorkspaceError(
                    code="STORY_DRAFT_CONFLICT",
                    where="story.draft",
                    why="草稿版本已变化",
                    fix="read_story(source=draft) 后用当前 draft_revision 修正",
                )
            rows = list(prior["operations"]) if prior else []
            for index, row in replacements.items():
                if index < 0 or index >= len(rows):
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where="story.draft",
                        why=f"待替换操作不存在：{index}",
                        fix="读取草稿中的操作索引后再替换",
                    )
                try:
                    checked = StoryChangeSet(
                        id="check", base_revision=variant.revision, operations=[row]
                    )
                except ValidationError as exc:
                    issues = validation_issues(exc, [row])
                    for issue in issues:
                        issue["operation_index"] = index
                        issue["field"] = issue["field"].replace(
                            "operations.0.", f"operations.{index}.", 1
                        )
                    raise WorkspaceError(
                        code="STORY_CHANGE_INVALID",
                        where=f"story.draft.operations.{index}",
                        why="替换操作的字段校验未通过",
                        fix="按问题列表修正当前替换项，已有草稿保留",
                        details={"issues": issues},
                    ) from exc
                row = checked.operations[0].model_dump(mode="json")
                original = rows[index]

                def identity(operation: dict[str, Any]) -> tuple[str, str]:
                    for key in (
                        "node",
                        "stage",
                        "thread",
                        "constraint",
                        "edge",
                        "binding",
                        "assessment",
                    ):
                        if isinstance(operation.get(key), dict):
                            return key, str(operation[key].get("id", ""))
                    return str(operation["op"]), str(
                        operation.get("temp_id") or operation.get("clue_id") or ""
                    )

                if identity(original) != identity(row):
                    raise WorkspaceError(
                        code="STORY_DRAFT_TARGET_CONFLICT",
                        where="story.draft",
                        why="替换对象的种类或标识与原操作不一致",
                        fix="按目标对象读取操作索引，只修改同一个对象",
                    )
                if original.get("op") == row.get("op") == "upsert_clue_plan":
                    intent = original["plan"].get("resolution_intent", "undecided")
                    if (
                        intent != "undecided"
                        and row["plan"].get("resolution_intent", "undecided") != intent
                    ):
                        raise WorkspaceError(
                            code="STORY_SEMANTIC_CONFLICT",
                            where="story.draft",
                            why="局部修正改变了已确定的线索结局安排",
                            fix="保留解释或开放计划，核对节点中的线索作用；改变结局应由作者另行提出",
                        )
                if (
                    original.get("op") in ("add_node", "update_node")
                    and row.get("op") == original["op"]
                ):
                    prior_knowledge = {
                        (k["subject_id"], k["claim"]): k["effective_event_node_id"]
                        for k in original["node"].get("knowledge_changes", [])
                    }
                    for knowledge in row["node"].get("knowledge_changes", []):
                        key = (knowledge["subject_id"], knowledge["claim"])
                        if (
                            key in prior_knowledge
                            and knowledge["effective_event_node_id"] != prior_knowledge[key]
                        ):
                            raise WorkspaceError(
                                code="STORY_SEMANTIC_CONFLICT",
                                where="story.draft",
                                why="局部修正移动了同一知情内容的生效事件",
                                fix="保留原生效事件，核对来源类型与实际先后；改变知情时点应由作者另行提出",
                            )
                if (
                    original.get("op") in ("add_constraint", "update_constraint")
                    and original.get("constraint", {}).get("strength") == "required"
                ) and (
                    row.get("op") != original["op"]
                    or row.get("constraint", {}).get("strength") != "required"
                ):
                    raise WorkspaceError(
                        code="STORY_AUTHORIZATION_CONFLICT",
                        where="story.draft",
                        why="修正草稿不能降低必需约定强度或移除约定",
                        fix="保留 required，提交来源确认或向作者集中提问",
                    )
                rows[index] = row
            batch_start = len(rows)
            rows.extend(operations)
            try:
                change = StoryChangeSet(
                    id=(prior or {}).get("change_id") or "draft_" + self.path.stem[:24],
                    base_revision=variant.revision,
                    operations=rows,
                    operation_id=operation_id,
                    generation=generation,
                )
            except ValidationError as exc:
                raise WorkspaceError(
                    code="STORY_CHANGE_INVALID",
                    where="story.draft",
                    why="草稿字段校验未通过",
                    fix="按问题列表局部修正，已有草稿保留",
                    details={"issues": validation_issues(exc, rows, batch_start=batch_start)},
                ) from exc
            # 在写盘前应用本批；重复 ID 等硬错误不得覆盖已有有效草稿。
            candidate_data(variant, change)
            normalized = [op.model_dump(mode="json") for op in change.operations]
            digest = hashlib.sha256(
                json.dumps(normalized, ensure_ascii=False, sort_keys=True).encode()
            ).hexdigest()
            payload = {
                "change_id": change.id,
                "variant_id": variant.id,
                "base_revision": variant.revision,
                "operation_id": operation_id,
                "generation": generation,
                "operations": normalized,
                "digest": digest,
                "committed": False,
                "module_refs": frozen_modules,
            }
            atomic_write_json(self.path, payload)
            return payload

    def mark_committed(self, digest: str, receipt: dict[str, Any]) -> None:
        with locked(self.lock_path):
            prior = self.read()
            if prior and prior["digest"] == digest:
                atomic_write_json(self.path, {**prior, "committed": True, "receipt": receipt})

    def confirm_constraints(self, digest: str, keys: list[str]) -> None:
        """仅供作者确认接口调用；授权绑定本任务当前草稿摘要。"""
        with locked(self.lock_path):
            prior = self.read()
            if not prior or prior["digest"] != digest or prior.get("committed"):
                raise WorkspaceError(
                    code="STORY_DRAFT_CONFLICT",
                    where="story.draft",
                    why="待确认草稿已变化",
                    fix="刷新列表后重新核对",
                )
            atomic_write_json(self.path, {**prior, "confirmed_constraints": keys})

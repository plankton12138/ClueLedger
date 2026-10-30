"""正文候选：先持久保留草稿，再核对，最后通过联合提交发布。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hastings.prompts.story import (
    constraint_requirement,
    describe_story_operation,
    scene_requirement,
    structure_change_requirement,
)
from hastings.prompts.tools import (
    REPAIR_STORY_CANDIDATE_DESCRIPTION,
    SUBMIT_STORY_CANDIDATE_REVIEW_DESCRIPTION,
)
from hastings.workspace.atomic_io import atomic_write_json
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, managed_kind, normalize_newlines
from hastings.workspace.story_authorization import authorize_change, validate_model_change
from hastings.workspace.story_changes import apply_change_set
from hastings.workspace.story_context import StoryContextSnapshot, assemble_story_input
from hastings.workspace.story_models import (
    AddNode,
    BindProse,
    ProseBinding,
    ProsePatch,
    ReadDependency,
    RemoveNode,
    StoryChangeSet,
    StoryOperation,
    UnbindProse,
)
from hastings.workspace.story_review import StoryCheck, StoryReview, build_story_review
from hastings.workspace.story_store import StoryStore, new_id
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult


def conflict(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="STORY_CANDIDATE_CONFLICT",
        where="story.candidate",
        why=why,
        fix="保留候选，核对原文与当前要求后重新处理",
    )


@dataclass
class StoryCandidate:
    workspace: Workspace
    state_root: Path
    frozen: StoryContextSnapshot
    operation_id: str
    generation: int
    guard: Callable[[], None]
    record: Callable[[str, Mapping[str, Any]], None]
    author_text: str
    id: str = field(default_factory=lambda: new_id("candidate"))
    documents: dict[str, str] = field(default_factory=dict)
    originals: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: 与正文一起提交的结构变更；核对与发布时成组生效，不单独落盘。
    operations: list[StoryOperation] = field(default_factory=list)
    repaired: bool = False
    approved: bool = False
    status: str = "draft"
    review: StoryReview | None = None
    report: dict[str, Any] | None = None
    context_text: str = ""
    source_candidate_id: str = ""

    @property
    def file(self) -> Path:
        return self.state_root / "story" / "candidates" / f"{self.id}.json"

    def save(self, *, check_guard: bool = True) -> None:
        if check_guard:
            self.guard()
        atomic_write_json(
            self.file,
            {
                "id": self.id,
                "operation_id": self.operation_id,
                "generation": self.generation,
                "variant_id": self.frozen.variant_id,
                "revision": self.frozen.revision,
                "node_ids": list(self.frozen.node_ids),
                "purpose": self.frozen.purpose,
                "structure_readonly": self.frozen.structure_readonly,
                "documents": self.documents,
                "originals": self.originals,
                "operations": [item.model_dump(mode="json") for item in self.operations],
                "status": self.status,
                "repaired": self.repaired,
                "report": self.report,
                "author_text": self.author_text,
                "context_text": self.context_text,
                "requirements": [
                    {"id": item.requirement_id, "text": item.text} for item in self.review.checks
                ]
                if self.review
                else [],
            },
            prefix=".candidate-",
        )

    def previous(self) -> dict[str, Any] | None:
        """只承接本操作的上一代或问题明确绑定的候选，并验证原始版本。"""
        self.guard()
        directory = self.state_root / "story" / "candidates"
        store = StoryStore(self.state_root, self.workspace)
        snapshot = store.read()
        rows: list[dict[str, Any]] = []
        for path in directory.glob("candidate_*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise conflict("无法读取已有候选") from exc
            if not isinstance(raw, dict) or raw.get("id") != path.stem:
                raise conflict("已有候选的身份不一致")
            selected = (
                raw.get("id") == self.source_candidate_id
                if self.source_candidate_id
                else raw.get("operation_id") == self.operation_id
                and isinstance(raw.get("generation"), int)
                and raw["generation"] < self.generation
            )
            if not selected:
                continue
            if raw.get("status") == "committed" or (
                snapshot.document and f"candidate:{path.stem}" in snapshot.document.requests
            ):
                continue
            if (
                raw.get("variant_id") != self.frozen.variant_id
                or raw.get("revision") != self.frozen.revision
            ):
                raise conflict("保留候选的构思版本已经变化")
            if (
                raw.get("node_ids") != list(self.frozen.node_ids)
                or raw.get("purpose") != self.frozen.purpose
            ):
                raise conflict("保留候选的构思节点或用途已经变化")
            documents, originals = raw.get("documents"), raw.get("originals")
            if not isinstance(documents, dict) or not isinstance(originals, dict):
                raise conflict("保留候选的正文记录不完整")
            for name, text in documents.items():
                original = originals.get(name)
                if (
                    not isinstance(name, str)
                    or not isinstance(text, str)
                    or not isinstance(original, list)
                    or len(original) != 2
                    or not all(isinstance(value, str) for value in original)
                ):
                    raise conflict("保留候选的正文版本格式无效")
                if store._prose_state(name, self.frozen.variant_id) != tuple(original):
                    raise conflict("保留候选对应的正文已经变化")
            rows.append(raw)
        if not rows:
            if self.source_candidate_id:
                raise conflict("找不到问题对应的未提交候选")
            return None
        raw = max(rows, key=lambda row: int(row.get("generation", 0)))
        return {
            "candidate_id": raw["id"],
            "documents": raw["documents"],
            "originals": raw["originals"],
            "operations": raw.get("operations", []),
            "report": raw.get("report"),
            "author_text": raw.get("author_text", ""),
        }

    def propose(self, path: str, base_digest: str, text: str) -> None:
        self.guard()
        if any(
            word in self.author_text
            for word in ("只讨论", "不保存", "只分析", "不要修改", "不执行修改")
        ):
            raise conflict("作者本轮要求只讨论，没有保存正文的授权")
        if self.status != "draft":
            raise conflict("当前候选已进入核对")
        if managed_kind(path) != "chapter":
            raise conflict("候选只能修改当前作品的正文文件")
        digest, original_text = StoryStore(self.state_root, self.workspace)._prose_state(
            path, self.frozen.variant_id
        )
        if digest != base_digest:
            raise conflict("候选依据的正文版本已经变化")
        original = self.originals.get(path)
        if original and original[0] != base_digest:
            raise conflict("同一候选的正文基础版本发生变化")
        self.originals[path] = (digest, original_text)
        self.documents[path] = normalize_newlines(text)
        self.approved = False
        self.save()
        self.record(
            "story_candidate_saved", {"candidate_id": self.id, "path": path, "status": self.status}
        )

    def propose_structure(self, operations: list[dict[str, Any]]) -> dict[str, Any]:
        """登记与正文一起提交的结构变更；先做对象授权、图结构与标识校验。"""
        self.guard()
        if self.status != "draft":
            raise conflict("候选已进入核对，不能再追加结构变更")
        store = StoryStore(self.state_root, self.workspace)
        variant = store.require_variant(self.frozen.variant_id)
        if variant.revision != self.frozen.revision:
            raise conflict("构思结构在候选生成后发生变化")
        try:
            change = StoryChangeSet(
                id=new_id("candidate_structure"),
                base_revision=self.frozen.revision,
                operations=operations,  # type: ignore[arg-type]
            )
            apply_change_set(variant, change)
        except ValueError as exc:
            raise conflict(f"结构变更无法应用：{exc}") from exc
        authorize_change(variant, self.frozen, self.author_text, change)
        validate_model_change(variant, change, self.author_text)
        self.operations = list(change.operations)
        self.approved = False
        self.report = None
        self.save()
        self.record(
            "story_candidate_structure",
            {"candidate_id": self.id, "operations": len(self.operations)},
        )
        return {
            "candidate_id": self.id,
            "operations": [item.model_dump(mode="json") for item in self.operations],
            "note": "结构变更已登记，将与候选正文一起核对、一起提交。",
        }

    def requirements(self) -> StoryReview:
        self.guard()
        variant = StoryStore(self.state_root, self.workspace).require_variant(
            self.frozen.variant_id
        )
        if variant.revision != self.frozen.revision:
            raise conflict("构思结构在候选生成后发生变化")
        if self.operations:
            variant = apply_change_set(
                variant,
                StoryChangeSet(
                    id=self.id, base_revision=variant.revision, operations=self.operations
                ),
            )
        self.context_text = assemble_story_input(variant, self.frozen).render()
        review = build_story_review(variant, self.frozen, documents=self.documents)
        checks = list(review.checks)
        for index, operation in enumerate(self.operations):
            checks.append(
                StoryCheck(
                    requirement_id=f"structure:{index}",
                    kind="semantic",
                    text=structure_change_requirement(describe_story_operation(operation)),
                )
            )
        for path in self.documents:
            checks.append(
                StoryCheck(
                    requirement_id=f"scene:{path}",
                    kind="semantic",
                    text=scene_requirement(path, self.author_text),
                )
            )
        for constraint in variant.constraints.values():
            if constraint.strength == "required":
                checks.append(
                    StoryCheck(
                        requirement_id=f"constraint:{constraint.id}",
                        kind="semantic",
                        text=constraint_requirement(
                            constraint.text,
                            constraint.scope.kind,
                            constraint.scope.target_id,
                        ),
                    )
                )
        self.review = StoryReview(checks=tuple(checks), findings=review.findings, text=review.text)
        return self.review

    def review_tool(self) -> AgentTool:
        async def submit(
            tool_call_id: str,
            arguments: Mapping[str, Any],
            signal: Any = None,
            on_update: Any = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            self.guard()
            if self.status != "reviewing" or self.review is None or self.report is not None:
                raise conflict("候选当前不接受核对报告")
            rows = arguments.get("checks")
            if not isinstance(rows, list):
                raise conflict("核对报告缺少逐项结论")
            wanted = {item.requirement_id for item in self.review.checks}
            seen: set[str] = set()
            approved = True
            for row in rows:
                if not isinstance(row, dict) or row.get("id") not in wanted:
                    raise conflict("核对报告包含未知要求")
                key = row["id"]
                if key in seen or row.get("status") not in ("pass", "fail", "unknown"):
                    raise conflict("核对报告存在重复要求或无效结论")
                seen.add(key)
                if not isinstance(row.get("reason"), str) or not row["reason"].strip():
                    raise conflict("每项核对都需要具体依据")
                if key.startswith("scene:") and row.get("path") != key.removeprefix("scene:"):
                    raise conflict("场景核对引用了其他文件")
                if row["status"] != "pass":
                    approved = False
                elif key.startswith("scene:"):
                    text = self.documents.get(str(row.get("path") or ""), "")
                    quote = row.get("quote")
                    if not isinstance(quote, str) or not quote.strip() or quote not in text:
                        raise conflict("通过结论没有对应的候选原文")
            if seen != wanted:
                raise conflict("核对报告未覆盖全部要求")
            self.approved = approved and not any(
                row.get("severity") == "blocking" for row in self.review.findings
            )
            self.report = {
                "checks": rows,
                "approved": self.approved,
                "findings": list(self.review.findings),
            }
            self.save()
            self.record(
                "story_candidate_reviewed", {"candidate_id": self.id, "approved": self.approved}
            )
            return AgentToolResult(
                content=[TextContent(text=json.dumps(self.report, ensure_ascii=False))]
            )

        return AgentTool(
            name="submit_story_candidate_review",
            label="核对候选正文",
            description=SUBMIT_STORY_CANDIDATE_REVIEW_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "checks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "status": {"enum": ["pass", "fail", "unknown"]},
                                "path": {"type": "string"},
                                "quote": {"type": "string"},
                                "reason": {"type": "string"},
                            },
                            "required": ["id", "status", "path", "quote", "reason"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["checks"],
                "additionalProperties": False,
            },
            execute_fn=submit,
            execution_mode="sequential",
        )

    def repair_tool(self) -> AgentTool:
        async def repair(
            tool_call_id: str,
            arguments: Mapping[str, Any],
            signal: Any = None,
            on_update: Any = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            self.guard()
            if self.repaired or self.status != "repairing":
                raise conflict("本次候选只允许一次局部修正")
            rows = arguments.get("patches")
            if not isinstance(rows, list) or not rows:
                raise conflict("局部修正需要明确的原文替换")
            documents = dict(self.documents)
            size = 0
            for row in rows:
                if not isinstance(row, dict):
                    raise conflict("局部修正字段无效")
                path, expected, replacement = (
                    row.get("path"),
                    row.get("expected"),
                    row.get("replacement"),
                )
                if (
                    path not in documents
                    or not isinstance(expected, str)
                    or not expected
                    or not isinstance(replacement, str)
                    or documents[path].count(expected) != 1
                ):
                    raise conflict("局部修正越界或无法唯一定位")
                size += len(expected) + len(replacement)
                documents[path] = documents[path].replace(expected, replacement, 1)
            if size > 2000 or size > max(200, sum(map(len, self.documents.values())) // 2):
                raise conflict("修正超出有限局部范围")
            self.documents = {path: normalize_newlines(text) for path, text in documents.items()}
            self.repaired = True
            self.approved = False
            self.report = None
            self.save()
            return AgentToolResult(
                content=[TextContent(text="候选已局部修正，尚未提交，等待复核。")]
            )

        return AgentTool(
            name="repair_story_candidate",
            label="局部修正候选",
            description=REPAIR_STORY_CANDIDATE_DESCRIPTION,
            parameters={
                "type": "object",
                "properties": {
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                name: {"type": "string"}
                                for name in ("path", "expected", "replacement")
                            },
                            "required": ["path", "expected", "replacement"],
                            "additionalProperties": False,
                        },
                    }
                },
                "required": ["patches"],
                "additionalProperties": False,
            },
            execute_fn=repair,
            execution_mode="sequential",
        )

    def change(self) -> StoryChangeSet:
        if not self.approved or self.status != "reviewing":
            raise conflict("候选没有通过完整核对，不能提交")
        operations = list(self.operations)
        if (
            len(self.frozen.node_ids) == 1
            and len(self.documents) == 1
            and not any(
                isinstance(op, (BindProse, UnbindProse, AddNode, RemoveNode)) for op in operations
            )
        ):
            node_id = self.frozen.node_ids[0]
            variant = StoryStore(self.state_root, self.workspace).require_variant(
                self.frozen.variant_id
            )
            path, text = next(iter(self.documents.items()))
            node = variant.nodes.get(node_id)
            quote = (
                next((use.presentation for use in node.clue_uses if use.presentation in text), "")
                if node
                else ""
            )
            start = (
                text.find(quote)
                if quote
                else (text.find("\n\n") + 2 if text.startswith("#") and "\n\n" in text else 0)
            )
            quote = quote or text[start : start + 200].rstrip()
            if node is not None and quote:
                operations.extend(
                    UnbindProse(binding_id=binding.id)
                    for binding in variant.bindings.values()
                    if binding.node_id == node_id and binding.path == path
                )
                operations.append(
                    BindProse(
                        binding=ProseBinding(
                            id="binding_" + self.id.removeprefix("candidate_"),
                            node_id=node_id,
                            document_id="doc_" + hashlib.sha256(path.encode()).hexdigest()[:24],
                            path=path,
                            digest=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                            start=start,
                            end=start + len(quote),
                            quote=quote,
                            observation="本轮核对通过的场景原文。",
                        )
                    )
                )
        return StoryChangeSet(
            id=self.id,
            base_revision=self.frozen.revision,
            operations=operations,
            prose_patches=[
                ProsePatch(
                    path=path,
                    base_digest=self.originals[path][0],
                    start=0,
                    end=len(self.originals[path][1]),
                    expected=self.originals[path][1],
                    replacement=text,
                )
                for path, text in self.documents.items()
            ],
            read_dependencies=[
                ReadDependency(
                    kind="variant", id=self.frozen.variant_id, revision=self.frozen.revision
                )
            ],
            operation_id=self.operation_id,
            generation=self.generation,
        )

"""可稳定引用的字段/正文行块；部分接受只重放选中的块。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from hastings.domain.changes.errors import violation
from hastings.domain.repository.serialization import content_hash


@dataclass(frozen=True, slots=True)
class PatchSet:
    patch_set_id: str
    change_set_id: str
    base_commit_id: str
    branch_commit_id: str
    hunks: list[dict[str, Any]]
    bases: dict[str, dict[str, Any]]
    status: str = "review"

    def to_content(self) -> dict[str, Any]:
        return asdict(self)


def make_hunks(
    artifact_id: str, before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, Any]]:
    hunks = []
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name) and (name in before) == (name in after):
            continue
        if (
            name == "text"
            and isinstance(before.get(name), str)
            and isinstance(after.get(name), str)
        ):
            old, new = before[name].splitlines(keepends=True), after[name].splitlines(keepends=True)
            for tag, i, j, k, end in SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
                if tag != "equal":
                    hunks.append(
                        {
                            "artifact_id": artifact_id,
                            "field": name,
                            "start": i,
                            "end": j,
                            "old": old[i:j],
                            "new": new[k:end],
                            "text": True,
                        }
                    )
        else:
            hunks.append(
                {
                    "artifact_id": artifact_id,
                    "field": name,
                    "old": before.get(name),
                    "new": after.get(name),
                    "old_exists": name in before,
                    "new_exists": name in after,
                    "text": False,
                }
            )
    for hunk in hunks:
        hunk["hunk_id"] = content_hash(hunk)
    return hunks


def apply_hunks(
    base: dict[str, Any], current: dict[str, Any], hunks: list[dict[str, Any]]
) -> dict[str, Any]:
    result = deepcopy(current)
    for name in sorted({h["field"] for h in hunks}):
        if current.get(name) != base.get(name) or (name in current) != (name in base):
            raise violation(
                "CHANGES_REVIEW_CONFLICT",
                f"review.{name}",
                "作者已修改该字段，当前补丁基于旧版本",
                "保留人工稿，重新比较并生成补丁",
            )
        parts = [h for h in hunks if h["field"] == name]
        if parts[0]["text"]:
            lines = base[name].splitlines(keepends=True)
            for h in sorted(parts, key=lambda h: h["start"], reverse=True):
                lines[h["start"] : h["end"]] = h["new"]
            result[name] = "".join(lines)
        elif parts[0]["new_exists"]:
            result[name] = deepcopy(parts[0]["new"])
        else:
            result.pop(name, None)
    return result

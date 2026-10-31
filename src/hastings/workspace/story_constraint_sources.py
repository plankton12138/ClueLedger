"""由作者界面确认的模块约定；普通模块文字不能自行授予必需约定权限。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from hastings.library.modules import ModuleLibrary
from hastings.workspace.atomic_io import atomic_write_json, locked
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.module_assembly import ModuleRef
from hastings.workspace.story_models import Constraint


def source_key(constraint: Constraint) -> str:
    data = {
        "source_ref": constraint.source_ref.model_dump() if constraint.source_ref else None,
        "text": constraint.text,
        "scope": constraint.scope.model_dump(),
        "strength": constraint.strength,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ConstraintSources:
    def __init__(self, root: Path) -> None:
        self.path = root / "story" / "constraint-sources.json"

    def read(self) -> dict[str, Any]:
        return dict(json.loads(self.path.read_text(encoding="utf-8"))) if self.path.exists() else {}

    @staticmethod
    def verify(
        constraint: Constraint, library: ModuleLibrary | None, modules: tuple[ModuleRef, ...]
    ) -> None:
        ref = constraint.source_ref
        if (
            ref is None
            or library is None
            or not any(
                (m.library_id, m.module_id, m.revision_id)
                == (ref.library_id, ref.module_id, ref.revision_id)
                for m in modules
            )
            or library.library_id != ref.library_id
        ):
            raise WorkspaceError(
                code="STORY_SOURCE_CONFLICT",
                where="story.constraint.source",
                why="约定来源不在当前冻结模块版本中",
                fix="读取当前装配的完整来源引用后重新核对",
            )
        record = library.read_entry(
            module_id=ref.module_id, revision_id=ref.revision_id, entry_id=ref.entry_id
        )
        # 从不可变版本读取完整正文，避免单次读取上限把有效引用误判为缺失。
        text, digest = library.storage.read_entry(ref.module_id, ref.revision_id, ref.entry_id)
        if record["digest"] != ref.digest or digest != ref.digest or constraint.text not in text:
            raise WorkspaceError(
                code="STORY_SOURCE_CONFLICT",
                where="story.constraint.source",
                why="约定原文或摘要与模块版本不一致",
                fix="保留原文，从该版本中选择准确内容；改写内容需作者另行确认",
            )

    def confirm(
        self,
        constraints: list[Constraint],
        library: ModuleLibrary | None,
        modules: tuple[ModuleRef, ...],
    ) -> None:
        for constraint in constraints:
            self.verify(constraint, library, modules)
        with locked(self.path.with_suffix(".lock")):
            data = self.read()
            for constraint in constraints:
                data[source_key(constraint)] = constraint.model_dump(mode="json")
            atomic_write_json(self.path, data)

    def verified(
        self, library: ModuleLibrary | None, modules: tuple[ModuleRef, ...]
    ) -> dict[str, Any]:
        result = {}
        for key, row in self.read().items():
            constraint = Constraint.model_validate(row)
            try:
                self.verify(constraint, library, modules)
            except WorkspaceError:
                continue
            result[key] = row
        return result

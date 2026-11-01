"""正文里的构思出处标记：只从当前绑定生成，不做语义判断。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.story_models import StoryVariant
from hastings.workspace.story_sync import binding_state, contract_digest, current_span

__all__ = ["ProseMark", "ProseMarksView", "build_prose_marks"]


class ProseMark(BaseModel):
    node_id: str
    title: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    quote: str
    state: str
    source: str


class ProseMarksView(BaseModel):
    path: str
    digest: str
    marks: list[ProseMark] = Field(default_factory=list)


def build_prose_marks(
    workspace: Workspace,
    variant: StoryVariant,
    *,
    chapter_path: str,
) -> ProseMarksView:
    entry = next(
        (row for row in workspace.catalog().chapters if row.path == chapter_path and row.exists),
        None,
    )
    if entry is None or entry.chapter_number is None:
        raise WorkspaceError(
            code="PROSE_CHAPTER_NOT_FOUND",
            where="prose.marks",
            why=f"所选路径不是当前正文章节：{chapter_path}",
            fix="刷新目录后重新选择章节",
            details={"path": chapter_path},
        )
    file = workspace.read(chapter_path)
    marks: list[ProseMark] = []
    for binding in variant.bindings.values():
        if binding.path != chapter_path:
            continue
        node = variant.nodes.get(binding.node_id)
        if node is None:
            continue
        span = current_span(binding, file.text)
        if span is None:
            continue
        start, end, prose_digest = span
        state = binding_state(binding, contract_digest(variant, node), prose_digest)
        marks.append(
            ProseMark(
                node_id=node.id,
                title=node.title,
                start=start,
                end=end,
                quote=binding.quote,
                state=state,
                source=binding.source,
            )
        )
    marks.sort(key=lambda row: (row.start, row.end, row.title))
    return ProseMarksView(path=chapter_path, digest=file.digest, marks=marks)

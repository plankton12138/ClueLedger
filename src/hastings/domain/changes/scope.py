"""章节范围展开为稳定对象 ID；普通 roots 保持原有对象白名单语义。"""

from dataclasses import replace

from hastings.domain.changes.models import ChangeScope
from hastings.domain.repository.models import Snapshot


def expand_scope(scope: ChangeScope, snapshot: Snapshot) -> ChangeScope:
    containers = {
        v.artifact.id.value
        for v in snapshot.artifacts.values()
        if v.artifact.kind
        in ("manuscript.chapter", "narrative.chapter", "narrative.arc", "narrative.book")
    }
    if not set(scope.roots) & containers:
        return scope
    allowed = set(scope.roots)
    changed = True
    while changed:
        changed = False
        for view in snapshot.artifacts.values():
            if not view.revision:
                continue
            body = view.revision.content
            parents = {
                body.get(field)
                for field in ("parent_id", "chapter_id", "narrative_chapter_id", "at_beat_id")
            }
            aid = view.artifact.id.value
            if aid not in allowed and parents & allowed:
                allowed.add(aid)
                changed = True
            if aid in allowed:
                for child in body.get("text_unit_ids", ()):
                    if child not in allowed:
                        allowed.add(child)
                        changed = True
    # Canon 对象作为章节之间共用的事实保留；正文与结构按所选章节截断。
    allowed.update(
        v.artifact.id.value
        for v in snapshot.artifacts.values()
        if v.artifact.kind.startswith("story.")
        and v.artifact.kind not in ("story.knowledge", "story.disclosure", "story.timeline_event")
    )
    return replace(scope, roots=tuple(sorted(allowed)))

"""作者计划关联与推理目标（1.0 S4）。

计划与目标只描述作者打算怎样安排线索，不复制模块正文，也不产生正文观察、覆盖或披露。
来源必须是作品设定文件或当前装配中的精确模块条目；模块升级、卸下后不按同名或最新版重绑。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from pydantic import JsonValue, ValidationError

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_models import (
    ClueDesignLink,
    ClueIdentity,
    ClueInference,
    ClueLedger,
    DesignSource,
    InferenceIntent,
    InferenceStatement,
)
from hastings.workspace.clue_store import ClueLedgerStore, new_id
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace, managed_kind
from hastings.workspace.module_assembly import Assembly, AssemblyStore

__all__ = [
    "create_clue_identity",
    "link_clue_design",
    "save_clue_inference",
    "unlink_clue_design",
    "validate_design_source",
]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _scope_error(why: str, fix: str, details: dict[str, object] | None = None) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_SCOPE_INVALID", where="clues.design", why=why, fix=fix, details=details
    )


def _revision_conflict(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_REVISION_CONFLICT",
        where="clues.design",
        why=why,
        fix="重新读取线索、计划与目标后，用新的请求键提交",
    )


def _not_found(why: str) -> WorkspaceError:
    return WorkspaceError(
        code="CLUE_NOT_FOUND",
        where="clues.design",
        why=why,
        fix="重新读取线索详情，确认关联或目标仍然存在",
    )


def _checked_range(text: str, start: int, end: int, quote: str) -> None:
    if not (0 <= start < end <= len(text)) or text[start:end] != quote:
        raise _scope_error(
            "计划引文与所选来源区间不一致",
            "重新选择来源中的原文区间；不要沿用旧坐标",
            {"start": start, "end": end, "quote": quote},
        )


def validate_design_source(
    workspace: Workspace,
    source: DesignSource,
    *,
    start: int,
    end: int,
    quote: str,
    library: ModuleLibrary | None = None,
    assembly: Assembly | None = None,
) -> tuple[DesignSource, str]:
    """按真实来源核对计划区间；返回可保存的来源与模块绑定身份。"""
    if source.kind == "file":
        if managed_kind(source.path) not in ("setting", "character"):
            raise _scope_error(
                f"计划来源只能使用设定或角色小传：{source.path}",
                "从设定集选择条目；正文章节里的人物流言不能当作者计划",
            )
        try:
            view = workspace.read(source.path)
        except WorkspaceError as exc:
            raise _scope_error(
                f"计划来源当前不可读：{source.path}（{exc.why}）",
                "确认文件仍在作品内，再重新选择计划区间",
            ) from exc
        if view.digest != source.digest:
            raise WorkspaceError(
                code="CLUE_SOURCE_STALE_CONFLICT",
                where="clues.design",
                why="所选设定文件在读取后发生了变化",
                fix="重新读取当前文件并选择区间；不要用旧指纹保存计划",
            )
        _checked_range(view.text, start, end, quote)
        return source, ""

    if library is None or library.library_id != source.library_id:
        raise WorkspaceError(
            code="CLUE_ACCESS_DENIED",
            where="clues.design",
            why="计划来源不属于当前模块库",
            fix="从本作品已装模块重新选择条目",
        )
    current = assembly if assembly is not None else AssemblyStore(Path(workspace.root)).read()
    binding = next(
        (
            item
            for item in current.bindings
            if item.module.library_id == source.library_id
            and item.module.module_id == source.module_id
            and item.module.revision_id == source.revision_id
        ),
        None,
    )
    if binding is None:
        raise WorkspaceError(
            code="CLUE_ACCESS_DENIED",
            where="clues.design",
            why="计划来源的模块版本不在当前装配里",
            fix="从已装模块选择精确条目；模块升级或卸下后需要作者重新关联",
        )
    try:
        checked = library.read_entry(
            module_id=source.module_id,
            revision_id=source.revision_id,
            entry_id=source.entry_id,
            end=0,
        )
        text, digest = library.storage.read_entry(
            source.module_id, source.revision_id, source.entry_id
        )
    except WorkspaceError as exc:
        raise _scope_error(
            "计划来源的模块条目当前不可读",
            "重新选择已装模块的条目；不要在旧版本目录里手工补内容",
        ) from exc
    if digest != str(checked["digest"]) or digest != source.content_digest:
        raise WorkspaceError(
            code="CLUE_SOURCE_STALE_CONFLICT",
            where="clues.design",
            why="所选模块条目与保存的版本指纹不一致",
            fix="重新读取精确修订后选择区间；不要用库最新版替代旧关联",
        )
    _checked_range(text, start, end, quote)
    return source, binding.id


def create_clue_identity(
    store: ClueLedgerStore,
    *,
    title: str,
    request_key: str,
    expected_revision: str,
    created_by: str = "author",
) -> dict[str, JsonValue]:
    """从零建立线索身份；不写贡献、覆盖或披露，也不伪造埋设章。"""
    clean = title.strip()
    if not clean:
        raise _scope_error("线索标题不能为空", "给出一个作者能辨认的计划标题")
    if len(clean) > 200:
        raise _scope_error("线索标题过长", "把标题缩短到 200 字以内")

    def apply(ledger: ClueLedger) -> dict[str, JsonValue]:
        clue_id = new_id("clue")
        revision = new_id("rev")
        ledger.clues[clue_id] = ClueIdentity(
            clue_id=clue_id,
            title=clean,
            clue_revision=revision,
            created_at=_now(),
            created_by=created_by,
            first_chapter=None,
        )
        return {"clue_id": clue_id, "clue_revision": revision, "title": clean}

    return store.mutate(
        expected_revision=expected_revision,
        request_key=request_key,
        kind="create_clue",
        payload={"title": clean},
        apply=apply,
    )


def link_clue_design(
    workspace: Workspace,
    store: ClueLedgerStore,
    *,
    clue_id: str,
    source: DesignSource,
    start: int,
    end: int,
    quote: str,
    relation: str,
    request_key: str,
    expected_revision: str,
    created_by: str,
    library: ModuleLibrary | None = None,
    assembly: Assembly | None = None,
) -> dict[str, JsonValue]:
    """把作者计划条目的实际区间关联到线索；区间不成立就不进入事务。"""
    checked, binding_id = validate_design_source(
        workspace,
        source,
        start=start,
        end=end,
        quote=quote,
        library=library,
        assembly=assembly,
    )

    def preflight() -> None:
        validate_design_source(
            workspace,
            checked,
            start=start,
            end=end,
            quote=quote,
            library=library,
            assembly=assembly,
        )

    def apply(ledger: ClueLedger) -> dict[str, JsonValue]:
        identity = ledger.clues.get(clue_id)
        if identity is None:
            raise _not_found("要关联计划的线索不存在")
        existing = next(
            (
                row
                for row in ledger.design_links.values()
                if row.clue_id == clue_id
                and row.source == checked
                and (row.start, row.end, row.quote) == (start, end, quote)
            ),
            None,
        )
        if existing is not None:
            return {
                "link_id": existing.link_id,
                "clue_id": clue_id,
                "clue_revision": identity.clue_revision,
                "duplicate": True,
            }
        link_id = new_id("link")
        ledger.design_links[link_id] = ClueDesignLink(
            link_id=link_id,
            clue_id=clue_id,
            source=checked,
            binding_id=binding_id,
            start=start,
            end=end,
            quote=quote,
            relation=relation.strip()[:500],
            created_at=_now(),
            created_by=created_by or "author",
            created_revision=ledger.ledger_revision,
        )
        revision = new_id("rev")
        identity.clue_revision = revision
        return {
            "link_id": link_id,
            "clue_id": clue_id,
            "clue_revision": revision,
            "duplicate": False,
        }

    return store.mutate(
        expected_revision=expected_revision,
        request_key=request_key,
        kind="link_clue_design",
        payload={
            "clue_id": clue_id,
            "source": checked.model_dump(mode="json"),
            "start": start,
            "end": end,
            "quote": quote,
            "relation": relation.strip()[:500],
        },
        apply=apply,
        preflight=preflight,
    )


def unlink_clue_design(
    store: ClueLedgerStore,
    *,
    clue_id: str,
    link_id: str,
    request_key: str,
    expected_revision: str,
) -> dict[str, JsonValue]:
    """移除计划关联；模块原文与正文都不改动，引用它的目标同步去掉该依据。"""

    def apply(ledger: ClueLedger) -> dict[str, JsonValue]:
        identity = ledger.clues.get(clue_id)
        link = ledger.design_links.get(link_id)
        if identity is None or link is None or link.clue_id != clue_id:
            raise _not_found("要移除的计划关联不存在")
        del ledger.design_links[link_id]
        touched: set[str] = {clue_id}
        for inference_id, row in ledger.inferences.items():
            if link_id not in row.design_link_ids and not any(
                link_id in item.design_link_ids for item in (*row.premises, *row.alternatives)
            ):
                continue
            row.design_link_ids = [item for item in row.design_link_ids if item != link_id]
            for item in (*row.premises, *row.alternatives):
                item.design_link_ids = [value for value in item.design_link_ids if value != link_id]
            row.inference_revision = new_id("rev")
            row.updated_at = _now()
            touched.update(row.clue_ids)
            ledger.inferences[inference_id] = row
        for identifier in touched:
            ledger.clues[identifier].clue_revision = new_id("rev")
        return {
            "link_id": link_id,
            "clue_id": clue_id,
            "clue_revision": ledger.clues[clue_id].clue_revision,
        }

    return store.mutate(
        expected_revision=expected_revision,
        request_key=request_key,
        kind="unlink_clue_design",
        payload={"clue_id": clue_id, "link_id": link_id},
        apply=apply,
    )


def _statements(items: Sequence[InferenceStatement]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for item in items:
        rows.append(
            {
                "text": item.text.strip(),
                "clue_ids": list(item.clue_ids),
                "design_link_ids": list(item.design_link_ids),
            }
        )
    return rows


def save_clue_inference(
    store: ClueLedgerStore,
    *,
    clue_ids: Sequence[str],
    goal: str,
    intent: InferenceIntent,
    premises: Sequence[InferenceStatement],
    alternatives: Sequence[InferenceStatement],
    design_link_ids: Sequence[str],
    request_key: str,
    expected_revision: str,
    inference_id: str | None = None,
    inference_revision: str | None = None,
    created_by: str = "author",
    request_source: str = "",
) -> dict[str, JsonValue]:
    """创建或更新一个推理目标；目标、前提与解释保存为结构化关系。"""
    clean = goal.strip()
    if not clean:
        raise _scope_error("推理目标不能为空", "写一句这次要推出的结论")
    if not 1 <= len(clue_ids) <= 10:
        raise _scope_error("一个推理目标要关联 1—10 条线索", "把较大的推断拆成有明确目标的步骤")
    if len(set(clue_ids)) != len(clue_ids):
        raise _scope_error("同一推理不能重复关联同一条线索", "去掉重复的线索引用")
    try:
        premise_rows = [
            InferenceStatement(
                text=item.text,
                clue_ids=item.clue_ids,
                design_link_ids=item.design_link_ids,
            )
            for item in premises
        ]
        alternative_rows = [
            InferenceStatement(
                text=item.text,
                clue_ids=item.clue_ids,
                design_link_ids=item.design_link_ids,
            )
            for item in alternatives
        ]
    except ValidationError as exc:
        raise _scope_error("前提或其他解释的结构不完整", "每条都写一句具体判断") from exc
    if len(premise_rows) > 10 or len(alternative_rows) > 10:
        raise _scope_error("前提或其他解释超过 10 条", "先保留与目标直接相关的条目")

    def apply(ledger: ClueLedger) -> dict[str, JsonValue]:
        missing = [item for item in clue_ids if item not in ledger.clues]
        if missing:
            raise _not_found(f"推理目标关联了不存在的线索：{missing[0]}")
        if not set(design_link_ids) <= set(ledger.design_links):
            raise _not_found("推理目标引用了不存在的计划关联")
        for link_id in design_link_ids:
            if ledger.design_links[link_id].clue_id not in clue_ids:
                raise _scope_error(
                    "计划依据不属于这个推理目标的线索",
                    "只引用目标线索自己的计划关联，或把该线索加入目标",
                )
        for statement in (*premise_rows, *alternative_rows):
            if not set(statement.clue_ids) <= set(clue_ids):
                raise _scope_error(
                    "前提或其他解释引用了目标之外的线索",
                    "只引用本次目标里的线索，或先把该线索加入目标",
                )
            if not set(statement.design_link_ids) <= set(design_link_ids):
                raise _scope_error(
                    "前提或其他解释引用了目标之外的计划依据",
                    "只引用本目标的计划关联",
                )
        now = _now()
        if inference_id:
            row = ledger.inferences.get(inference_id)
            if row is None:
                raise _not_found("要更新的推理目标不存在")
            if inference_revision is not None and row.inference_revision != inference_revision:
                raise _revision_conflict("推理目标在读取后已经变化")
            touched = set(row.clue_ids) | set(clue_ids)
            created_at, created_source = row.created_at, row.created_by
        else:
            identifier = new_id("inference")
            row = ClueInference(
                inference_id=identifier,
                inference_revision=new_id("rev"),
                goal=clean,
                intent=intent,
                clue_ids=list(clue_ids),
                created_at=now,
                updated_at=now,
                created_by=created_by or "author",
            )
            touched = set(clue_ids)
            created_at, created_source = now, created_by or "author"
        identifier = row.inference_id
        row = ClueInference(
            inference_id=identifier,
            inference_revision=new_id("rev"),
            goal=clean,
            intent=intent,
            clue_ids=list(clue_ids),
            premises=premise_rows,
            alternatives=alternative_rows,
            design_link_ids=list(design_link_ids),
            created_at=created_at,
            updated_at=now,
            created_by=created_source,
            request_source=request_source.strip()[:500],
        )
        ledger.inferences[identifier] = row
        for clue_id in touched:
            ledger.clues[clue_id].clue_revision = new_id("rev")
        return {
            "inference_id": identifier,
            "inference_revision": row.inference_revision,
            "clue_ids": list(row.clue_ids),
            "updated": bool(inference_id),
        }

    return store.mutate(
        expected_revision=expected_revision,
        request_key=request_key,
        kind="save_clue_inference",
        payload={
            "inference_id": inference_id,
            "inference_revision": inference_revision,
            "clue_ids": list(clue_ids),
            "goal": clean,
            "intent": intent,
            "premises": _statements(premise_rows),
            "alternatives": _statements(alternative_rows),
            "design_link_ids": list(design_link_ids),
            "request_source": request_source.strip()[:500],
        },
        apply=apply,
    )

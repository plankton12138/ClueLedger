"""正文命令（T14.1）：导入切分、应用 Edit Operation、完整文本兜底。

命令是**纯函数** `fn(catalog, new_id, ...) -> ManuscriptChange`，不触达
Repository；唯一写路径是应用层 handler（读 Snapshot → 执行命令 → Commit）。
硬不变量在命令层拒绝（ManuscriptViolation）：引用越界、越界偏移、同一提交内
重复改动同一段落（重叠）。

顺序由章的 `text_unit_ids` 显式持有：结构性编辑（插入/删除/拆分/合并/移动）
只改写章的这一个字段；纯替换只改写目标段落。删除与合并产生的旧段落进入
`deleted` 并保留 Artifact 与谱系（Repository 不变量 1 不允许删除 Revision）。

Agent Patch（T14.4）复用同一批操作与校验：提案只登记 hunk 与所基于的章基础，
正文变化只经作者显式接受发生——「Agent Patch 无法直接提交」是命令层的硬约束，
不是调用方的约定。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace as _dataclass_replace
from difflib import SequenceMatcher

from hastings.domain.manuscript import diff, kinds
from hastings.domain.manuscript import error_codes as ec
from hastings.domain.manuscript import operations as ops_mod
from hastings.domain.manuscript.catalog import (
    ManuscriptCatalog,
    ManuscriptEntry,
    content_base,
)
from hastings.domain.manuscript.errors import violation
from hastings.domain.manuscript.models import (
    EVENT_CHAPTER_COMMITTED,
    HUNK_ACCEPTED,
    HUNK_REJECTED,
    ORIGIN_EDIT,
    ORIGIN_IMPORT,
    PATCH_SOURCE_AGENT,
    STATE_DELETED,
    Chapter,
    ManuscriptChange,
    ManuscriptPatchProposal,
    PatchHunk,
    TextUnit,
)
from hastings.domain.manuscript.schema import require_str
from hastings.domain.repository.ids import ArtifactId, RevisionId
from hastings.domain.repository.requests import ArtifactChange

__all__ = [
    "NewId",
    "accept_patch_hunks",
    "apply_edits",
    "import_chapter",
    "propose_patch",
    "reject_patch",
    "save_full_text",
]

# new_id：由 kind 生成一个未被占用的新 ArtifactId（application 侧接 IdGenerator 端口）
NewId = Callable[[str], str]

_OPS_FIELD = "operations"


def import_chapter(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    chapter_id: str,
    title: str,
    text: str,
    order_key: str = "0001",
    narrative_chapter_id: str | None = None,
) -> ManuscriptChange:
    """用确定性的段落切分建立初始 TextUnit；空文本只建立章（合法）。"""
    where = "manuscript.import_chapter"
    title = require_str(title, field="title", where=where)
    if catalog.get(chapter_id) is not None:
        raise violation(
            ec.MANUSCRIPT_DUPLICATE_ID,
            where,
            f"章 ID {chapter_id!r} 已存在，不能覆盖既有对象",
            "换一个未被占用的章 ID；导入与修订都必须产生新身份",
            chapter_id=chapter_id,
        )

    unit_ids: list[str] = []
    unit_changes: list[ArtifactChange] = []
    for paragraph in diff.split_paragraphs(text):
        unit_id = new_id("tu")
        unit_ids.append(unit_id)
        unit = TextUnit(
            unit_id=unit_id, chapter_id=chapter_id, text=paragraph, origin=ORIGIN_IMPORT
        )
        unit_changes.append(_create(unit_id, kinds.KIND_TEXT_UNIT, unit.to_content()))

    chapter = Chapter(
        chapter_id=chapter_id,
        title=title,
        order_key=order_key,
        text_unit_ids=tuple(unit_ids),
        narrative_chapter_id=narrative_chapter_id,
    )
    changes = [_create(chapter_id, kinds.KIND_CHAPTER, chapter.to_content())]
    changes.extend(unit_changes)
    return ManuscriptChange(
        artifact_changes=tuple(changes), summary=f"导入「{title}」：{len(unit_ids)} 段"
    )


def apply_edits(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    chapter_id: str,
    operations: tuple[ops_mod.EditOperation, ...],
    expected_chapter_base: str | None = None,
) -> ManuscriptChange:
    """在章的当前 head 上应用一批编辑操作（作者保存与 Agent Patch 共用）。"""
    where = "manuscript.apply_edits"
    entry = catalog.require_chapter(chapter_id, where=where)
    _check_expected_base(catalog, entry, expected_chapter_base, where=where)
    return _apply(
        catalog,
        new_id,
        chapter=entry.model(),
        chapter_entry=entry,
        operations=tuple(operations),
        where=where,
    )


def save_full_text(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    chapter_id: str,
    text: str,
    expected_chapter_base: str | None = None,
) -> ManuscriptChange:
    """兼容完整文本提交：服务器计算段落 diff 并规范化为 Edit Operations。"""
    where = "manuscript.save_full_text"
    entry = catalog.require_chapter(chapter_id, where=where)
    _check_expected_base(catalog, entry, expected_chapter_base, where=where)

    current: list[TextUnit] = [u.model() for u in catalog.units_of(chapter_id)]
    operations = _operations_from_text(
        [unit.unit_id for unit in current],
        [unit.text for unit in current],
        diff.split_paragraphs(text),
    )
    return _apply(
        catalog,
        new_id,
        chapter=entry.model(),
        chapter_entry=entry,
        operations=operations,
        where=where,
    )


# ---------------------------------------------------------------------------
# Agent Patch（T14.4）：提案登记 / 逐 hunk 接受 / 拒绝
# ---------------------------------------------------------------------------
def propose_patch(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    chapter_id: str,
    operations: tuple[ops_mod.EditOperation, ...],
    source: str = PATCH_SOURCE_AGENT,
) -> ManuscriptChange:
    """登记 Patch 提案：只记录候选编辑与所基于的章基础，**不改动正文**。

    「Agent Patch 无法直接提交」（计划 §8）：本命令创建提案 Artifact（一次提交），
    但它只落 hunk 及其状态；正文段落与章只有经作者显式 ``accept_patch_hunks`` 才
    会变化。范围硬约束在此拒绝：每个 hunk 引用的段落必须都在**本章**的活动段落中
    （越界抛 ``MANUSCRIPT_PATCH_SCOPE``），否则 Agent 可借提案旁改他章。
    """
    where = "manuscript.propose_patch"
    catalog.require_chapter(chapter_id, where=where)
    allowed = {unit.artifact_id for unit in catalog.units_of(chapter_id)}
    _check_patch_scope(operations, allowed=allowed, chapter_id=chapter_id, where=where)
    _reject_overlap(operations, where=where)

    hunks = tuple(
        PatchHunk(
            hunk_id=ops_mod.normalize_operation(operation).hunk_id,
            operation=ops_mod.operation_to_content(operation),
        )
        for operation in operations
    )
    proposal = ManuscriptPatchProposal(
        proposal_id=new_id("art"),
        chapter_id=chapter_id,
        base=catalog.chapter_base(chapter_id),
        hunks=hunks,
        source=source,
    )
    return ManuscriptChange(
        artifact_changes=(
            _create(proposal.proposal_id, kinds.KIND_PATCH_PROPOSAL, proposal.to_content()),
        ),
        summary=f"登记 Patch 提案：{len(hunks)} 个 hunk",
    )


def accept_patch_hunks(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    proposal_id: str,
    hunk_ids: tuple[str, ...],
) -> ManuscriptChange:
    """接受提案中的**部分** hunk：应用选中操作并回写提案状态，未选中的仍是 pending。

    乐观并发对比「本提案已接受后的基础」（``applied_base``，首次为登记 ``base``）：
    他处编辑移动了真实基础将得到 ``MANUSCRIPT_BASE_CONFLICT``——Patch 不得静默落在
    已移动的基础上；而本提案自己接受造成的移动不算外部冲突，剩余 hunk 可继续逐个
    接受（评审 #4）。未接受的 hunk 与其状态留在提案 Artifact 里（内容版本化），
    后续可继续接受或拒绝。
    """
    where = "manuscript.accept_patch_hunks"
    proposal_entry = catalog.require_proposal(proposal_id, where=where)
    proposal: ManuscriptPatchProposal = proposal_entry.model()
    selected = _select_pending_hunks(proposal, hunk_ids, where=where, action="接受")
    chapter_entry = catalog.require_chapter(proposal.chapter_id, where=where)
    # 乐观并发对比「本提案自己已接受后的基础」：未接受过任何 hunk 时等于登记基础。
    # 本提案造成的移动记在 applied_base 里（每次接受原子回写），不算外部冲突；
    # 他处编辑移动了真实基础才会得到 MANUSCRIPT_BASE_CONFLICT（评审 #4）。
    _check_expected_base(
        catalog, chapter_entry, proposal.applied_base or proposal.base, where=f"{where}.base"
    )

    applied = _apply(
        catalog,
        new_id,
        chapter=chapter_entry.model(),
        chapter_entry=chapter_entry,
        operations=tuple(_operation_of(hunk, where=where) for hunk in selected),
        where=f"{where}.hunks",
    )
    accepted = _mark_hunks(proposal, {hunk.hunk_id for hunk in selected}, HUNK_ACCEPTED)
    revised_proposal = _dataclass_replace(accepted, applied_base=applied.chapter_base_after)
    return ManuscriptChange(
        artifact_changes=(
            *applied.artifact_changes,
            _revise(proposal_entry, revised_proposal.to_content()),
        ),
        hunks=applied.hunks,
        events=applied.events,
        summary=f"接受 {len(selected)}/{len(proposal.hunks)} 个 hunk",
    )


def reject_patch(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    proposal_id: str,
    hunk_ids: tuple[str, ...] | None = None,
    note: str = "",
) -> ManuscriptChange:
    """拒绝提案中的 hunk（省略 ``hunk_ids`` 表示拒绝全部未决 hunk）；不改正文。"""
    where = "manuscript.reject_patch"
    del new_id  # 拒绝只改写提案状态，不产生新对象
    proposal_entry = catalog.require_proposal(proposal_id, where=where)
    proposal: ManuscriptPatchProposal = proposal_entry.model()
    if hunk_ids is None:
        selected = proposal.pending_hunks()
    else:
        selected = _select_pending_hunks(proposal, hunk_ids, where=where, action="拒绝")
    rejected = _mark_hunks(proposal, {hunk.hunk_id for hunk in selected}, HUNK_REJECTED, note=note)
    if rejected.hunks == proposal.hunks:
        return ManuscriptChange(summary="提案没有可拒绝的未决 hunk（合法空结果）")
    return ManuscriptChange(
        artifact_changes=(_revise(proposal_entry, rejected.to_content()),),
        summary=f"拒绝 {len(selected)}/{len(proposal.hunks)} 个 hunk",
    )


def _check_patch_scope(
    operations: tuple[ops_mod.EditOperation, ...],
    *,
    allowed: set[str],
    chapter_id: str,
    where: str,
) -> None:
    """提案的每个引用都必须落在本章活动段落内（范围硬约束，四透镜②）。"""
    for operation in operations:
        for unit_id in _referenced_unit_ids(operation):
            if unit_id not in allowed:
                raise violation(
                    ec.MANUSCRIPT_PATCH_SCOPE,
                    f"{where}.{_OPS_FIELD}",
                    f"Patch 引用的段落 {unit_id!r} 不在章 {chapter_id!r} 的活动段落中",
                    "提案只能引用本章活动段落；跨章修改请单独为该章提案",
                    chapter_id=chapter_id,
                    unit_id=unit_id,
                    allowed_unit_ids=sorted(allowed),
                )


def _referenced_unit_ids(operation: ops_mod.EditOperation) -> tuple[str, ...]:
    ids = list(ops_mod.modified_unit_ids(operation))
    if isinstance(operation, ops_mod.Insert | ops_mod.Move) and operation.after_unit_id is not None:
        ids.append(operation.after_unit_id)
    return tuple(ids)


def _select_pending_hunks(
    proposal: ManuscriptPatchProposal,
    hunk_ids: tuple[str, ...],
    *,
    where: str,
    action: str,
) -> tuple[PatchHunk, ...]:
    """去重后的 hunk_id 必须都在提案内且仍 pending；否则显式拒绝处理。"""
    ids = tuple(dict.fromkeys(hunk_ids))
    if not ids:
        raise violation(
            ec.MANUSCRIPT_UNKNOWN_HUNK,
            f"{where}.hunk_ids",
            f"没有指定任何要{action}的 hunk",
            "至少指定一个 hunk_id；未决 hunk 列表见提案内容的 hunks 字段",
        )
    by_id = {hunk.hunk_id: hunk for hunk in proposal.hunks}
    selected: list[PatchHunk] = []
    for hunk_id in ids:
        hunk = by_id.get(hunk_id)
        if hunk is None:
            raise violation(
                ec.MANUSCRIPT_UNKNOWN_HUNK,
                f"{where}.hunk_ids",
                f"hunk {hunk_id!r} 不在提案 {proposal.proposal_id!r} 中",
                "只用该提案 hunks 字段里的 hunk_id",
                hunk_id=hunk_id,
                proposal_id=proposal.proposal_id,
            )
        if not hunk.is_pending:
            raise violation(
                ec.MANUSCRIPT_UNKNOWN_HUNK,
                f"{where}.hunk_ids",
                f"hunk {hunk_id!r} 当前状态是 {hunk.status!r}，不能再次{action}",
                "只处理状态为 pending 的 hunk；已处理 hunk 的状态不再改变",
                hunk_id=hunk_id,
                status=hunk.status,
            )
        selected.append(hunk)
    return tuple(selected)


def _mark_hunks(
    proposal: ManuscriptPatchProposal, hunk_ids: set[str], status: str, *, note: str = ""
) -> ManuscriptPatchProposal:
    hunks = tuple(
        _dataclass_replace(hunk, status=status, note=note or hunk.note)
        if hunk.hunk_id in hunk_ids
        else hunk
        for hunk in proposal.hunks
    )
    return _dataclass_replace(proposal, hunks=hunks)


def _operation_of(hunk: PatchHunk, *, where: str) -> ops_mod.EditOperation:
    try:
        return ops_mod.operation_from_content(hunk.operation)
    except ValueError as exc:
        raise violation(
            ec.MANUSCRIPT_INVALID_OPERATION,
            f"{where}.hunks.{hunk.hunk_id}",
            f"提案中的操作无法还原：{exc}",
            "提案内容应由服务端生成；此错误指向内容损坏，请重建提案",
            hunk_id=hunk.hunk_id,
        ) from exc


# ---------------------------------------------------------------------------
# 内部：应用一批操作
# ---------------------------------------------------------------------------
def _apply(
    catalog: ManuscriptCatalog,
    new_id: NewId,
    *,
    chapter: Chapter,
    chapter_entry: ManuscriptEntry,
    operations: tuple[ops_mod.EditOperation, ...],
    where: str,
) -> ManuscriptChange:
    order: list[str] = list(chapter.text_unit_ids)
    order_before = list(order)
    entries: dict[str, ManuscriptEntry] = {}
    units: dict[str, TextUnit] = {}
    for unit_id in order:
        entry = catalog.unit_entry(unit_id)
        if entry is not None:
            entries[unit_id] = entry
            units[unit_id] = entry.model()

    _reject_overlap(operations, where=where)

    revised: dict[str, TextUnit] = {}
    created: dict[str, TextUnit] = {}
    hunks: list[ops_mod.Hunk] = []
    for operation in operations:
        effective = _apply_one(
            operation,
            order=order,
            units=units,
            revised=revised,
            created=created,
            new_id=new_id,
            chapter_id=chapter.chapter_id,
            where=where,
        )
        if effective:
            hunks.append(ops_mod.normalize_operation(operation))

    changes: list[ArtifactChange] = []
    for unit_id in sorted(set(revised) | set(created)):
        model = created[unit_id] if unit_id in created else revised[unit_id]
        if unit_id in created:
            changes.append(_create(unit_id, kinds.KIND_TEXT_UNIT, model.to_content()))
        else:
            changes.append(_revise(entries[unit_id], model.to_content()))
    if order != order_before:
        new_chapter = _dataclass_replace(chapter, text_unit_ids=tuple(order))
        changes.append(_revise(chapter_entry, new_chapter.to_content()))

    # 应用后的章基础：纯函数在提交前即可确定（内容哈希，见 catalog.content_base），
    # 逐 hunk 接受靠它把「本提案自己造成的移动」原子记回提案（评审 #4）
    pairs = (
        (unit_id, unit.text)
        for unit_id in order
        if (unit := created.get(unit_id) or revised.get(unit_id) or units.get(unit_id)) is not None
    )
    base_after = content_base(chapter.chapter_id, pairs)

    return ManuscriptChange(
        artifact_changes=tuple(changes),
        hunks=tuple(hunks),
        events=((EVENT_CHAPTER_COMMITTED, {"chapter_id": chapter.chapter_id}),),
        summary=f"应用 {len(hunks)} 处编辑",
        chapter_base_after=base_after,
    )


def _apply_one(
    operation: ops_mod.EditOperation,
    *,
    order: list[str],
    units: dict[str, TextUnit],
    revised: dict[str, TextUnit],
    created: dict[str, TextUnit],
    new_id: NewId,
    chapter_id: str,
    where: str,
) -> bool:
    """应用单条操作；返回是否实际生效（False 表示合法 no-op）。"""
    if isinstance(operation, ops_mod.Replace):
        _require_active(operation.unit_id, order, where=where)
        paragraphs = diff.split_paragraphs(operation.text)
        if len(paragraphs) != 1:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.{_OPS_FIELD}",
                f"replace 只能替换单一段落，收到 {len(paragraphs)} 段",
                "跨段修改改用 delete+insert；replace 的 text 不要包含空行",
                unit_id=operation.unit_id,
            )
        current = units[operation.unit_id]
        if current.text == paragraphs[0]:
            return False
        revised[operation.unit_id] = _dataclass_replace(current, text=paragraphs[0])
        return True

    if isinstance(operation, ops_mod.Delete):
        _require_active(operation.unit_id, order, where=where)
        order.remove(operation.unit_id)
        revised[operation.unit_id] = _dataclass_replace(
            units[operation.unit_id], state=STATE_DELETED
        )
        return True

    if isinstance(operation, ops_mod.Insert):
        paragraphs = diff.split_paragraphs(operation.text)
        if not paragraphs:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.{_OPS_FIELD}",
                "insert 的 text 为空（没有可插入的段落）",
                "提供至少一段非空文本；若要删除请用 delete",
            )
        if operation.after_unit_id is None:
            position = 0
        else:
            _require_active(operation.after_unit_id, order, where=where)
            position = order.index(operation.after_unit_id) + 1
        for paragraph in paragraphs:
            unit_id = new_id("tu")
            created[unit_id] = TextUnit(
                unit_id=unit_id,
                chapter_id=chapter_id,
                text=paragraph,
                origin=ORIGIN_EDIT,
            )
            order.insert(position, unit_id)
            position += 1
        return True

    if isinstance(operation, ops_mod.Split):
        _require_active(operation.unit_id, order, where=where)
        current = units[operation.unit_id]
        if not 0 < operation.offset < len(current.text):
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.{_OPS_FIELD}",
                f"split 偏移 {operation.offset} 越界（段落长度 {len(current.text)}）",
                "偏移必须落在 (0, 段落长度) 内；两端都要保留至少一个字符",
                unit_id=operation.unit_id,
                offset=operation.offset,
            )
        revised[operation.unit_id] = _dataclass_replace(
            current, text=current.text[: operation.offset]
        )
        new_unit_id = new_id("tu")
        created[new_unit_id] = TextUnit(
            unit_id=new_unit_id,
            chapter_id=chapter_id,
            text=current.text[operation.offset :],
            origin=ORIGIN_EDIT,
            derived_from=(operation.unit_id,),
        )
        order.insert(order.index(operation.unit_id) + 1, new_unit_id)
        return True

    if isinstance(operation, ops_mod.Merge):
        _require_active(operation.first_unit_id, order, where=where)
        _require_active(operation.second_unit_id, order, where=where)
        first_index = order.index(operation.first_unit_id)
        if order.index(operation.second_unit_id) != first_index + 1:
            raise violation(
                ec.MANUSCRIPT_INVALID_OPERATION,
                f"{where}.{_OPS_FIELD}",
                f"merge 只允许相邻两段（{operation.first_unit_id!r} 与 "
                f"{operation.second_unit_id!r} 不相邻）",
                "先 move 把两段相邻，再 merge；跨段合并会破坏阅读顺序",
                first_unit_id=operation.first_unit_id,
                second_unit_id=operation.second_unit_id,
            )
        first = units[operation.first_unit_id]
        second = units[operation.second_unit_id]
        order.remove(operation.second_unit_id)
        revised[operation.first_unit_id] = _dataclass_replace(
            first,
            text=f"{first.text}\n{second.text}",
            derived_from=(*first.derived_from, operation.second_unit_id),
        )
        revised[operation.second_unit_id] = _dataclass_replace(second, state=STATE_DELETED)
        return True

    # Move
    _require_active(operation.unit_id, order, where=where)
    if operation.after_unit_id == operation.unit_id:
        return False
    before = list(order)
    order.remove(operation.unit_id)
    if operation.after_unit_id is None:
        position = 0
    else:
        _require_active(operation.after_unit_id, order, where=where)
        position = order.index(operation.after_unit_id) + 1
    order.insert(position, operation.unit_id)
    return order != before


def _reject_overlap(operations: tuple[ops_mod.EditOperation, ...], *, where: str) -> None:
    """同一提交内不得重复改动同一段落（重叠），否则无从确定最终文本。"""
    seen: dict[str, str] = {}
    for operation in operations:
        for unit_id in ops_mod.modified_unit_ids(operation):
            if unit_id in seen:
                raise violation(
                    ec.MANUSCRIPT_OVERLAPPING_EDIT,
                    f"{where}.{_OPS_FIELD}",
                    f"段落 {unit_id!r} 被多条操作同时改动"
                    f"（{seen[unit_id]} 与 {type(operation).__name__}）",
                    "合并成一条操作，或分成两次提交；同一提交内同一段落只能改一次",
                    unit_id=unit_id,
                )
            seen[unit_id] = type(operation).__name__


def _require_active(unit_id: str, order: list[str], *, where: str) -> None:
    if unit_id not in order:
        raise violation(
            ec.MANUSCRIPT_UNKNOWN_TEXT_UNIT,
            f"{where}.{_OPS_FIELD}",
            f"引用的段落 {unit_id!r} 不在当前章的活动段落中",
            "改用当前章内存在的段落 ID；已删除或不存在的段落都不能引用",
            unit_id=unit_id,
        )


def _check_expected_base(
    catalog: ManuscriptCatalog, entry: ManuscriptEntry, expected: str | None, *, where: str
) -> None:
    """乐观并发（T14.3）：基础不一致时给显式冲突 + 三方比较所需数据。

    三方比较：base（客户端读到的旧基础）、ours（客户端本次提案）、theirs（当前
    服务端版本）。base 与 ours 在客户端手里，服务端只需补齐 theirs——故 details
    给出当前基础、当前章 Revision、当前整章文本与活动段落 ID，前端据此重新载入、
    手动合并或另存分支，**不自动 force overwrite**。
    """
    if expected is None:
        return
    current = catalog.chapter_base(entry.artifact_id)
    if expected == current:
        return
    units = catalog.units_of(entry.artifact_id)
    raise violation(
        ec.MANUSCRIPT_BASE_CONFLICT,
        f"{where}.expected_chapter_base",
        f"基础版本 {expected!r} 已过期；当前基础为 {current!r}（该章已在他处改动）",
        "重新载入最新版本后再提交，或手动三方合并、另存为新分支；不得基于旧基础覆盖他人修改",
        expected=expected,
        current=current,
        chapter_revision=entry.revision_id,
        current_text="\n\n".join(unit.model().text for unit in units),
        current_unit_ids=[unit.artifact_id for unit in units],
    )


def _operations_from_text(
    old_ids: list[str], old_paragraphs: list[str], new_paragraphs: list[str]
) -> tuple[ops_mod.EditOperation, ...]:
    """把整章文本差异转成显式操作；连续新增段落合并为一条 insert。"""
    matcher = SequenceMatcher(a=old_paragraphs, b=new_paragraphs, autojunk=False)
    result: list[ops_mod.EditOperation] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace":
            paired = min(i2 - i1, j2 - j1)
            for offset in range(paired):
                result.append(ops_mod.Replace(old_ids[i1 + offset], new_paragraphs[j1 + offset]))
            for offset in range(i1 + paired, i2):
                result.append(ops_mod.Delete(old_ids[offset]))
            extras = new_paragraphs[j1 + paired : j2]
            if extras:
                if paired > 0:
                    anchor: str | None = old_ids[i1 + paired - 1]
                elif i1 > 0:
                    anchor = old_ids[i1 - 1]
                else:
                    anchor = None
                result.append(ops_mod.Insert(anchor, "\n\n".join(extras)))
        elif tag == "insert":
            anchor = old_ids[i1 - 1] if i1 > 0 else None
            result.append(ops_mod.Insert(anchor, "\n\n".join(new_paragraphs[j1:j2])))
        else:  # delete
            for offset in range(i1, i2):
                result.append(ops_mod.Delete(old_ids[offset]))
    return tuple(result)


def _create(artifact_id: str, kind: str, content: dict[str, object]) -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId(artifact_id), content=content, old_revision_id=None, kind=kind
    )


def _revise(entry: ManuscriptEntry, content: dict[str, object]) -> ArtifactChange:
    return ArtifactChange(
        artifact_id=ArtifactId(entry.artifact_id),
        content=content,
        old_revision_id=RevisionId(entry.revision_id),
        kind=None,
    )

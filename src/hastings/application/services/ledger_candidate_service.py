"""设定候选成组与部分确认（V03-3.2）：暂存、挑选、一次入账、真实版本回读。

候选是「还没确认的设定」，存在仓库辅助表里，不产生任何正式版本：

- 暂存时可引用已有条目，也可用 ``@item_id`` 引用同组条目（如关系引用尚未
  保存的两个人）；
- 确认时只提交选中的条目，一次原子 Commit；未选中的继续留在组里；
- 依赖未闭合（引用的组内条目既未选中也未确认）时拒绝，不造悬空关系；
- 标记为「假设」的条目不进入正式账本，确认时显式拒绝；
- 条目 ID 由 (group_id, item_id) 确定性派生，提交成功但响应丢失时重试能
  认出同一次结果，不会重复入账。

模型整理的候选（构思/正文建议）复用同一套确认规则，生产入口归 V03-4.4。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from hastings.application.errors import ViolationError
from hastings.application.ports.ids import IdGenerator
from hastings.application.queries.ledger import entry_view
from hastings.application.services.commit_service import CommitService
from hastings.application.services.revision_links import record_acceptance
from hastings.domain.manuscript.catalog import ManuscriptCatalog
from hastings.domain.manuscript.errors import ManuscriptViolation
from hastings.domain.repository.ids import Actor, ArtifactId, CommitId, ProjectId
from hastings.domain.repository.requests import ArtifactChange, CommitRequest, RelationChange
from hastings.domain.repository.serialization import content_hash
from hastings.domain.story import error_codes as story_ec
from hastings.domain.story.canon import entries as ledger_entries
from hastings.domain.story.catalog import StoryCatalog, StoryEntry
from hastings.domain.story.errors import StoryViolation

__all__ = ["CANDIDATE_BUCKET", "LedgerCandidateService"]

CANDIDATE_BUCKET = "ledger_staged"
GROUP_KEY = "group"

_AUTHOR = Actor(kind="human", id="author")

PENDING = "pending"
ACCEPTED = "accepted"
SKIPPED = "skipped"


def _violation(code: str, where: str, why: str, fix: str, **details: Any) -> ViolationError:
    return ViolationError(code=code, where=where, why=why, fix=fix, details=details)


def _as_violation(exc: StoryViolation) -> ViolationError:
    code = "LEDGER_ENTRY_CONFLICT" if exc.code == story_ec.STORY_STALE_REVISION else exc.code
    return ViolationError(code=code, where=exc.where, why=exc.why, fix=exc.fix, details=exc.details)


def _deterministic_id(group_id: str, item_id: str) -> str:
    digest = content_hash({"group": group_id, "item": item_id})
    return f"art_{digest[:16]}"


def _ref_values(fields: Mapping[str, Any], kind: str) -> list[str]:
    refs: list[str] = []
    for field in ledger_entries.LEDGER_REF_FIELDS.get(kind, {}):
        value = fields.get(field)
        if not value:
            continue
        refs.extend([str(value)] if isinstance(value, str) else [str(v) for v in value])
    return refs


class LedgerCandidateService:
    def __init__(self, repo: CommitService, ids: IdGenerator, clock: Any) -> None:
        self._repo = repo
        self._ids = ids
        self._clock = clock

    # -- 读 -----------------------------------------------------------------
    def view(self, project_id: str) -> dict[str, Any]:
        pid = ProjectId(project_id)
        group = self._load(pid)
        return self._group_view(group) if group is not None else self._empty_view(pid)

    # -- 暂存 / 跳过 ---------------------------------------------------------
    def stage(
        self,
        project_id: str,
        *,
        kind: str,
        fields: dict[str, Any],
        hypothesis: bool,
        request_id: str,
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        head = self._head(pid)
        catalog = self._catalog(pid, head)
        group = self._load(pid) or self._empty_group(pid)
        if request_id in group["requests"]:
            return self._group_view(group)
        items_by_id = {item["item_id"]: item for item in group["items"]}
        try:
            clean = ledger_entries.validate_fields(
                kind,
                fields,
                ref_lookup=self._lookup(catalog, items_by_id),
                cmd="StageLedgerCandidate",
            )
        except StoryViolation as exc:
            raise _as_violation(exc) from exc
        item = {
            "item_id": self._ids.new_id("lci"),
            "kind": kind,
            "fields": clean,
            "hypothesis": bool(hypothesis),
            "status": PENDING,
            "accepted_entry_id": None,
            "accepted_revision_id": None,
            "created_at": self._now(),
        }

        def mutate(current: dict[str, Any] | None) -> dict[str, Any] | None:
            base = dict(current) if current is not None else self._empty_group(pid)
            if request_id in base["requests"]:
                return current
            return {
                **base,
                "items": [*base["items"], item],
                "revision": int(base["revision"]) + 1,
                "requests": {
                    **base["requests"],
                    request_id: {"action": "stage", "item_ids": [item["item_id"]]},
                },
                "updated_at": self._now(),
            }

        self._repo.aux_mutate(pid, CANDIDATE_BUCKET, GROUP_KEY, mutate)
        return self.view(project_id)

    def stage_suggestions(
        self,
        project_id: str,
        *,
        origin: dict[str, Any],
        items: list[dict[str, Any]],
        request_id: str,
    ) -> dict[str, Any]:
        """一次暂存整组建议（V03-4.4）：一条辅助版本，不逐条递增。

        ``items`` 每项含 ``kind``/``fields``/``source``/``hypothesis``；字段与引用
        仍走账本同一套校验——建议不因为来自模型就绕过类型规则。
        """
        pid = ProjectId(project_id)
        head = self._head(pid)
        catalog = self._catalog(pid, head)
        group = self._load(pid) or self._empty_group(pid)
        if request_id in group["requests"]:
            return self._group_view(group)
        lookup = self._lookup(catalog, {i["item_id"]: i for i in group["items"]})
        staged: list[dict[str, Any]] = []
        for raw in items:
            try:
                clean = ledger_entries.validate_fields(
                    str(raw["kind"]),
                    dict(raw["fields"]),
                    ref_lookup=lookup,
                    cmd="StageLedgerSuggestion",
                )
            except StoryViolation as exc:
                raise _as_violation(exc) from exc
            staged.append(
                {
                    "item_id": self._ids.new_id("lci"),
                    "kind": str(raw["kind"]),
                    "fields": clean,
                    "hypothesis": bool(raw.get("hypothesis", False)),
                    "source": dict(raw.get("source") or {}),
                    "status": PENDING,
                    "accepted_entry_id": None,
                    "accepted_revision_id": None,
                    "created_at": self._now(),
                }
            )

        def mutate(current: dict[str, Any] | None) -> dict[str, Any] | None:
            base = dict(current) if current is not None else self._empty_group(pid)
            if request_id in base["requests"]:
                return current
            return {
                **base,
                "items": [*base["items"], *staged],
                "origin": dict(origin),
                "revision": int(base["revision"]) + 1,
                "requests": {
                    **base["requests"],
                    request_id: {
                        "action": "stage_suggestions",
                        "item_ids": [item["item_id"] for item in staged],
                    },
                },
                "updated_at": self._now(),
            }

        updated = self._repo.aux_mutate(pid, CANDIDATE_BUCKET, GROUP_KEY, mutate)
        return self._group_view(updated or group)

    def skip(
        self,
        project_id: str,
        *,
        item_ids: list[str],
        expected_revision: int,
        request_id: str,
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        group = self._require_group(pid)
        if not item_ids:
            raise _violation(
                "LEDGER_CANDIDATE_EMPTY_SELECTION",
                f"ledger.candidates.skip({pid.value})",
                "没有选中任何候选条目",
                "至少选中一条要暂不处理的设定",
                group_id=group["group_id"],
            )
        if request_id in group["requests"]:
            return self._group_view(group)
        self._require_revision(group, expected_revision)
        pending = self._pending(group, item_ids)
        target = {item["item_id"] for item in pending}

        def mutate(current: dict[str, Any] | None) -> dict[str, Any] | None:
            base = dict(current) if current is not None else group
            if request_id in base["requests"]:
                return current
            items = [
                {**item, "status": SKIPPED} if item["item_id"] in target else item
                for item in base["items"]
            ]
            return {
                **base,
                "items": items,
                "revision": int(base["revision"]) + 1,
                "requests": {
                    **base["requests"],
                    request_id: {"action": "skip", "item_ids": sorted(target)},
                },
                "updated_at": self._now(),
            }

        updated = self._repo.aux_mutate(pid, CANDIDATE_BUCKET, GROUP_KEY, mutate) or group
        return self._group_view(updated)

    # -- 确认 -----------------------------------------------------------------
    def accept(
        self,
        project_id: str,
        *,
        item_ids: list[str],
        expected_revision: int,
        request_id: str,
        source_operation_id: str | None = None,
    ) -> dict[str, Any]:
        pid = ProjectId(project_id)
        group = self._require_group(pid)
        item_ids = list(dict.fromkeys(item_ids))
        if not item_ids:
            raise _violation(
                "LEDGER_CANDIDATE_EMPTY_SELECTION",
                f"ledger.candidates.accept({pid.value})",
                "没有选中任何候选条目",
                "至少选中一条要确认的设定；暂不处理的条目用「暂不处理」",
                group_id=group["group_id"],
            )
        recorded = group["requests"].get(request_id)
        if recorded is not None:
            return self._replay(pid, group, recorded)
        key = f"ledger-candidates:{pid.value}:{request_id}"
        prior = self._repo.find_commit_result(pid, key)
        if prior is not None and prior.commit_id is not None:
            # 上次提交成功但组状态未落盘：按确定性 ID 认回同一次结果。
            return self._recover(
                pid, group, prior.commit_id, request_id, prior.changed_artifact_ids
            )
        self._require_revision(group, expected_revision)

        by_id = {item["item_id"]: item for item in group["items"]}
        selected_ids = set(item_ids)
        for item_id in selected_ids:
            if item_id not in by_id:
                raise _violation(
                    "LEDGER_CANDIDATE_ITEM_NOT_FOUND",
                    f"ledger.candidates.accept({pid.value})",
                    f"候选组里没有条目 {item_id!r}",
                    "刷新待确认列表后重新选择",
                    item_id=item_id,
                )
        selected = [by_id[item_id] for item_id in item_ids]
        for item in selected:
            if item["status"] != PENDING:
                raise _violation(
                    "LEDGER_CANDIDATE_ITEM_NOT_PENDING",
                    f"ledger.candidates.accept({pid.value})",
                    f"条目 {item['item_id']!r} 的状态是 {item['status']}，不能再次确认",
                    "刷新待确认列表；已确认或已跳过的条目不再变动",
                    item_id=item["item_id"],
                    status=item["status"],
                )
            if item["hypothesis"]:
                raise _violation(
                    "LEDGER_HYPOTHESIS_NOT_ACCEPTABLE",
                    f"ledger.candidates.accept({pid.value})",
                    f"条目 {item['item_id']!r} 标记为假设，不能进入正式账本",
                    "先把它改成已确认的设定再保存；不确定的内容留在候选里继续讨论",
                    item_id=item["item_id"],
                )

        ordered = self._order(group, selected, by_id)
        # 来源核对（V03-4.4）：建议带生成时的章基础版本，正文变了就不能原样采用。
        self._assert_sources_current(pid, selected)
        branch = self._repo.get_branch(pid, "main")
        if branch is None:
            raise _violation(
                "REPO_BRANCH_NOT_FOUND",
                f"ledger.candidates.accept({pid.value})",
                f"作品 {pid.value} 没有 main 分支",
                "先创建作品，再确认设定候选",
                project_id=pid.value,
            )
        head = branch.head_commit_id
        if source_operation_id is not None:
            # 因果关联只指向真实存在的整体操作；不存在的 ID 不落库，避免假链。
            record_view = (
                self._repo.get_artifact(ArtifactId(f"art_{source_operation_id}"), head)
                if head is not None
                else None
            )
            if record_view is None:
                raise _violation(
                    "REVISION_OPERATION_NOT_FOUND",
                    f"ledger.candidates.accept({pid.value})",
                    f"引用的整体操作 {source_operation_id!r} 不存在",
                    "刷新本次整体调整的结果后再采用建议；或去掉来源操作，按独立采用处理",
                    operation_id=source_operation_id,
                )
        catalog = self._catalog(pid, head)
        created: list[str] = []
        changes: list[ArtifactChange] = []
        relations: list[RelationChange] = []
        working = catalog
        for item in ordered:
            artifact_id = _deterministic_id(group["group_id"], item["item_id"])
            fields = self._resolve(item, by_id, group["group_id"], selected_ids)

            def new_id(_kind: str, target: str = artifact_id) -> str:
                return target

            try:
                change = ledger_entries.create_entry(
                    working,
                    new_id,
                    kind=item["kind"],
                    fields=fields,
                )
            except StoryViolation as exc:
                raise _as_violation(exc) from exc
            artifact_change = change.artifact_changes[0]
            changes.append(artifact_change)
            relations.extend(change.relation_changes)
            working = replace(
                working,
                entries=(
                    *working.entries,
                    StoryEntry(
                        artifact_id=artifact_id,
                        kind=item["kind"],
                        revision_id="pending",
                        content=dict(artifact_change.content),
                    ),
                ),
            )
            created.append(artifact_id)

        result = self._repo.commit(
            CommitRequest(
                project_id=pid,
                branch_id=branch.id,
                expected_head=head,
                actor=_AUTHOR,
                idempotency_key=key,
                summary=f"确认设定候选：{len(ordered)} 条",
                changes=tuple(changes),
                relations=tuple(relations),
                events=(
                    (
                        "ledger.candidates.accepted",
                        {
                            "entry_ids": created,
                            "group_id": group["group_id"],
                            "source_operation_id": source_operation_id,
                        },
                    ),
                ),
            )
        )
        commit_id = result.commit_id
        if commit_id is None:
            return self._group_view(group)

        entries = [self._entry_at(pid, commit_id, artifact_id) for artifact_id in created]
        accepted = {
            item["item_id"]: artifact_id for item, artifact_id in zip(ordered, created, strict=True)
        }
        self._mark_accepted(pid, accepted, request_id, commit_id.value, entries)
        if source_operation_id is not None:
            record_acceptance(
                self._repo,
                project_id,
                source_operation_id,
                commit_id=commit_id.value,
                request_id=request_id,
                item_ids=sorted(accepted),
                at=self._clock.now().isoformat(),
            )
        return {
            "status": "committed",
            "commit_id": commit_id.value,
            "entries": entries,
            "commit_item_ids": sorted(accepted),
            "group": self.view(project_id),
        }

    # -- 内部 -----------------------------------------------------------------
    def _mark_accepted(
        self,
        pid: ProjectId,
        accepted: Mapping[str, str],
        request_id: str,
        commit_id: str,
        entries: list[dict[str, Any]],
    ) -> None:
        revisions = {entry["id"]: entry["revision_id"] for entry in entries}

        def mutate(current: dict[str, Any] | None) -> dict[str, Any] | None:
            base = dict(current) if current is not None else None
            if base is None:
                return None
            if request_id in base["requests"]:
                return current
            items = [
                (
                    {
                        **item,
                        "status": ACCEPTED,
                        "accepted_entry_id": accepted[item["item_id"]],
                        "accepted_revision_id": revisions.get(accepted[item["item_id"]]),
                    }
                    if item["item_id"] in accepted
                    else item
                )
                for item in base["items"]
            ]
            return {
                **base,
                "items": items,
                "revision": int(base["revision"]) + 1,
                "requests": {
                    **base["requests"],
                    request_id: {
                        "action": "accept",
                        "commit_id": commit_id,
                        "entry_ids": dict(accepted),
                    },
                },
                "updated_at": self._now(),
            }

        self._repo.aux_mutate(pid, CANDIDATE_BUCKET, GROUP_KEY, mutate)

    def _replay(
        self, pid: ProjectId, group: dict[str, Any], recorded: Mapping[str, Any]
    ) -> dict[str, Any]:
        if recorded.get("action") != "accept":
            return self._group_view(group)
        commit_id = CommitId(str(recorded["commit_id"]))
        entry_ids = dict(recorded.get("entry_ids") or {})
        entries = [self._entry_at(pid, commit_id, entry_id) for entry_id in entry_ids.values()]
        return {
            "status": "committed",
            "commit_id": commit_id.value,
            "entries": entries,
            "commit_item_ids": sorted(entry_ids),
            "group": self._group_view(group),
        }

    def _recover(
        self,
        pid: ProjectId,
        group: dict[str, Any],
        commit_id: CommitId,
        request_id: str,
        changed_ids: tuple[ArtifactId, ...],
    ) -> dict[str, Any]:
        """提交成功但组未更新：按确定性 ID 认回条目并补齐组状态。"""
        changed = {artifact_id.value for artifact_id in changed_ids}
        accepted: dict[str, str] = {}
        for item in group["items"]:
            if item["status"] != PENDING:
                continue
            artifact_id = _deterministic_id(group["group_id"], item["item_id"])
            if artifact_id in changed:
                accepted[item["item_id"]] = artifact_id
        entries = [self._entry_at(pid, commit_id, artifact_id) for artifact_id in accepted.values()]
        self._mark_accepted(pid, accepted, request_id, commit_id.value, entries)
        return {
            "status": "committed",
            "commit_id": commit_id.value,
            "entries": entries,
            "commit_item_ids": sorted(accepted),
            "group": self.view(pid.value),
        }

    def _assert_sources_current(self, pid: ProjectId, selected: list[dict[str, Any]]) -> None:
        """建议带生成时的章基础版本：正文变了就不能原样采用（V03-4.4）。

        **逐项核对，不按章覆盖**：同章的旧建议与新建议一起选时，按章去重会让
        新建议的基础覆盖旧建议的，旧建议于是绕过过期检查直接入账——已复现
        「混合采用写入两条设定」。现在每条带来源的建议各自比对，任一条过期即
        拒绝整组。

        手工暂存的候选没有 ``source``，因此不受影响；只有模型建议会被拦。
        变化时给 409 而不是悄悄放行——采用一条依据已变的设定，等于把旧事实写进新稿。
        """
        checked: list[tuple[str, str, str]] = []
        for item in selected:
            source = item.get("source") or {}
            chapter_id = source.get("chapter_id")
            chapter_base = source.get("chapter_base")
            if isinstance(chapter_id, str) and isinstance(chapter_base, str) and chapter_base:
                checked.append((str(item["item_id"]), chapter_id, chapter_base))
        if not checked:
            return
        head = self._head(pid)
        manuscripts = (
            ManuscriptCatalog(commit_id=None, entries=())
            if head is None
            else ManuscriptCatalog.from_snapshot(self._repo.get_snapshot(pid, head))
        )
        for item_id, chapter_id, base in checked:
            try:
                current = manuscripts.chapter_base(chapter_id)
            except (ManuscriptViolation, StoryViolation):
                current = None
            if current != base:
                raise _violation(
                    "SOURCE_NEEDS_RECHECK",
                    f"ledger.candidates.accept({pid.value})",
                    f"章节 {chapter_id} 的正文在建议 {item_id} 生成后已变化",
                    "重新整理这一章的账本建议；旧建议保留但不能再原样采用",
                    item_id=item_id,
                    chapter_id=chapter_id,
                    expected_base=base,
                    current_base=current,
                )

    def _order(
        self,
        group: dict[str, Any],
        selected: list[dict[str, Any]],
        by_id: Mapping[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """按 ``@item`` 依赖排序；缺依赖或成环时拒绝，不造悬空关系。"""
        selected_ids = {item["item_id"] for item in selected}
        ordered: list[dict[str, Any]] = []
        visiting: set[str] = set()
        done: set[str] = set()
        for item in selected:
            deps = [
                ref[1:] for ref in _ref_values(item["fields"], item["kind"]) if ref.startswith("@")
            ]
            for dep in deps:
                target = by_id.get(dep)
                if target is None:
                    raise _violation(
                        "LEDGER_DEPENDENCY_UNCLOSED",
                        "ledger.candidates.accept",
                        f"条目 {item['item_id']!r} 引用了不存在的组内条目 {dep!r}",
                        "刷新待确认列表后重新整理这条设定",
                        item_id=item["item_id"],
                        dependency=dep,
                    )
                if (
                    dep not in selected_ids
                    and target.get("status") != ACCEPTED
                    and not target.get("accepted_entry_id")
                ):
                    raise _violation(
                        "LEDGER_DEPENDENCY_UNCLOSED",
                        "ledger.candidates.accept",
                        f"条目 {item['item_id']!r} 依赖 {dep!r}，但该条目本次未选中且尚未确认",
                        "同时选中被依赖的条目，或先确认它再确认这条",
                        item_id=item["item_id"],
                        dependency=dep,
                    )

        def visit(item: dict[str, Any]) -> None:
            item_id = item["item_id"]
            if item_id in done:
                return
            if item_id in visiting:
                raise _violation(
                    "LEDGER_DEPENDENCY_CYCLE",
                    "ledger.candidates.accept",
                    f"候选条目 {item_id!r} 通过引用形成了循环依赖",
                    "拆开循环引用；关系两端不能互相等待对方先创建",
                    item_id=item_id,
                )
            visiting.add(item_id)
            for ref in _ref_values(item["fields"], item["kind"]):
                if not ref.startswith("@"):
                    continue
                dep = by_id.get(ref[1:])
                if dep is not None and dep["item_id"] in selected_ids:
                    visit(dep)
            visiting.discard(item_id)
            done.add(item_id)
            ordered.append(item)

        for item in selected:
            visit(item)
        return ordered

    def _resolve(
        self,
        item: dict[str, Any],
        by_id: Mapping[str, dict[str, Any]],
        group_id: str,
        selected_ids: set[str],
    ) -> dict[str, Any]:
        """把 ``@item`` 占位替换成真实 ID：已确认的用其条目，同批的用确定性 ID。"""
        fields = dict(item["fields"])
        for field in ledger_entries.LEDGER_REF_FIELDS.get(item["kind"], {}):
            value = fields.get(field)
            if not value:
                continue
            if isinstance(value, str):
                fields[field] = self._resolve_ref(value, by_id, group_id, selected_ids)
            else:
                fields[field] = [
                    self._resolve_ref(str(ref), by_id, group_id, selected_ids) for ref in value
                ]
        return fields

    def _resolve_ref(
        self,
        ref: str,
        by_id: Mapping[str, dict[str, Any]],
        group_id: str,
        selected_ids: set[str],
    ) -> str:
        if not ref.startswith("@"):
            return ref
        item_id = ref[1:]
        target = by_id.get(item_id)
        if target is None:
            raise _violation(
                "LEDGER_DEPENDENCY_UNCLOSED",
                "ledger.candidates.accept",
                f"引用的组内条目 {item_id!r} 不存在",
                "刷新待确认列表后重新整理这条设定",
                dependency=item_id,
            )
        accepted = target.get("accepted_entry_id")
        if accepted:
            return str(accepted)
        if item_id in selected_ids:
            return _deterministic_id(group_id, item_id)
        raise _violation(
            "LEDGER_DEPENDENCY_UNCLOSED",
            "ledger.candidates.accept",
            f"引用的组内条目 {item_id!r} 尚未确认且本次未选中",
            "同时选中被依赖的条目，或先确认它",
            dependency=item_id,
        )

    def _lookup(
        self,
        catalog: StoryCatalog,
        items_by_id: Mapping[str, dict[str, Any]],
    ) -> Callable[[str], str | None]:
        def lookup(target: str) -> str | None:
            if target.startswith("@"):
                item = items_by_id.get(target[1:])
                return item["kind"] if item is not None else None
            entry = catalog.get(target)
            return entry.kind if entry is not None else None

        return lookup

    def _require_group(self, pid: ProjectId) -> dict[str, Any]:
        group = self._load(pid)
        if group is None:
            raise _violation(
                "LEDGER_CANDIDATE_NOT_FOUND",
                f"ledger.candidates({pid.value})",
                f"作品 {pid.value} 还没有待确认的设定",
                "先在账本里把一条设定存为待确认",
                project_id=pid.value,
            )
        return group

    def _require_revision(self, group: dict[str, Any], expected: int) -> None:
        if int(group["revision"]) != int(expected):
            raise _violation(
                "LEDGER_CANDIDATE_CONFLICT",
                "ledger.candidates",
                f"待确认组已推进到版本 {group['revision']}，本次基于 {expected}",
                "刷新待确认列表后重新选择；不要基于旧列表确认",
                expected=expected,
                current=group["revision"],
            )

    def _pending(self, group: dict[str, Any], item_ids: list[str]) -> list[dict[str, Any]]:
        by_id = {item["item_id"]: item for item in group["items"]}
        wanted: list[dict[str, Any]] = []
        for item_id in item_ids:
            item = by_id.get(item_id)
            if item is None:
                raise _violation(
                    "LEDGER_CANDIDATE_ITEM_NOT_FOUND",
                    "ledger.candidates",
                    f"候选组里没有条目 {item_id!r}",
                    "刷新待确认列表后重新选择",
                    item_id=item_id,
                )
            if item["status"] != PENDING:
                raise _violation(
                    "LEDGER_CANDIDATE_ITEM_NOT_PENDING",
                    "ledger.candidates",
                    f"条目 {item_id!r} 已处理过（{item['status']}）",
                    "刷新待确认列表；已处理条目不再变动",
                    item_id=item_id,
                    status=item["status"],
                )
            wanted.append(item)
        return wanted

    def _load(self, pid: ProjectId) -> dict[str, Any] | None:
        row = self._repo.aux_read(pid, CANDIDATE_BUCKET, GROUP_KEY)
        return dict(row) if row is not None else None

    def _empty_group(self, pid: ProjectId) -> dict[str, Any]:
        now = self._now()
        return {
            "group_id": f"lcg_{content_hash({'project': pid.value})[:12]}",
            "project_id": pid.value,
            "revision": 0,
            "items": [],
            "requests": {},
            "created_at": now,
            "updated_at": now,
        }

    def _empty_view(self, pid: ProjectId) -> dict[str, Any]:
        return self._group_view(self._empty_group(pid))

    def _group_view(self, group: dict[str, Any]) -> dict[str, Any]:
        items = [self._item_view(item) for item in group["items"]]
        pending = [item for item in items if item["status"] == PENDING]
        return {
            "group_id": group["group_id"],
            "project_id": group["project_id"],
            "revision": int(group["revision"]),
            "items": items,
            "origin": group.get("origin"),
            "pending_count": len(pending),
            "hypothesis_count": sum(1 for item in pending if item["hypothesis"]),
            "updated_at": group.get("updated_at", ""),
        }

    def _item_view(self, item: dict[str, Any]) -> dict[str, Any]:
        entry = StoryEntry(
            artifact_id=item["item_id"],
            kind=item["kind"],
            revision_id="",
            content=dict(item["fields"]),
        )
        view = entry_view(entry)
        title = view["title"]
        if title.startswith("@") or title.startswith("art_") or title == item["item_id"]:
            # 引用占位不是给人看的标题；退回种类标签，详情由 summary 承担。
            title = view["kind_label"]
        return {
            "item_id": item["item_id"],
            "kind": item["kind"],
            "kind_label": view["kind_label"],
            "title": title,
            "summary": view["summary"],
            "hypothesis": bool(item["hypothesis"]),
            "status": item["status"],
            "source": item.get("source"),
            "accepted_entry_id": item.get("accepted_entry_id"),
        }

    def _head(self, pid: ProjectId) -> CommitId | None:
        branch = self._repo.get_branch(pid, "main")
        return branch.head_commit_id if branch is not None else None

    def _catalog(self, pid: ProjectId, head: CommitId | None) -> StoryCatalog:
        if head is None:
            return StoryCatalog(commit_id=None, entries=())
        return StoryCatalog.from_snapshot(self._repo.get_snapshot(pid, head))

    def _entry_at(self, pid: ProjectId, commit_id: CommitId, entry_id: str) -> dict[str, Any]:
        catalog = self._catalog(pid, commit_id)
        entry = catalog.get(entry_id)
        if entry is None:
            raise _violation(
                "LEDGER_ENTRY_NOT_FOUND",
                f"ledger.candidates({pid.value})",
                f"确认后没有找到条目 {entry_id!r}",
                "重新读取账本；若持续缺失，检查本次提交是否被回滚",
                entry_id=entry_id,
            )
        return {
            **entry_view(entry),
            "revision_id": entry.revision_id,
            "content": dict(entry.content),
        }

    def _now(self) -> str:
        return str(self._clock.now().isoformat())

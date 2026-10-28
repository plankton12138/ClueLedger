"""线索的共用作者视图：按正文出现章过滤，正文引文与解释记录分开。"""

from __future__ import annotations

import base64
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Literal, cast

from pydantic import BaseModel, Field

from hastings.library.modules import ModuleLibrary
from hastings.workspace.clue_models import (
    ClueContribution,
    ClueDesignLink,
    ClueEvidence,
    ClueInference,
    ClueMode,
    FileSource,
    InferenceIntent,
    ModuleSource,
    Source,
)
from hastings.workspace.clue_reviews import ClueReviewSummaryView, clue_review_summaries
from hastings.workspace.clue_store import ClueLedgerStore
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import FileView, Workspace, has_chapter_content
from hastings.workspace.module_assembly import Assembly, AssemblyStore
from hastings.workspace.source_locations import design_evidence_id, file_evidence_id
from hastings.workspace.state_store import current_chapter

Freshness = Literal["valid", "stale", "source_missing", "unavailable", "none"]
DesignFreshness = Literal[
    "valid", "stale", "detached", "source_missing", "unavailable", "invalid_range"
]
Outcome = Literal["found", "empty", "insufficient", "unrecorded"]


class ClueCoverageView(BaseModel):
    outcome: Outcome = "unrecorded"
    freshness: Freshness = "none"
    chapter_paths: list[str] = Field(default_factory=list)
    task_id: str = ""
    at: str = ""
    note: str = ""


class ClueLifecycle(BaseModel):
    status: Literal["open", "resolved", "dropped", "unknown"] = "unknown"
    planted_chapter: int | None = None
    due_chapter: int | None = None
    resolution_chapter: int | None = None


class ClueBasis(BaseModel):
    evidence_id: str
    path: str
    digest: str
    current_digest: str | None = None
    chapter: int
    start: int
    end: int
    quote: str
    freshness: Freshness


class ClueObservation(BaseModel):
    observation_id: str
    chapter: int
    kind: Literal["plant", "mention", "resolve"]
    mode: ClueMode = "unknown"
    text: str
    evidence_ids: list[str]


class ReaderSeen(BaseModel):
    text: str
    mode: ClueMode
    basis_ids: list[str]


class ClueDesignView(BaseModel):
    link_id: str
    relation: str = ""
    source: Source
    freshness: DesignFreshness
    quote: str
    evidence_id: str
    label: str = ""


class InferenceClue(BaseModel):
    clue_id: str
    title: str


class InferenceStatementView(BaseModel):
    text: str
    clue_ids: list[str] = Field(default_factory=list)
    design_link_ids: list[str] = Field(default_factory=list)


class ClueInferenceView(BaseModel):
    inference_id: str
    inference_revision: str
    goal: str
    intent: InferenceIntent
    clue_ids: list[str]
    clues: list[InferenceClue]
    premises: list[InferenceStatementView]
    alternatives: list[InferenceStatementView]
    design_link_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""
    warnings: list[str] = Field(default_factory=list)
    #: 目标线索已有的正文章节（截止章内）；核对界面用它作为默认目标，不替作者扩大范围。
    suggested_chapters: list[str] = Field(default_factory=list)


class ClueSummary(BaseModel):
    clue_id: str
    clue_revision: str
    title: str
    as_of_chapter: int
    lifecycle: ClueLifecycle
    freshness: Freshness
    coverage: ClueCoverageView
    mentioned_chapters: list[int]
    warnings: list[str] = Field(default_factory=list)


class ClueDetail(ClueSummary):
    workspace_lineage: str
    ledger_revision: str = ""
    observations: list[ClueObservation]
    reader_seen: list[ReaderSeen]
    evidence: list[ClueBasis]
    interpretation: str = ""
    observations_truncated: bool = False
    author_designs: list[ClueDesignView] = Field(default_factory=list)
    inferences: list[ClueInferenceView] = Field(default_factory=list)
    #: 已有核对报告的摘要；完整报告按 report_id 单独读取，不随详情一起下发。
    reviews: list[ClueReviewSummaryView] = Field(default_factory=list)


class ClueChapter(BaseModel):
    path: str
    chapter: int
    title: str
    digest: str | None
    has_content: bool
    coverage: ClueCoverageView


class ClueList(BaseModel):
    items: list[ClueSummary]
    total: int
    next_cursor: str
    as_of_chapter: int
    current_chapter: int
    ledger_revision: str
    view_revision: str
    chapters: list[ClueChapter]
    chapter: ClueChapter | None = None


class ClueReader:
    """一次响应使用同一份账本和同一次读取的源文件，避免列表、详情各算一套。"""

    def __init__(
        self,
        workspace: Workspace,
        root: Path,
        as_of_chapter: int | None,
        *,
        library: ModuleLibrary | None = None,
    ) -> None:
        self.workspace = workspace
        self.library = library
        self.snapshot = ClueLedgerStore(root).read()
        self.latest = current_chapter(workspace)
        self.as_of = (
            self.latest if as_of_chapter is None else min(self.latest, max(0, as_of_chapter))
        )
        self.catalog = [
            row
            for row in workspace.catalog().chapters
            if row.exists and row.chapter_number is not None
        ]
        self.files: dict[str, FileView | WorkspaceError] = {}
        self.rows: dict[str, list[ClueContribution]] = defaultdict(list)
        if self.snapshot.ledger:
            for row in self.snapshot.ledger.contributions.values():
                self.rows[row.clue_id].append(row)

    def file(self, path: str) -> FileView | WorkspaceError:
        if path not in self.files:
            try:
                self.files[path] = self.workspace.read(path)
            except WorkspaceError as exc:
                self.files[path] = exc
        return self.files[path]

    def status(self, path: str, digest: str) -> tuple[Freshness, str | None]:
        current = self.file(path)
        if isinstance(current, WorkspaceError):
            return ("source_missing" if current.code == "FILE_NOT_FOUND" else "unavailable"), None
        return ("valid" if current.digest == digest else "stale"), current.digest

    def coverage(self, paths: list[str]) -> ClueCoverageView:
        ledger = self.snapshot.ledger
        records = [ledger.coverage[path] for path in paths if ledger and path in ledger.coverage]
        if not records:
            return ClueCoverageView(chapter_paths=paths)
        # 贡献涉及的每一章都需要有效覆盖，不能用其中一章概括全部。
        statuses = [self.status(row.path, row.digest)[0] for row in records]
        status: Freshness = next((value for value in statuses if value != "valid"), "valid")
        if len(records) != len(paths) and status == "valid":
            status = "none"
        outcome: Outcome = (
            "found"
            if any(row.outcome == "found" for row in records)
            else "insufficient"
            if any(row.outcome == "insufficient" for row in records)
            else "empty"
        )
        latest = max(records, key=lambda row: row.at)
        return ClueCoverageView(
            outcome=outcome,
            freshness=status,
            chapter_paths=paths,
            task_id=latest.task_id,
            at=latest.at,
            note=latest.note,
        )

    def chapters(self) -> list[ClueChapter]:
        result: list[ClueChapter] = []
        for row in self.catalog:
            if row.chapter_number is None or row.chapter_number > self.as_of:
                continue
            file = self.file(row.path)
            result.append(
                ClueChapter(
                    path=row.path,
                    chapter=row.chapter_number,
                    title=row.display_title,
                    digest=file.digest if isinstance(file, FileView) else None,
                    has_content=has_chapter_content(file.text)
                    if isinstance(file, FileView)
                    else False,
                    coverage=self.coverage([row.path]),
                )
            )
        return result

    def _module_label(self, source: ModuleSource) -> str:
        if self.library is None or self.library.library_id != source.library_id:
            return f"{source.module_id} · {source.entry_id}"
        try:
            module = self.library.module(source.module_id)
            entry = self.library.read_entry(
                module_id=source.module_id,
                revision_id=source.revision_id,
                entry_id=source.entry_id,
                end=0,
            )
            return f"{module.name} · {entry['title']}"
        except WorkspaceError:
            return f"{source.module_id} · {source.entry_id}"

    def _design_source(
        self, link: ClueDesignLink, assembly: Assembly
    ) -> tuple[DesignFreshness, str]:
        source = link.source
        if isinstance(source, FileSource):
            freshness, _ = self.status(source.path, source.digest)
            status = cast(DesignFreshness, freshness)
            file = self.file(source.path)
            if (
                status == "valid"
                and isinstance(file, FileView)
                and file.text[link.start : link.end] != link.quote
            ):
                status = "unavailable"
            label = file.path if isinstance(file, FileView) else source.path
            return status, label
        label = self._module_label(source)
        binding = next((item for item in assembly.bindings if item.id == link.binding_id), None)
        if (
            binding is None
            or binding.module.library_id != source.library_id
            or binding.module.module_id != source.module_id
            or binding.module.revision_id != source.revision_id
        ):
            return "detached", label
        if self.library is None or self.library.library_id != source.library_id:
            return "unavailable", label
        try:
            checked = self.library.read_entry(
                module_id=source.module_id,
                revision_id=source.revision_id,
                entry_id=source.entry_id,
                end=0,
            )
            text, digest = self.library.storage.read_entry(
                source.module_id, source.revision_id, source.entry_id
            )
        except WorkspaceError:
            return "unavailable", label
        if digest != str(checked["digest"]) or digest != source.content_digest:
            return "stale", label
        if not (0 <= link.start < link.end <= len(text)) or (
            text[link.start : link.end] != link.quote
        ):
            return "invalid_range", label
        return "valid", label

    def _design_views(self, clue_id: str) -> tuple[list[ClueDesignView], list[str]]:
        ledger = self.snapshot.ledger
        if ledger is None:
            return [], []
        assembly = AssemblyStore(self.workspace.root).read()
        views: list[ClueDesignView] = []
        warnings: list[str] = []
        for link in sorted(
            (row for row in ledger.design_links.values() if row.clue_id == clue_id),
            key=lambda row: (row.created_at, row.link_id),
        ):
            freshness, label = self._design_source(link, assembly)
            views.append(
                ClueDesignView(
                    link_id=link.link_id,
                    relation=link.relation,
                    source=link.source,
                    freshness=freshness,
                    quote=link.quote,
                    evidence_id=design_evidence_id(self.workspace, self.snapshot, link),
                    label=label,
                )
            )
            if freshness != "valid":
                warnings.append("作者计划关联的来源已变化或模块已调整，请核对后重新关联。")
        return views, list(dict.fromkeys(warnings))

    def _inference_view(
        self, row: ClueInference, assembly: Assembly
    ) -> tuple[ClueInferenceView, list[str]]:
        ledger = self.snapshot.ledger
        assert ledger is not None
        linked = [
            ledger.design_links[link_id]
            for link_id in row.design_link_ids
            if link_id in ledger.design_links
        ]
        changed = [
            link for link in linked if self._design_source(link, assembly)[0] != "valid"
        ]
        warnings = (
            [f"推理目标「{row.goal[:40]}」引用的计划来源已变化，请核对。"] if changed else []
        )
        suggested: list[str] = []
        for clue_id in row.clue_ids:
            for contribution in self.rows.get(clue_id, []):
                path = contribution.source_path
                if (
                    path
                    and contribution.content.source_chapter <= self.as_of
                    and path not in suggested
                ):
                    suggested.append(path)
        view = ClueInferenceView(
            inference_id=row.inference_id,
            inference_revision=row.inference_revision,
            goal=row.goal,
            intent=row.intent,
            clue_ids=list(row.clue_ids),
            suggested_chapters=suggested[:10],
            clues=[
                InferenceClue(clue_id=item, title=ledger.clues[item].title)
                for item in row.clue_ids
                if item in ledger.clues
            ],
            premises=[
                InferenceStatementView(
                    text=item.text,
                    clue_ids=list(item.clue_ids),
                    design_link_ids=list(item.design_link_ids),
                )
                for item in row.premises
            ],
            alternatives=[
                InferenceStatementView(
                    text=item.text,
                    clue_ids=list(item.clue_ids),
                    design_link_ids=list(item.design_link_ids),
                )
                for item in row.alternatives
            ],
            design_link_ids=list(row.design_link_ids),
            updated_at=row.updated_at,
            warnings=(
                ["引用的计划来源已变化，请核对后再把它视为当前安排。"] if changed else []
            ),
        )
        return view, warnings

    def _inference_views(self, clue_id: str) -> tuple[list[ClueInferenceView], list[str]]:
        ledger = self.snapshot.ledger
        if ledger is None:
            return [], []
        assembly = AssemblyStore(self.workspace.root).read()
        views: list[ClueInferenceView] = []
        warnings: list[str] = []
        for row in ledger.inferences.values():
            if clue_id not in row.clue_ids:
                continue
            view, rows = self._inference_view(row, assembly)
            views.append(view)
            warnings.extend(rows)
        return views, list(dict.fromkeys(warnings))

    def detail(self, clue_id: str, *, include_designs: bool = False) -> ClueDetail | None:
        ledger = self.snapshot.ledger
        if ledger is None or clue_id not in ledger.clues:
            return None
        identity = ledger.clues[clue_id]
        all_rows = self.rows[clue_id]
        visible = [
            row
            for row in all_rows
            if row.content.planted_chapter <= self.as_of
            and row.content.source_chapter <= self.as_of
        ]
        if not visible and all_rows and (
            identity.first_chapter or self.latest
        ) > self.as_of:
            return None
        visible.sort(key=lambda row: row.content.source_chapter or row.content.planted_chapter)
        latest = visible[-1].content if visible else None
        lifecycle = ClueLifecycle()
        if latest:
            resolved = (
                latest.resolution_chapter is not None and latest.resolution_chapter <= self.as_of
            )
            lifecycle = ClueLifecycle(
                status="open" if latest.status == "resolved" and not resolved else latest.status,
                planted_chapter=latest.planted_chapter,
                due_chapter=latest.due_chapter,
                resolution_chapter=latest.resolution_chapter if resolved else None,
            )
        bases: list[ClueBasis] = []
        observations: list[ClueObservation] = []
        paths: list[str] = []
        seen: set[tuple[str, str, int, int, str]] = set()
        source_status: list[Freshness] = []
        mentions: set[int] = set()
        for row in visible:
            if row.source_path not in paths:
                paths.append(row.source_path)
            source_status.append(self.status(row.source_path, row.source_digest)[0])
            if row.content.source_chapter:
                mentions.add(row.content.source_chapter)
            proofs: list[tuple[Literal["plant", "mention", "resolve"], ClueEvidence, int]] = [
                ("plant", row.content.evidence, row.content.planted_chapter)
            ]
            if row.content.mention_evidence:
                proofs.append(("mention", row.content.mention_evidence, row.content.source_chapter))
            if row.content.resolution_evidence and row.content.resolution_chapter:
                proofs.append(
                    ("resolve", row.content.resolution_evidence, row.content.resolution_chapter)
                )
            for kind, proof, chapter in proofs:
                if chapter > self.as_of:
                    continue
                mentions.add(chapter)
                key = (kind, proof.path, proof.start, proof.end, proof.digest)
                if key in seen:
                    continue
                seen.add(key)
                if len(observations) >= 200:
                    continue
                status, digest = self.status(proof.path, proof.digest)
                file = self.file(proof.path)
                if (
                    status == "valid"
                    and isinstance(file, FileView)
                    and file.text[proof.start : proof.end] != proof.quote
                ):
                    status = "unavailable"
                evidence_id = file_evidence_id(
                    self.workspace,
                    self.snapshot,
                    clue_id=clue_id,
                    source_chapter=row.content.source_chapter,
                    role=kind,
                    evidence=proof,
                )
                bases.append(
                    ClueBasis(
                        evidence_id=evidence_id,
                        path=proof.path,
                        digest=proof.digest,
                        current_digest=digest,
                        chapter=chapter,
                        start=proof.start,
                        end=proof.end,
                        quote=proof.quote,
                        freshness=status,
                    )
                )
                observations.append(
                    ClueObservation(
                        observation_id=f"{row.observation_id}_{kind}",
                        chapter=chapter,
                        kind=kind,
                        # 一份贡献可同时引用早章埋设、本章证词和回收叙述。
                        # 贡献性质只属于本章主要观察，不能据此替另一段引文定性。
                        mode=row.mode
                        if kind == "mention"
                        or kind == "plant"
                        and chapter == row.content.source_chapter
                        else "unknown",
                        text=proof.quote,
                        evidence_ids=[evidence_id],
                    )
                )
        statuses = [row.freshness for row in bases] + source_status
        freshness: Freshness = (
            next((value for value in statuses if value != "valid"), "valid") if statuses else "none"
        )
        warnings = []
        if freshness not in ("valid", "none"):
            warnings.append("部分来源已变化或不可读，请重新整理相关章节。")
        if not bases:
            warnings.append("暂无有效正文依据。")
        if len(seen) > len(observations):
            warnings.append("当前显示前 200 条观察，其他记录仍保存在账本中。")
        valid_ids = {row.evidence_id for row in bases if row.freshness == "valid"}
        reader_seen = [
            ReaderSeen(text=row.text, mode=row.mode, basis_ids=[row.observation_id])
            for row in observations
            if all(value in valid_ids for value in row.evidence_ids)
        ]
        author_designs: list[ClueDesignView] = []
        inferences: list[ClueInferenceView] = []
        if include_designs:
            author_designs, design_warnings = self._design_views(clue_id)
            inferences, inference_warnings = self._inference_views(clue_id)
            warnings.extend(design_warnings)
            warnings.extend(inference_warnings)
        return ClueDetail(
            clue_id=clue_id,
            clue_revision=identity.clue_revision,
            title=latest.clue
            if latest
            else identity.title
            if identity.first_chapter is None or self.as_of == self.latest
            else "暂无正文依据的线索",
            workspace_lineage=ledger.workspace_lineage,
            ledger_revision=self.snapshot.revision,
            as_of_chapter=self.as_of,
            lifecycle=lifecycle,
            freshness=freshness,
            coverage=self.coverage(paths),
            mentioned_chapters=sorted(mentions),
            warnings=warnings,
            observations=observations,
            reader_seen=reader_seen,
            evidence=bases,
            interpretation=latest.supports if latest else "",
            observations_truncated=len(seen) > len(observations),
            author_designs=author_designs,
            inferences=inferences,
        )

    def details(self) -> list[ClueDetail]:
        ledger = self.snapshot.ledger
        result = (
            [detail for clue_id in ledger.clues if (detail := self.detail(clue_id))]
            if ledger
            else []
        )
        return sorted(
            result,
            key=lambda row: (
                {"open": 0, "resolved": 1, "dropped": 2, "unknown": 3}[row.lifecycle.status],
                row.lifecycle.due_chapter or 10**9,
                row.lifecycle.planted_chapter or 10**9,
                row.clue_id,
            ),
        )


def clue_detail(
    workspace: Workspace,
    root: Path,
    clue_id: str,
    *,
    as_of_chapter: int | None = None,
    library: ModuleLibrary | None = None,
) -> ClueDetail:
    detail = ClueReader(workspace, root, as_of_chapter, library=library).detail(
        clue_id, include_designs=True
    )
    if detail is not None:
        detail = detail.model_copy(
            update={
                "reviews": clue_review_summaries(
                    workspace,
                    ClueLedgerStore(root),
                    clue_ids=[clue_id],
                    as_of_chapter=detail.as_of_chapter,
                )
            }
        )
    if detail is None:
        raise WorkspaceError(
            code="CLUE_NOT_FOUND",
            where="clues.detail",
            why="当前截止章下没有这条线索",
            fix="重新读取当前线索列表后选择",
        )
    return detail


def clue_inference_view(
    workspace: Workspace,
    root: Path,
    inference_id: str,
    *,
    library: ModuleLibrary | None = None,
) -> ClueInferenceView:
    reader = ClueReader(workspace, root, None, library=library)
    ledger = reader.snapshot.ledger
    row = ledger.inferences.get(inference_id) if ledger is not None else None
    if row is None:
        raise WorkspaceError(
            code="CLUE_NOT_FOUND",
            where="clues.inference",
            why="当前账本没有这个推理目标",
            fix="重新读取线索详情后选择目标",
        )
    view, _ = reader._inference_view(row, AssemblyStore(workspace.root).read())
    return view


def clue_list(
    workspace: Workspace,
    root: Path,
    *,
    chapter_path: str | None = None,
    as_of_chapter: int | None = None,
    query: str = "",
    limit: int = 20,
    cursor: str = "",
) -> ClueList:
    reader = ClueReader(workspace, root, as_of_chapter)
    chapter = None
    if chapter_path is not None and not any(row.path == chapter_path for row in reader.catalog):
        raise WorkspaceError(
            code="CLUE_CHAPTER_NOT_FOUND",
            where="clues.list",
            why="所选路径不是当前正文章节",
            fix="从作品目录重新选择章节",
        )
    chapters = reader.chapters()
    chapter = next((row for row in chapters if row.path == chapter_path), None)
    details = reader.details()
    if chapter_path is not None:
        # 必须按实际目标路径或依据路径关联，不只看第一次埋设章。
        relevant = {
            row.clue_id
            for rows in reader.rows.values()
            for row in rows
            if row.content.source_chapter <= reader.as_of
            and (
                row.source_path == chapter_path
                or row.content.evidence.path == chapter_path
                and row.content.planted_chapter <= reader.as_of
                or row.content.mention_evidence is not None
                and row.content.mention_evidence.path == chapter_path
                or row.content.resolution_evidence is not None
                and row.content.resolution_evidence.path == chapter_path
                and row.content.resolution_chapter is not None
                and row.content.resolution_chapter <= reader.as_of
            )
        }
        details = [row for row in details if row.clue_id in relevant] if chapter else []
    needle = query.strip().casefold()
    if needle:
        details = [
            row
            for row in details
            if needle
            in (row.title + " " + " ".join(obs.text for obs in row.observations)).casefold()
        ]
    size = min(50, max(1, limit))
    stamp = hashlib.sha256(
        json.dumps(
            [
                reader.snapshot.revision,
                reader.as_of,
                chapter_path,
                needle,
                size,
                [
                    (path, value.digest if isinstance(value, FileView) else value.code)
                    for path, value in sorted(reader.files.items())
                ],
                [(row.path, row.chapter) for row in chapters],
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    offset = 0
    if cursor:
        try:
            raw = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            if (
                raw["stamp"] != stamp
                or type(raw["offset"]) is not int
                or not 0 <= raw["offset"] <= len(details)
            ):
                raise ValueError("游标范围已变化")
            offset = raw["offset"]
        except (ValueError, KeyError, TypeError) as exc:
            raise WorkspaceError(
                code="CLUE_CURSOR_CONFLICT",
                where="clues.list",
                why="分页游标与当前资料或范围不一致",
                fix="重新读取第一页",
            ) from exc
    page = details[offset : offset + size]
    next_cursor = (
        base64.urlsafe_b64encode(
            json.dumps({"stamp": stamp, "offset": offset + len(page)}).encode()
        ).decode()
        if offset + len(page) < len(details)
        else ""
    )
    return ClueList(
        items=[ClueSummary.model_validate(row.model_dump()) for row in page],
        total=len(details),
        next_cursor=next_cursor,
        as_of_chapter=reader.as_of,
        current_chapter=reader.latest,
        ledger_revision=reader.snapshot.revision,
        view_revision=stamp,
        chapters=chapters,
        chapter=chapter,
    )

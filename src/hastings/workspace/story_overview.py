"""故事总览只读服务：来源清单、失效、分页与关联共用一个版本快照。"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from hastings.library.modules import ModuleLibrary
from hastings.workspace.errors import WorkspaceError
from hastings.workspace.files import Workspace
from hastings.workspace.module_assembly import AssemblyStore
from hastings.workspace.module_facets import OverviewStore, overview_error
from hastings.workspace.overview_models import RECIPE_VERSION, fingerprint
from hastings.workspace.story_index import RECIPE_VERSION as INDEX_RECIPE
from hastings.workspace.story_index import StoryIndexStore, chapter_view, story_view


def page(items: list[Any], key: object, cursor: str | None, limit: int) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    query = fingerprint(key)
    offset = 0
    if cursor:
        try:
            value = json.loads(base64.urlsafe_b64decode(cursor.encode()))
            offset = value["offset"]
            if (
                value["key"] != query
                or type(offset) is not int
                or offset < 0
                or offset > len(items)
            ):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise overview_error("OVERVIEW_CURSOR_STALE_CONFLICT", "分页位置已失效") from None
    end = min(offset + limit, len(items))
    next_cursor = (
        base64.urlsafe_b64encode(json.dumps({"offset": end, "key": query}).encode()).decode()
        if end < len(items)
        else None
    )
    return {"items": items[offset:end], "next_cursor": next_cursor, "total": len(items)}


class StoryOverview:
    def __init__(
        self,
        workspace: Workspace,
        root: Path,
        library: ModuleLibrary | None,
        as_of: int | None = None,
    ):
        self.workspace, self.root, self.library = workspace, root, library
        self.store = OverviewStore(root)
        assembly_store = AssemblyStore(workspace.root)
        self.assembly = assembly_store.read()
        self.assembly_digest = assembly_store.digest()
        self.chapters: list[dict[str, Any]] = []
        for entry in workspace.catalog().chapters:
            if entry.exists and entry.chapter_number is not None:
                view = workspace.read(entry.path)
                self.chapters.append(
                    {
                        "path": entry.path,
                        "chapter": entry.chapter_number,
                        "title": entry.display_title or entry.title,
                        "digest": view.digest,
                    }
                )
        self.chapter_directory = list(self.chapters)
        maximum = max((item["chapter"] for item in self.chapters), default=0)
        self.as_of = maximum if as_of is None else min(as_of, maximum)
        self.chapters = [item for item in self.chapters if item["chapter"] <= self.as_of]
        self.bindings: list[dict[str, Any]] = []
        self.module_targets: list[dict[str, Any]] = []
        for binding in self.assembly.bindings:
            row: dict[str, Any] = {
                "binding_id": binding.id,
                "display_name": binding.label or "模块",
                "module": binding.module.view(),
                "availability": "available",
                "capabilities": ["content"],
                "records": [],
            }
            try:
                if library is None or library.library_id != binding.module.library_id:
                    raise overview_error("OVERVIEW_REF_UNAVAILABLE", "模块库当前不可用")
                info = library.module(binding.module.module_id)
                row["display_name"] = binding.label or info.name
                # 清单读取只读版本元信息；不通过 catalog 重建去逐条读全文。
                manifest = library.storage.read_manifest(
                    binding.module.module_id, binding.module.revision_id
                )
                for record in manifest.get("entries", []):
                    source = {
                        "binding_id": binding.id,
                        **binding.module.view(),
                        "entry_id": record["entry_id"],
                        "digest": record["digest"],
                    }
                    target = {
                        "id": "module:" + fingerprint(source),
                        "kind": "module",
                        "source": source,
                        "signature": fingerprint([source, RECIPE_VERSION]),
                    }
                    self.module_targets.append(target)
                    row["records"].append(
                        {
                            "entry_id": record["entry_id"],
                            "digest": record["digest"],
                            "title": record.get("title") or "内容",
                        }
                    )
                    projection = self.store.valid(target)
                    if projection:
                        for facet in projection.get("facets", []):
                            capability = "characters" if facet["kind"] == "character" else "clues"
                            if capability not in row["capabilities"]:
                                row["capabilities"].append(capability)
            except (WorkspaceError, OSError, KeyError, TypeError) as exc:
                row.update(
                    availability="unavailable", diagnostic=str(exc), capabilities=["content"]
                )
            self.bindings.append(row)
        self.index = StoryIndexStore(root).read_all()
        self.module_generation = fingerprint(
            [(t["signature"], self.store.valid(t)) for t in self.module_targets]
        )
        self.chapter_targets = [
            {
                "id": "chapter:" + item["path"],
                "kind": "chapter",
                **item,
                "signature": fingerprint([item["digest"], INDEX_RECIPE]),
            }
            for item in self.chapters
        ]
        self.link_targets = []
        if self.module_targets:
            for item in self.chapter_targets:
                chapter_projection = self.index.get(item["path"])
                self.link_targets.append(
                    {
                        **item,
                        "id": "links:" + item["path"],
                        "kind": "links",
                        "signature": fingerprint(
                            [
                                item["digest"],
                                self.module_generation,
                                chapter_projection.to_dict() if chapter_projection else None,
                                RECIPE_VERSION,
                            ]
                        ),
                    }
                )
        self.targets = self.module_targets + self.chapter_targets + self.link_targets
        self.generation = fingerprint(
            [
                self.assembly_digest,
                self.as_of,
                self.bindings,
                self.chapters,
                [(p, v.to_dict()) for p, v in sorted(self.index.items())],
                self.store.generation(self.targets),
                RECIPE_VERSION,
            ]
        )

    def envelope(self) -> dict[str, Any]:
        return {
            "generation": self.generation,
            "assembly_digest": self.assembly_digest,
            "as_of_chapter": self.as_of,
        }

    def require_generation(self, generation: str | None) -> None:
        if generation and generation != self.generation:
            raise overview_error("OVERVIEW_SOURCE_CHANGED_CONFLICT", "总览来源已经改变")

    def complete(self, target: dict[str, Any]) -> bool:
        if target["kind"] == "chapter":
            projection = self.index.get(target["path"])
            return bool(
                projection
                and projection.digest == target["digest"]
                and projection.recipe_version == INDEX_RECIPE
            )
        return self.store.valid(target) is not None

    def manifest(
        self, chapter_cursor: str | None = None, binding_cursor: str | None = None, limit: int = 40
    ) -> dict[str, Any]:
        return {
            **self.envelope(),
            "chapters": page(
                self.chapter_directory, [self.generation, "chapters"], chapter_cursor, limit
            ),
            "bindings": page(
                [{k: v for k, v in b.items() if k != "records"} for b in self.bindings],
                [self.generation, "bindings"],
                binding_cursor,
                limit,
            ),
            "update_summary": {
                "changed_targets": sum(not self.complete(t) for t in self.targets),
                "incomplete_targets": sum(
                    bool(self.store.read(t["id"] + ":attempt")) and not self.complete(t)
                    for t in self.targets
                ),
            },
        }

    def facets(self, binding_id: str | None = None) -> list[dict[str, Any]]:
        rows = []
        available = {b["binding_id"] for b in self.bindings if b["availability"] == "available"}
        for target in self.module_targets:
            if target["source"]["binding_id"] not in available or (
                binding_id and target["source"]["binding_id"] != binding_id
            ):
                continue
            result = self.store.valid(target)
            if result:
                rows.extend(result.get("facets", []))
        return rows

    def binding_view(
        self, binding_id: str, view: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        binding = next((b for b in self.bindings if b["binding_id"] == binding_id), None)
        if binding is None:
            raise overview_error("OVERVIEW_REF_UNAVAILABLE_NOT_FOUND", "模块已经卸下")
        if view == "content":
            rows = binding["records"]
        else:
            kind = "character" if view == "characters" else "clue"
            rows = [f for f in self.facets(binding_id) if f["kind"] == kind]
        return {
            **self.envelope(),
            "binding": {k: v for k, v in binding.items() if k != "records"},
            **page(rows, [self.generation, binding_id, view], cursor, limit),
        }

    def observations(self, ref: dict[str, Any]) -> list[dict[str, Any]]:
        rows = []
        for target in self.link_targets:
            result = self.store.valid(target)
            projection = self.index.get(target["path"])
            if not result or not projection or projection.digest != target["digest"]:
                continue
            events = {e.id: e for e in projection.events}
            for link in result.get("links", []):
                event = events.get(link["event_id"])
                if link["ref"] == ref and event:
                    rows.append(
                        {
                            **link,
                            "path": target["path"],
                            "digest": target["digest"],
                            "chapter": target["chapter"],
                            "mode": event.mode,
                        }
                    )
        return rows

    def detail(
        self, binding_id: str, selected_id: str, cursor: str | None, limit: int
    ) -> dict[str, Any]:
        facet = next(
            (f for f in self.facets(binding_id) if f["ref"]["facet_id"] == selected_id), None
        )
        if facet is None:
            raise overview_error("OVERVIEW_REF_UNAVAILABLE_NOT_FOUND", "对象不属于当前模块版本")
        return {
            **self.envelope(),
            "facet": facet,
            "observations": page(
                self.observations(facet["ref"]),
                [self.generation, selected_id, "observations"],
                cursor,
                limit,
            ),
        }

    def relations(self, binding_id: str, selected_id: str, cursor: str | None) -> dict[str, Any]:
        detail = self.detail(binding_id, selected_id, None, 1)
        ref = detail["facet"]["ref"]
        edges: list[dict[str, Any]] = []
        for target in self.module_targets:
            result = self.store.valid(target)
            if result:
                edges.extend(
                    e for e in result.get("relations", []) if ref in (e["left"], e["right"])
                )
        for target in self.link_targets:
            written = self.store.valid(target)
            projection = self.index.get(target["path"])
            if not written or not projection or projection.digest != target["digest"]:
                continue
            by_id = {e.id: e for e in projection.events}
            for relation in written.get("relations", []):
                if ref in (relation["left"], relation["right"]) and relation["event_id"] in by_id:
                    edges.append(
                        {
                            **relation,
                            "nature": "written",
                            "path": target["path"],
                            "digest": target["digest"],
                            "chapter": target["chapter"],
                            "mode": by_id[relation["event_id"]].mode,
                        }
                    )
        # 每页至多 39 条边，中心 + 39 个相邻端点 <= 40 个节点。
        result = page(edges, [self.generation, selected_id, "relations"], cursor, 39)
        refs = [ref] + [e[k] for e in result["items"] for k in ("left", "right")]
        nodes = [f for f in self.facets() if f["ref"] in refs]
        return {**self.envelope(), **result, "nodes": nodes}

    def events(
        self, order: str, cursor: str | None, limit: int, selected_id: str | None = None
    ) -> dict[str, Any]:
        result: Any = (
            chapter_view(self.workspace, self.root, as_of_chapter=self.as_of)
            if order == "chapter"
            else story_view(self.workspace, self.root, as_of_chapter=self.as_of)
        )
        rows = []
        if order == "chapter":
            for chapter in result["chapters"]:
                for event in chapter["events"] or [None]:
                    rows.append(
                        {
                            "chapter": {
                                k: v
                                for k, v in chapter.items()
                                if k not in ("events", "legacy_entries")
                            },
                            "event": event,
                            "group": "chapter",
                        }
                    )
        else:
            for i, group in enumerate(result.get("groups", [])):
                for event in group["events"]:
                    rows.append({"event": event, "chapter": None, "group": f"story:{i}"})
            rows.extend(
                {"event": e, "chapter": None, "group": "unknown"} for e in result.get("unknown", [])
            )
            for i, conflict in enumerate(result.get("conflicts", [])):
                rows.extend(
                    {"event": e, "chapter": None, "group": f"conflict:{i}"}
                    for e in conflict.get("events", [])
                )
        all_events = {
            event.id: event for projection in self.index.values() for event in projection.events
        }
        related: dict[str, list[dict[str, Any]]] = {}
        facets = self.facets()
        for facet in facets:
            for observation in self.observations(facet["ref"]):
                related.setdefault(observation["event_id"], []).append(
                    {"name": facet["name"], "kind": facet["kind"], "ref": facet["ref"]}
                )
        for row in rows:
            if row["event"]:
                # 冲突摘要没有证据字段，恢复同一已发布投影中的完整观察。
                if "evidence" not in row["event"]:
                    from hastings.workspace.story_index import _event_view

                    row["event"] = _event_view(all_events[row["event"]["id"]])
                row["related_facets"] = related.get(row["event"]["id"], [])[:40]
                row["related_more"] = len(related.get(row["event"]["id"], [])) > 40
                for evidence in row["event"]["evidence"]:
                    projection = self.index.get(evidence["path"])
                    evidence["digest"] = projection.digest if projection else None
        if selected_id:
            rows = [r for r in rows if r["event"] and r["event"]["id"] == selected_id]
        return {
            **self.envelope(),
            **page(rows, [self.generation, order, selected_id], cursor, limit),
            "conflict_count": len(result.get("conflicts", [])),
            "warnings": result.get("warnings", [])[:10],
        }

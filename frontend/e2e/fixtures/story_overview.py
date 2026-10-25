"""浏览器隔离夹具：真实模块版本、真实章节索引与有依据的派生结果。"""

import sys
from pathlib import Path

from hastings.library.modules import ModuleLibrary
from hastings.workspace.disclosure import ObservedEvidence
from hastings.workspace.files import Workspace
from hastings.workspace.module_facets import module_projection
from hastings.workspace.overview_models import ModuleSource
from hastings.workspace.story_index import ChapterProjection, EventObservation, StoryIndexStore
from hastings.workspace.story_overview import StoryOverview

workspace = Workspace(Path(sys.argv[1]))
state_root, library_root = Path(sys.argv[2]), Path(sys.argv[3])
library = ModuleLibrary(library_root)
overview = StoryOverview(workspace, state_root, library)
target = overview.module_targets[0]
source = target["source"]
text, _ = library.storage.read_entry(source["module_id"], source["revision_id"], source["entry_id"])


def evidence(quote):
    start = text.index(quote)
    return {"start": start, "end": start + len(quote), "quote": quote}


result = module_projection(ModuleSource(**source), {
    "outcome": "found", "facets": [
        {"kind": "character", "name": "𠮷", "summary": "经营码头邮局，林远的姐姐。", "evidence": evidence("𠮷是林远的姐姐。")},
        {"kind": "character", "name": "林远", "summary": "随身携带铜钥匙的船医。", "evidence": evidence("林远是一名船医。")},
        {"kind": "clue", "name": "铜钥匙", "summary": "带缺口，可以打开废弃船坞的侧门。作者计划第九章揭晓来历。", "evidence": evidence("铜钥匙可以打开废弃船坞的侧门。作者计划第九章揭晓来历。")},
    ], "relations": [{"left": 0, "right": 1, "label": "姐弟", "evidence": evidence("𠮷是林远的姐姐。")}],
}, text)
overview.store.publish(target, result, "browser-fixture")

for entry in workspace.catalog().chapters:
    view = workspace.read(entry.path)
    quote = view.text.splitlines()[-1]
    start = view.text.index(quote)
    StoryIndexStore(state_root).publish(ChapterProjection(path=entry.path, digest=view.digest, chapter=entry.chapter_number or 0, summary="侦探追问铜钥匙的来历。", events=(EventObservation(text=quote, chapter=entry.chapter_number or 0, mode="testimony" if entry.chapter_number == 1 else "narration", evidence=(ObservedEvidence(path=entry.path, digest=view.digest, start=start, end=start + len(quote), quote=quote),)),)), expected_digest=view.digest)

current = StoryOverview(workspace, state_root, library)
for target in current.link_targets:
    event = current.index[target["path"]].events[0]
    quote = event.evidence[0]
    current.store.publish(target, {"outcome": "found", "links": [
        {"ref": facet["ref"], "event_id": event.id, "text": event.text, "evidence": {"start": quote.start, "end": quote.end, "quote": quote.quote}}
        for facet in result["facets"] if facet["name"] in ("林远", "铜钥匙")
    ]}, "browser-fixture")

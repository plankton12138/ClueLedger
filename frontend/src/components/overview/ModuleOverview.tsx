import { useEffect, useState } from "react";
import { fetchRecord, entryBody } from "../../api/moduleLibrary";
import { type BindingPage, type ContentRecord, type FacetDetail, type FacetRef, type Manifest, type RelationPage } from "../../api/storyOverview";
import { pageHash, type WorkspacePage } from "../../state/navigation";
import { useOverviewResource } from "./useOverviewResource";
import type { EvidenceSelection } from "./OverviewEvidence";
import CharacterRelations from "./CharacterRelations";
import ChatMarkdown from "../chat/ChatMarkdown";
import { Button } from "../ui/Button";
import { errorMessage } from "../../api/errors";

const LABEL = { content: "内容", characters: "人物", clues: "线索" };
type OverviewPage = Extract<WorkspacePage, { kind: "overview" }>;
export default function ModuleOverview({ page, manifest, onNavigate, onEvidence }: { page: OverviewPage; manifest: Manifest; onNavigate(page: WorkspacePage): void; onEvidence(selection: EvidenceSelection): void }) {
  const [cursor, setCursor] = useState<string | null>(null);
  const [relationCursor, setRelationCursor] = useState<string | null>(null);
  const [observationCursor, setObservationCursor] = useState<string | null>(null);
  const params = { generation: manifest.generation, as_of_chapter: manifest.as_of_chapter };
  const binding = manifest.bindings.items.find(item => item.binding_id === page.bindingId);
  const requested = page.view ?? binding?.capabilities.find(view => view !== "content") ?? "content";
  const { data, loading, error } = useOverviewResource<BindingPage>(page.workspaceId, `/bindings/${page.bindingId}`, { ...params, view: requested, cursor });
  const detail = useOverviewResource<FacetDetail>(page.workspaceId, page.facetId ? `/bindings/${page.bindingId}/facets/${page.facetId}` : null, { ...params, cursor: observationCursor });
  const relations = useOverviewResource<RelationPage>(page.workspaceId, page.facetId && detail.data?.facet.kind === "character" ? "/relations" : null, { ...params, binding_id: page.bindingId, facet_id: page.facetId, cursor: relationCursor });
  useEffect(() => { setCursor(null); setRelationCursor(null); setObservationCursor(null); }, [page.bindingId, page.view, page.facetId, manifest.generation]);
  const select = (ref: FacetRef) => onNavigate({ ...page, bindingId: ref.source.binding_id, facetId: ref.facet_id, eventId: undefined });
  const view = data?.binding.capabilities.includes(requested) ? requested : "content";
  useEffect(() => { if (data && view !== requested) onNavigate({ ...page, view: "content", facetId: undefined }); }, [data, view, requested]);
  return <section className="overview-module">
    <div className="overview-section-head"><h2>{data?.binding.display_name ?? binding?.display_name ?? "模块"}</h2>
      {data && <a className="overview-source-link" href={pageHash({ kind: "library", moduleId: data.binding.module.module_id, revisionId: data.binding.module.revision_id, workspaceContextId: page.workspaceId })}>打开模块</a>}
    </div>
    {data && data.binding.capabilities.length > 1 && <nav className="overview-local-nav" aria-label="模块视图">{data.binding.capabilities.map(item => <Button key={item} size="compact" variant={view === item ? "primary" : "quiet"} aria-pressed={view === item} onClick={() => onNavigate({ ...page, view: item, facetId: undefined })}>{LABEL[item]}</Button>)}</nav>}
    {loading && <p role="status">正在读取模块…</p>}{error && <p role="alert">{error}</p>}
    {data?.binding.availability === "unavailable" && <p role="status">{data.binding.diagnostic ?? "模块库当前不可用"}</p>}
    {data && <div className="overview-module-columns"><div className="overview-card-list">
      {data.items.map((item, index) => "ref" in item ? <button className="overview-object-card" key={item.ref.facet_id} aria-pressed={page.facetId === item.ref.facet_id} onClick={() => select(item.ref)}><strong>{item.name}</strong><p>{item.summary}</p></button> : <ModuleRecord key={item.entry_id + item.digest} item={item} binding={data.binding} initialOpen={index === 0} />)}
      {!data.items.length && data.binding.availability === "available" && <p className="overview-muted">暂无可展示的对象。可以先阅读模块内容。</p>}
      <div className="overview-page-actions">{cursor && <Button size="compact" onClick={() => setCursor(null)}>回到开头</Button>}{data.next_cursor && <Button size="compact" onClick={() => setCursor(data.next_cursor)}>下一页</Button>}</div>
    </div>
    {page.facetId && <aside className="overview-object-detail" aria-label="对象详情">
      {detail.loading && <p role="status">正在读取详情…</p>}{detail.error && <><p role="alert">{detail.error}</p><Button size="compact" onClick={() => onNavigate({ ...page, facetId: undefined, view: "content" })}>回到模块内容</Button></>}
      {detail.data && <><div className="overview-section-head"><h2>{detail.data.facet.name}</h2><Button size="compact" variant="quiet" onClick={() => onNavigate({ ...page, facetId: undefined })}>收起</Button></div>
        <p>{detail.data.facet.summary}</p><Button size="compact" variant="quiet" onClick={() => onEvidence({ source: detail.data!.facet.ref.source, evidence: detail.data!.facet.evidence })}>作者设定依据</Button>
        {relations.data && <CharacterRelations data={relations.data} selectedId={page.facetId} onSelect={select} onEvidence={index => { const relation = relations.data!.items[index]; onEvidence(relation.nature === "written" && relation.path && relation.digest ? { path: relation.path, digest: relation.digest, evidence: relation.evidence } : { source: relation.left.source, evidence: relation.evidence }); }} />}
        {relations.error && <p role="alert">{relations.error}</p>}
        <div className="overview-page-actions">{relationCursor && <Button size="compact" onClick={() => setRelationCursor(null)}>关系开头</Button>}{relations.data?.next_cursor && <Button size="compact" onClick={() => setRelationCursor(relations.data!.next_cursor)}>更多关系</Button>}</div>
        <h3>已写进展 · 截至第 {manifest.as_of_chapter} 章</h3>
        {!detail.data.observations.items.length && <p className="overview-muted">暂无有依据的正文关联。</p>}
        <ol className="overview-observations">{detail.data.observations.items.map((observation, i) => <li key={i}><span>第 {observation.chapter} 章 · {MODE[observation.mode] ?? observation.mode}</span><p>{observation.text}</p><Button size="compact" variant="quiet" onClick={() => onNavigate({ ...page, bindingId: undefined, facetId: undefined, eventId: observation.event_id, order: "chapter" })}>查看事件</Button><Button size="compact" variant="quiet" onClick={() => onEvidence({ path: observation.path, digest: observation.digest, evidence: observation.evidence })}>正文依据</Button></li>)}</ol>
        <div className="overview-page-actions">{observationCursor && <Button size="compact" onClick={() => setObservationCursor(null)}>进展开头</Button>}{detail.data.observations.next_cursor && <Button size="compact" onClick={() => setObservationCursor(detail.data!.observations.next_cursor)}>更多进展</Button>}</div>
      </>}
    </aside>}
    </div>}
  </section>;
}
export const MODE: Record<string, string> = { narration: "叙述", testimony: "证词", recollection: "回忆", hypothesis: "推测", dream: "梦", unknown: "未知" };

function ModuleRecord({ item, binding, initialOpen }: { item: ContentRecord; binding: BindingPage["binding"]; initialOpen: boolean }) {
  const [opened, setOpened] = useState(initialOpen);
  const [content, setContent] = useState<{ text: string; next: number | null } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const key = JSON.stringify([binding.module, item.entry_id, item.digest]);
  useEffect(() => { if (!opened) return; let alive = true; setContent(null); setError(""); setBusy(true);
    void fetchRecord(binding.module.module_id, binding.module.revision_id, item.entry_id).then(record => { if (!alive) return; if (record.digest !== item.digest) throw new Error("模块来源已经改变，请重新读取总览。"); setContent({ text: entryBody(record.text), next: record.next_cursor }); }).catch(cause => { if (alive) setError(errorMessage(cause, "模块内容")); }).finally(() => { if (alive) setBusy(false); });
    return () => { alive = false; };
  }, [key, opened]);
  async function more() {
    if (content?.next == null || busy) return;
    setBusy(true);
    try { const record = await fetchRecord(binding.module.module_id, binding.module.revision_id, item.entry_id, { start: content.next }); if (record.digest !== item.digest) throw new Error("模块来源已经改变，请重新读取总览。"); setContent(old => old ? { text: old.text + record.text, next: record.next_cursor } : old); } catch (cause) { setError(errorMessage(cause, "模块内容")); } finally { setBusy(false); }
  }
  return <article className="overview-content-card"><div className="overview-section-head"><h3>{item.title}</h3><Button size="compact" variant="quiet" onClick={() => setOpened(value => !value)}>{opened ? "收起内容" : "阅读内容"}</Button></div>{opened && error && <p role="alert">{error}</p>}{opened && content && <ChatMarkdown text={content.text} wide />}{opened && busy && <p role="status">正在读取…</p>}{opened && content?.next != null && <Button size="compact" disabled={busy} onClick={() => void more()}>继续阅读</Button>}</article>;
}

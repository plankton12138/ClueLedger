import { useEffect, useRef, useState } from "react";
import { Clock3, Layers3, RefreshCw } from "lucide-react";
import { type BindingPage, type EventPage, type Manifest } from "../../api/storyOverview";
import { pageHash, type WorkspacePage } from "../../state/navigation";
import { useOverviewTasks } from "../../state/storyOverview";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { useModelsStore } from "../../state/models";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import { useOverviewResource } from "./useOverviewResource";
import OverviewEvidence, { type EvidenceSelection } from "./OverviewEvidence";
import ModuleOverview, { MODE } from "./ModuleOverview";

type OverviewPage = Extract<WorkspacePage, { kind: "overview" }>;
export default function StoryOverviewPage({ page, onNavigate, onOpenSource, onOpenActivity }: { page: OverviewPage; onNavigate(page: WorkspacePage): void; onOpenSource(path: string): void; onOpenActivity(): void }) {
  const [refresh, setRefresh] = useState(0);
  const [chapterCursor, setChapterCursor] = useState<string | null>(null);
  const [bindingCursor, setBindingCursor] = useState<string | null>(null);
  const [eventCursor, setEventCursor] = useState<string | null>(null);
  const [evidence, setEvidence] = useState<EvidenceSelection | null>(null);
  const assemblyEpoch = useModuleLibraryStore(state => state.assemblyEpoch);
  const modelAvailable = useModelsStore(state => state.selection.available);
  const tasks = useOverviewTasks();
  const task = tasks.workspaces[page.workspaceId];
  const status = task?.progress?.run_status;
  const running = Boolean(task?.turnId && (!status || ["running", "accepted", "stop_requested"].includes(status)));
  const { data, loading, error } = useOverviewResource<Manifest>(page.workspaceId, "", { as_of_chapter: page.chapter, chapter_cursor: chapterCursor, binding_cursor: bindingCursor }, refresh + assemblyEpoch);
  const events = useOverviewResource<EventPage>(page.workspaceId, data && !page.bindingId ? "/events" : null, { generation: data?.generation, as_of_chapter: data?.as_of_chapter, order: page.order, cursor: eventCursor, event_id: page.eventId });
  const selectedBinding = useOverviewResource<BindingPage>(page.workspaceId, data && page.bindingId && !data.bindings.items.some(b => b.binding_id === page.bindingId) ? `/bindings/${page.bindingId}` : null, { generation: data?.generation, as_of_chapter: data?.as_of_chapter, view: "content", limit: 1 });
  const scopeKey = JSON.stringify([page.workspaceId, page.chapter, page.order, page.bindingId, page.eventId]);
  const sourceGeneration = useRef<string>();
  useEffect(() => { setEventCursor(null); setEvidence(null); }, [scopeKey]);
  useEffect(() => { if (data && sourceGeneration.current !== data.generation) { sourceGeneration.current = data.generation; setEventCursor(null); setEvidence(null); } }, [data?.generation]);
  useEffect(() => { setChapterCursor(null); setBindingCursor(null); }, [page.workspaceId, page.chapter]);
  useEffect(() => { if (data && page.chapter !== null && page.chapter !== data.as_of_chapter) onNavigate({ ...page, chapter: data.as_of_chapter, eventId: undefined }); }, [data?.as_of_chapter, page.chapter]);
  useEffect(() => {
    const focus = () => setRefresh(value => value + 1);
    window.addEventListener("focus", focus);
    return () => window.removeEventListener("focus", focus);
  }, []);
  useEffect(() => { if (data?.active_update) tasks.recover(page.workspaceId, data.active_update.turn_id); }, [data?.active_update?.turn_id, page.workspaceId]);
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(() => void tasks.poll(page.workspaceId), 1000);
    void tasks.poll(page.workspaceId);
    return () => window.clearInterval(timer);
  }, [page.workspaceId, task?.turnId, running]);
  useEffect(() => { if (status && !["running", "accepted", "stop_requested"].includes(status)) setRefresh(value => value + 1); }, [status]);
  function openTimeline() { onNavigate({ ...page, bindingId: undefined, facetId: undefined, eventId: undefined, view: undefined }); }
  function openBinding(bindingId: string) { onNavigate({ ...page, bindingId, facetId: undefined, eventId: undefined, view: undefined }); }
  const navBindings = data?.bindings.items ?? [];
  const currentBinding = navBindings.find(b => b.binding_id === page.bindingId) ?? selectedBinding.data?.binding;
  const visibleBindings = currentBinding && !navBindings.slice(0, 5).some(b => b.binding_id === currentBinding.binding_id) ? [...navBindings.slice(0, 4), currentBinding] : navBindings.slice(0, 5);
  const otherBindings = navBindings.filter(b => !visibleBindings.some(item => item.binding_id === b.binding_id));
  return <div className="story-overview workbench">
    <header className="overview-toolbar"><div><h1>故事总览</h1></div><div className="overview-toolbar-actions">
      <label>查看到 <select className="ui-input" aria-label="查看到第几章" value={page.chapter ?? "latest"} onChange={event => onNavigate({ ...page, chapter: event.target.value === "latest" ? null : Number(event.target.value), eventId: undefined, facetId: undefined })}>
        <option value="latest">最新章节</option>{page.chapter !== null && !data?.chapters.items.some(item => item.chapter === page.chapter) && <option value={page.chapter}>第 {page.chapter} 章</option>}{data?.chapters.items.map(item => <option key={item.path} value={item.chapter}>{item.title}</option>)}
      </select></label>
      {running ? <Button size="compact" onClick={() => void tasks.stop(page.workspaceId)}>停止更新</Button> : <Button size="compact" variant="primary" disabled={!data || loading || task?.submitting || Boolean(task?.pending)} onClick={() => { if (!modelAvailable && data?.update_summary.changed_targets) useModelsStore.getState().openSettings(); else if (data) void tasks.start(page.workspaceId, data); }}><RefreshCw size={14} aria-hidden="true" />{task?.submitting ? "正在受理…" : "更新总览"}</Button>}
      <details className="overview-more"><summary>更多</summary><div>
        <Button size="compact" variant="quiet" onClick={() => setRefresh(value => value + 1)}>重新读取</Button>
        <Button size="compact" variant="quiet" onClick={onOpenActivity}>活动记录</Button>
        <Button size="compact" variant="quiet" onClick={() => useModelsStore.getState().openSettings()}>模型设置</Button>
        <a href={pageHash({ kind: "materials", workspaceId: page.workspaceId, section: "characters", chapter: page.chapter, order: "chapter" })}>旧版资料</a>
        <a href={pageHash({ kind: "library", workspaceContextId: page.workspaceId })}>管理模块</a>
      </div></details>
    </div></header>
    {(error || task?.error) && <StatusMessage tone="error" action={<Button size="compact" onClick={() => { tasks.clear(page.workspaceId); setRefresh(value => value + 1); }}>重新读取</Button>}>{error || task?.error}</StatusMessage>}
    {task?.pending && !task.submitting && <StatusMessage tone="warning" action={<Button size="compact" onClick={() => void tasks.retry(page.workspaceId)}>重试原请求</Button>}>尚未确认更新请求的受理结果。</StatusMessage>}
    {task?.notice && <div className="overview-task" role="status">{task.notice}</div>}
    {task?.progress && <div className="overview-task" role="status">{running ? "正在更新" : task.progress.outcome === "complete" ? "总览已更新" : task.progress.run_status === "stopped" ? "更新已停止" : "部分内容尚未更新"} · {task.progress.completed} / {task.progress.total}{task.progress.errors.map((message, i) => <p key={i}>{message}</p>)}</div>}
    <nav className="overview-nav" aria-label="总览内容"><Button size="compact" variant={!page.bindingId ? "primary" : "quiet"} aria-pressed={!page.bindingId} onClick={openTimeline}><Clock3 size={15} aria-hidden="true" />时间线</Button>
      {visibleBindings.map(binding => <Button key={binding.binding_id} size="compact" variant={page.bindingId === binding.binding_id ? "primary" : "quiet"} aria-pressed={page.bindingId === binding.binding_id} onClick={() => openBinding(binding.binding_id)}><Layers3 size={14} aria-hidden="true" />{binding.display_name}</Button>)}
      {(navBindings.length > 5 || data?.bindings.next_cursor || bindingCursor) && <details className="overview-more"><summary>更多模块</summary><div>{otherBindings.map(binding => <Button key={binding.binding_id} size="compact" variant="quiet" onClick={() => openBinding(binding.binding_id)}>{binding.display_name}</Button>)}{data?.bindings.next_cursor && <Button size="compact" onClick={() => setBindingCursor(data.bindings.next_cursor)}>下一页模块</Button>}{bindingCursor && <Button size="compact" onClick={() => setBindingCursor(null)}>回到开头</Button>}</div></details>}
    </nav>
    <div className={`overview-body ${evidence ? "has-evidence" : ""}`}><main className="overview-main">
      {loading && <p role="status" className="overview-empty">正在读取总览…</p>}
      {data && (page.bindingId ? <ModuleOverview key={page.bindingId} page={page} manifest={data} onNavigate={onNavigate} onEvidence={setEvidence} /> : <>
        <div className="overview-section-head"><h2>时间线</h2><div className="overview-local-nav"><Button size="compact" variant={page.order === "chapter" ? "primary" : "quiet"} aria-pressed={page.order === "chapter"} onClick={() => onNavigate({ ...page, order: "chapter" })}>按章节</Button><Button size="compact" variant={page.order === "story" ? "primary" : "quiet"} aria-pressed={page.order === "story"} onClick={() => onNavigate({ ...page, order: "story" })}>按故事时间</Button></div></div>
        {page.eventId && <Button size="compact" variant="quiet" onClick={openTimeline}>返回完整时间线</Button>}
        {events.loading && <p role="status">正在读取事件…</p>}{events.error && <p role="alert">{events.error}</p>}
        {events.data?.conflict_count ? <p className="overview-notice">有 {events.data.conflict_count} 组时间冲突，未强行合并顺序。</p> : null}
        {events.data?.items.length === 0 && <div className="overview-empty"><Clock3 size={28} aria-hidden="true" /><h3>{page.eventId ? "该事件当前不可用" : page.order === "story" ? "还没有可排列的故事事件" : "从第一章开始"}</h3><p>{page.order === "story" ? "可切回章节顺序阅读原文，或更新总览整理事件。" : "写下正文后，章节会出现在这里。"}</p></div>}
        <ol className="overview-timeline">{events.data?.items.map((row, i) => <li key={`${row.event?.id ?? row.chapter?.path}:${i}`} className="overview-timeline-item"><span className="overview-timeline-dot" /><div className="overview-timeline-meta">{row.chapter?.title ?? `第 ${row.event?.chapter} 章`}{row.group === "unknown" && <span>时间未确定</span>}{row.group.startsWith("story:") && <span>先后关系组 {Number(row.group.split(":")[1]) + 1} · 组间先后未确定</span>}{row.group.startsWith("conflict") && <span>时间冲突</span>}{row.event && <span>{row.event.mode_label || MODE[row.event.mode]}</span>}</div>
          <article className="overview-event-card"><h3>{row.event?.text || row.chapter?.summary || row.chapter?.title}</h3>{!row.event && <p className="overview-muted">{row.chapter?.summary || "尚未整理，可直接阅读原文。"}</p>}{row.chapter?.status === "stale" && <p className="overview-notice">原文已改动，需要更新</p>}
            <div className="overview-event-actions">{row.related_facets?.map(facet => <Button key={facet.ref.facet_id} size="compact" variant="quiet" onClick={() => onNavigate({ ...page, bindingId: facet.ref.source.binding_id, facetId: facet.ref.facet_id, eventId: undefined, view: facet.kind === "character" ? "characters" : "clues" })}>{facet.name}</Button>)}{row.related_more && <span>更多关联请在模块中查看</span>}{row.event?.evidence.map((source, index) => <Button key={index} size="compact" variant="quiet" onClick={() => setEvidence({ path: source.path, digest: source.digest, evidence: source })}>原文依据{index > 0 ? index + 1 : ""}</Button>)}{!row.event && row.chapter && <Button size="compact" variant="quiet" onClick={() => onOpenSource(row.chapter!.path)}>阅读正文</Button>}</div>
          </article></li>)}</ol>
        <div className="overview-page-actions">{eventCursor && <Button size="compact" onClick={() => setEventCursor(null)}>回到开头</Button>}{events.data?.next_cursor && <Button size="compact" onClick={() => setEventCursor(events.data!.next_cursor)}>下一页事件</Button>}{data.chapters.next_cursor && <Button size="compact" onClick={() => setChapterCursor(data.chapters.next_cursor)}>更多章节选项</Button>}{chapterCursor && <Button size="compact" onClick={() => setChapterCursor(null)}>章节选项开头</Button>}</div>
      </>)}
    </main>{evidence && <OverviewEvidence key={JSON.stringify(evidence)} workspaceId={page.workspaceId} selection={evidence} onClose={() => setEvidence(null)} onOpenSource={onOpenSource} />}</div>
  </div>;
}

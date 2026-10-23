import { useEffect, useRef, useState } from "react";
import { activityError, activityPage, activityRequest, activityStream, exportActivity, mergeActivity,
  type ActivityEvent, type ActivityFilters, type ActivityPage } from "../../api/activity";
import { useWorkspaceStore } from "../../state/workspace";
import { useConversationsStore } from "../../state/conversations";
import ChangesDrawer from "./ChangesDrawer";
import FontSizeControl from "./FontSizeControl";

const CATEGORIES: Record<string, string> = {
  "": "全部", task: "任务", model: "模型", tool: "工具", context: "材料与范围",
  file: "文件操作", state: "状态与检查", version: "版本",
};
const STATUS: Record<string, string> = {
  started: "已开始", running: "进行中", completed: "完成", failed: "失败", stopped: "已停止",
  interrupted: "中断", partial: "部分完成", truncated: "输出截断",
};
const emptyStats = { events: 0, models: 0, tools: 0, errors: 0 };

function Detail({ workspace, event, jump, showVersions }: {
  workspace: string; event: ActivityEvent; jump: (conversation: string) => void;
  showVersions: () => void;
}) {
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null);
  const [attachment, setAttachment] = useState<unknown>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    activityRequest<Record<string, unknown>>(workspace, `/${encodeURIComponent(event.event_id)}`)
      .then(value => { if (live) setDetail(value); })
      .catch(reason => { if (live) setError(activityError(reason)); });
    return () => { live = false; };
  }, [workspace, event.event_id]);
  async function load(key: string) {
    try { setAttachment(await activityRequest(workspace, `/attachments/${key}`)); }
    catch (reason) { setError(activityError(reason)); }
  }
  return <div className="space-y-2 border-t border-[#e2dbc6] bg-white/70 p-3">
    <div className="flex flex-wrap gap-2">
      {event.conversation_id && <button className="btn btn-gray px-2 py-1"
        onClick={() => jump(event.conversation_id)}>跳转对话</button>}
      {typeof detail?.path === "string" && <button className="btn btn-gray px-2 py-1"
        onClick={() => { void useWorkspaceStore.getState().openPath(detail.path as string); useWorkspaceStore.getState().closeDrawer(); }}>打开文件</button>}
      {typeof detail?.commit === "string" && <button className="btn btn-gray px-2 py-1"
        onClick={() => { void useWorkspaceStore.getState().showCommit({ commit: detail.commit as string,
          subject: "关联提交", at: event.at }); showVersions(); }}>查看关联版本差异</button>}
      {["input_detail", "result_detail"].map(key => typeof detail?.[key] === "string" &&
        <button key={key} className="btn btn-gray px-2 py-1"
          onClick={() => void load(detail[key] as string)}>
          {key === "input_detail" ? "查看输入详情" : "查看结果详情"}</button>)}
    </div>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5">
      {detail ? JSON.stringify(detail, null, 2) : "正在读取详情…"}
    </pre>
    {attachment !== null && <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all border-t pt-2">
      {typeof attachment === "string" ? attachment : JSON.stringify(attachment, null, 2)}</pre>}
  </div>;
}

export default function ActivityPanel() {
  const open = useWorkspaceStore(state => state.drawerOpen);
  const workspace = useWorkspaceStore(state => state.currentId);
  const selected = useConversationsStore(state => state.selectedId);
  const conversations = useConversationsStore(state => state.items);
  const [tab, setTab] = useState("events");
  const [filters, setFilters] = useState<ActivityFilters>({});
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [stats, setStats] = useState(emptyStats);
  const [run, setRun] = useState<ActivityPage["run"]>(null);
  const [before, setBefore] = useState<number | null>(null);
  const [expanded, setExpanded] = useState("");
  const [connection, setConnection] = useState("连接中");
  const [error, setError] = useState("");
  const [follow, setFollow] = useState(true);
  const [capture, setCapture] = useState(false);
  const [retention, setRetention] = useState(30);
  const [settingsReady, setSettingsReady] = useState(false);
  const [savingSettings, setSavingSettings] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);
  const [notice, setNotice] = useState("");
  const [unseen, setUnseen] = useState(false);
  const [exportDetails, setExportDetails] = useState(false);
  const [generation, setGeneration] = useState(0);
  const [loading, setLoading] = useState(false);
  const scroll = useRef<HTMLDivElement>(null);
  const cursor = useRef("");
  const epoch = useRef(0);
  const browsing = useRef(false);
  const settingsEpoch = useRef(0);
  const dialog = useRef<HTMLElement>(null);
  const scope = JSON.stringify(filters);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement;
    dialog.current?.focus();
    function keydown(event: KeyboardEvent) {
      if (event.key === "Escape") { event.preventDefault(); useWorkspaceStore.getState().closeDrawer(); }
      if (event.key !== "Tab") return;
      const controls = [...(dialog.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex="0"]') ?? [])]
        .filter(node => node.getClientRects().length > 0);
      const first = controls[0], last = controls[controls.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) {
        event.preventDefault(); last?.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) {
        event.preventDefault(); first?.focus();
      }
    }
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, [open]);

  useEffect(() => {
    if (!open || !workspace) return;
    setFilters(selected ? { conversation_id: selected } : {});
    setTab("events");
    setExpanded("");
    let live = true;
    setConfirmClear(false); setNotice(""); setSettingsReady(false); setSavingSettings(false);
    settingsEpoch.current += 1;
    activityRequest<{ capture_inputs: boolean; retention_days: number }>(workspace, "/settings")
      .then(value => { if (live) { setCapture(value.capture_inputs); setRetention(value.retention_days); setSettingsReady(true); } })
      .catch(reason => { if (live) setError(activityError(reason)); });
    return () => { live = false; settingsEpoch.current += 1; };
  }, [open, workspace]); // 事件页的范围由筛选器控制，不跟随背景对话选择。

  useEffect(() => {
    if (!open || !workspace || tab !== "events") return;
    const abort = new AbortController();
    epoch.current += 1; browsing.current = false;
    setEvents([]); setBefore(null); setStats(emptyStats); setRun(null); setError("");
    setFollow(true); setUnseen(false); setLoading(false);
    cursor.current = ""; setConnection("连接中");
    let initial = true;
    void (async () => {
      for (let attempt = 0; attempt < 4 && !abort.signal.aborted; attempt += 1) {
        try {
          for await (const page of activityStream(workspace, JSON.parse(scope), cursor.current, abort.signal)) {
            if (abort.signal.aborted) break;
            cursor.current = page.cursor;
            if (!browsing.current || page.reset) {
              if (page.reset) browsing.current = false;
              setEvents(current => {
                const merged = mergeActivity(current, page.items, page.reset);
                if (merged.length > 1000) setBefore(merged[merged.length - 1000]!.id);
                return merged.slice(-1000);
              });
            } else if (page.items.length) setUnseen(true);
            setStats(page.stats); setRun(page.run); setConnection("实时连接中");
            if (initial || page.reset) setBefore(page.next_before);
            initial = false;
          }
        } catch (reason) {
          if (abort.signal.aborted) return;
          if (attempt === 3) { setConnection("连接中断"); setError(activityError(reason)); return; }
          setConnection(`连接中断，正在重连（${attempt + 1}/3）`);
          await new Promise<void>(resolve => {
            const timer = setTimeout(resolve, [1000, 2000, 5000][attempt]);
            abort.signal.addEventListener("abort", () => { clearTimeout(timer); resolve(); }, { once: true });
          });
        }
      }
    })();
    return () => { epoch.current += 1; abort.abort(); };
  }, [open, workspace, tab, scope, generation]);

  useEffect(() => {
    if (follow && scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight;
  }, [events, follow]);

  if (!open || !workspace) return null;
  const change = (key: keyof ActivityFilters, value: string | boolean) =>
    setFilters(current => ({ ...current, [key]: value }));
  async function older() {
    if (before === null || loading) return;
    setLoading(true);
    const identity = cursor.current.split(":")[0];
    const requestEpoch = epoch.current;
    browsing.current = true; setFollow(false);
    try {
      const page = await activityPage(workspace!, filters, before);
      if (requestEpoch !== epoch.current || identity !== cursor.current.split(":")[0]) return;
      setEvents(current => mergeActivity(current, page.items).slice(0, 1000)); setBefore(page.next_before);
    } catch (reason) { if (requestEpoch === epoch.current) setError(activityError(reason)); }
    finally { if (requestEpoch === epoch.current) setLoading(false); }
  }
  async function download() {
    try {
      const blob = await exportActivity(workspace!, filters, exportDetails);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url; link.download = "activity.jsonl";
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) { setError(activityError(reason)); }
  }
  async function configure(value: boolean, days = retention) {
    const currentEpoch = settingsEpoch.current;
    const previousCapture = capture, previousRetention = retention;
    setCapture(value); setRetention(days); setSavingSettings(true);
    try {
      const result = await activityRequest<{ capture_inputs: boolean; retention_days: number }>(workspace!, "/settings", {
        method: "PUT", body: JSON.stringify({ capture_inputs: value, retention_days: days }),
      });
      if (currentEpoch === settingsEpoch.current) { setCapture(result.capture_inputs); setRetention(result.retention_days); }
    } catch (reason) {
      if (currentEpoch === settingsEpoch.current) { setCapture(previousCapture); setRetention(previousRetention); setError(activityError(reason)); }
    } finally { if (currentEpoch === settingsEpoch.current) setSavingSettings(false); }
  }
  function jump(conversation: string) {
    void useConversationsStore.getState().select(conversation);
    useWorkspaceStore.getState().closeDrawer();
  }
  return <div className="fixed inset-0 z-40 bg-black/30 p-2 sm:p-5">
    <section ref={dialog} tabIndex={-1} role="dialog" aria-modal="true" aria-label="活动记录"
      className="flex h-full flex-col rounded border border-[#cfc7ae] bg-[#fbf8ef] text-xs text-[#4b4737] shadow-2xl">
      <header className="flex flex-none flex-wrap items-center gap-3 border-b border-[#e2dbc6] px-4 py-3">
        <h2 className="font-serif text-lg font-bold">活动记录</h2>
        <button className={`btn px-3 py-1 ${tab === "events" ? "btn-gold" : "btn-gray"}`}
          onClick={() => setTab("events")}>事件流</button>
        <button className={`btn px-3 py-1 ${tab === "versions" ? "btn-gold" : "btn-gray"}`}
          onClick={() => setTab("versions")}>文件版本</button>
        <div className="flex-1" />
        <FontSizeControl />
        <button className="btn btn-gray px-3 py-1" onClick={() => useWorkspaceStore.getState().closeDrawer()}>关闭</button>
      </header>
      {tab === "versions" ? <ChangesDrawer embedded /> : <>
        <div className="flex min-h-0 flex-1 flex-col-reverse md:flex-row">
          <main ref={scroll} className="min-h-0 min-w-0 flex-1 overflow-auto"
            onScroll={() => { const el = scroll.current; if (el && el.scrollHeight - el.scrollTop - el.clientHeight > 80) setFollow(false); }}>
            <div className="sticky top-0 z-10 grid grid-cols-[90px_70px_1fr] gap-2 border-b border-[#e2dbc6] bg-[#fbf8ef] px-3 py-2 font-bold sm:grid-cols-[110px_90px_1fr_80px]">
              <span>时间</span><span>类型</span><span>事件内容</span><span className="hidden sm:block">操作</span>
            </div>
            {before !== null && <button className="btn btn-gray m-3 px-3 py-1" disabled={loading}
              onClick={() => void older()}>加载更早记录</button>}
            {events.length === 0 && <p className="p-6">{connection === "连接中" ? "正在读取记录…" : "没有符合条件的事件。"}</p>}
            {events.map((event, index) => <article key={event.event_id}>
              {(index === 0 || events[index - 1]?.turn_id !== event.turn_id ||
                events[index - 1]?.conversation_id !== event.conversation_id) &&
                <div className="sticky border-b bg-[#eee8d8] px-3 py-2 font-bold"
                  style={{ top: "calc(1rem * var(--font-scale, 1) + 1rem + 1px)" }}>
                  <span className="mr-3">{event.at ? new Date(event.at).toLocaleDateString("zh-CN") : ""}</span>
                  {event.turn_id ? `任务 ${event.turn_id.slice(-10)}` : "作品操作"}
                  {event.conversation_id && <span className="ml-3 font-normal">{conversations.find(item => item.id === event.conversation_id)?.title ?? event.conversation_id}</span>}
                </div>}
              <button aria-expanded={expanded === event.event_id} className="grid w-full grid-cols-[90px_70px_1fr] gap-2 border-b border-[#e2dbc6] p-3 text-left hover:bg-white/60 sm:grid-cols-[110px_90px_1fr_80px]"
                onClick={() => setExpanded(expanded === event.event_id ? "" : event.event_id)}>
                <time title={event.at}>{event.at ? new Date(event.at).toLocaleTimeString("zh-CN", { hour12: false }) + "." + String(new Date(event.at).getMilliseconds()).padStart(3, "0") : "未记录"}</time>
                <span>{CATEGORIES[event.category] ?? event.category}</span>
                <span className={event.status === "failed" ? "text-red-700" : ""}>
                  {event.summary} <span className="text-[#8a8371]">· {STATUS[event.status] ?? event.status}</span>
                  {event.duration_ms != null && <span className="ml-2">{event.duration_ms} ms</span>}
                </span>
                <span className="hidden text-right sm:inline">{expanded === event.event_id ? "收起" : "详情"}</span>
              </button>
              {expanded === event.event_id && <Detail workspace={workspace} event={event} jump={jump} showVersions={() => setTab("versions")} />}
            </article>)}
          </main>
          <aside className="max-h-48 flex-none space-y-4 overflow-auto border-b border-[#e2dbc6] p-4 md:max-h-none md:w-72 md:border-b-0 md:border-l">
            {run && <div className="space-y-1 border-b border-[#e2dbc6] pb-3">
              <strong>最近任务 · {STATUS[run.status] ?? run.status}</strong>
              <p>{run.model ?? "模型未记录"}</p>
              <p>{Math.max(0, Math.round((new Date(run.finished_at ?? Date.now()).getTime() - new Date(run.started_at).getTime()) / 1000))} 秒</p>
            </div>}
            <div className="grid grid-cols-4 gap-2 md:grid-cols-2">
              {([["事件", stats.events], ["模型请求", stats.models], ["工具调用", stats.tools], ["失败事件", stats.errors]] as const).map(([label, value]) =>
                <div key={label} className="rounded border border-[#e2dbc6] p-2">{label}<strong className="block text-xl">{value}</strong></div>)}
            </div>
            <label className="block">查看范围<select aria-label="活动范围" className="mt-1 w-full rounded border p-2"
              value={filters.conversation_id ?? ""} onChange={e => change("conversation_id", e.target.value)}>
              <option value="">当前作品全部活动</option>{conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select></label>
            <label className="block">任务标识<input aria-label="任务标识" className="mt-1 w-full rounded border p-2" value={filters.turn_id ?? ""}
              onChange={e => change("turn_id", e.target.value)} /></label>
            <label className="block">搜索内容或文件路径<input aria-label="搜索活动" type="search" className="mt-1 w-full rounded border p-2"
              value={filters.q ?? ""} onChange={e => change("q", e.target.value)} /></label>
            <label className="block">事件类型<select aria-label="事件类型" className="mt-1 w-full rounded border p-2"
              value={filters.category ?? ""} onChange={e => change("category", e.target.value)}>
              {Object.entries(CATEGORIES).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            <label className="block">结果<select aria-label="事件结果" className="mt-1 w-full rounded border p-2"
              value={filters.status ?? ""} onChange={e => change("status", e.target.value)}>
              <option value="">全部</option>{Object.entries(STATUS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
            {(["since", "until"] as const).map(key => <label className="block" key={key}>{key === "since" ? "开始时间" : "结束时间"}
              <input aria-label={key === "since" ? "开始时间" : "结束时间"} type="datetime-local" className="mt-1 w-full rounded border p-2"
                onChange={e => change(key, e.target.value ? new Date(e.target.value).toISOString() : "")} /></label>)}
            <label className="block"><input type="checkbox" checked={filters.verbose ?? false}
              onChange={e => change("verbose", e.target.checked)} /> 显示文字分片事件</label>
            <label className="block"><input type="checkbox" checked={capture} disabled={!settingsReady || savingSettings}
              onChange={e => void configure(e.target.checked)} /> 保存后续模型输入快照</label>
            <p className="text-[11px] leading-5 text-[#8a8371]">快照保存在本机，可能包含作品正文。清理详情保留事件摘要与版本关联。</p>
            <label className="block">详情保留时间<select aria-label="详情保留时间" className="mt-1 w-full rounded border p-2" value={retention} disabled={!settingsReady || savingSettings}
              onChange={e => void configure(capture, Number(e.target.value))}>
              <option value={7}>7 天</option><option value={30}>30 天</option><option value={0}>直到手动清理</option>
              {![0, 7, 30].includes(retention) && <option value={retention}>{retention} 天</option>}
            </select></label>
            {!confirmClear ? <button className="btn btn-gray px-3 py-1" onClick={() => setConfirmClear(true)}>清理已保存详情</button> :
              <div><p className="mb-2">输入与工具结果详情将被删除，无法恢复。</p>
                <button className="btn btn-gray mr-2 px-3 py-1" onClick={() => setConfirmClear(false)}>取消</button>
                <button className="btn btn-gold px-3 py-1" onClick={() => void activityRequest<{ removed: number }>(workspace, "/details", { method: "DELETE" })
                  .then(value => { setNotice(`已清理 ${value.removed} 份详情。`); setExpanded(""); setConfirmClear(false); })
                  .catch(reason => setError(activityError(reason)))}>确认清理详情</button></div>}
            <button className="btn btn-gray ml-2 px-3 py-1" onClick={() => void activityRequest(workspace, "/rebuild", { method: "POST" })
              .then(() => setGeneration(value => value + 1)).catch(reason => setError(activityError(reason)))}>重建查询索引</button>
          </aside>
        </div>
        <footer className="flex flex-none flex-wrap items-center gap-3 border-t border-[#e2dbc6] px-4 py-3">
          <span role="status">{connection}</span><span>已载入 {events.length} 条{unseen ? " · 有新事件" : ""}</span>
          <button className="btn btn-gray px-2 py-1" onClick={() => setGeneration(value => value + 1)}>重新连接</button>
          <div className="flex-1" />
          <label><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> 自动跟随</label>
          <button className="btn btn-gray px-2 py-1" onClick={() => { if (browsing.current) setGeneration(value => value + 1); setFollow(true); }}>回到最新</button>
          <label><input type="checkbox" checked={exportDetails} onChange={e => setExportDetails(e.target.checked)} /> 导出包含已保存详情</label>
          <button className="btn btn-gold px-3 py-1" onClick={() => void download()}>导出事件日志</button>
        </footer>
      </>}
      {error && <p role="alert" className="max-h-24 overflow-auto border-t p-3 text-red-700">{error}</p>}
      {notice && <p role="status" className="border-t p-3">{notice}</p>}
    </section>
  </div>;
}

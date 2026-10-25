import { Button } from "../ui/Button";
/**
 * 活动记录（0.8）：按任务分组的事件流。
 *
 * 每条事件由后端给出动作说法（action / phase / title / subject / note），这里只做三件事：
 * 把同一调用的开始与结束合成一行、按任务分组、把连续的同名文件动作折成一批。
 * 原始事件仍保留在行内，展开即可逐条查看，日志本身不改写。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { activityError, activityPage, activityRequest, activityStream, exportActivity, mergeActivity,
  type ActivityEvent, type ActivityFilters, type ActivityPage } from "../../api/activity";
import { useWorkspaceStore } from "../../state/workspace";
import { useConversationsStore } from "../../state/conversations";
import { AppDialog } from "../ui/AppDialog";
import ChangesDrawer from "./ChangesDrawer";
import FontSizeControl from "./FontSizeControl";
import {
  ACTION_LABEL,
  ACTION_TONE,
  PHASE_LABEL,
  PHASE_TONE,
  actionCounts,
  buildActivity,
  hasLegacyEvents,
  formatDuration,
  type ActivityBand,
  type ActivityRow,
} from "./activityRows";

const STATUS: Record<string, string> = {
  started: "已开始", running: "进行中", completed: "完成", failed: "失败", stopped: "已停止",
  interrupted: "中断", partial: "部分完成", truncated: "输出截断", unloaded: "未完整载入",
};
const emptyStats = { events: 0, models: 0, tools: 0, errors: 0 };

const clock = (at: string) =>
  at ? `${new Date(at).toLocaleTimeString("zh-CN", { hour12: false })}.${String(new Date(at).getMilliseconds()).padStart(3, "0")}` : "未记录";

const duration = (band: ActivityBand) => {
  const end = band.finished_at ? new Date(band.finished_at).getTime() : Date.now();
  const start = new Date(band.started_at).getTime();
  return Number.isFinite(start) && end >= start ? formatDuration(end - start) : "";
};

function ActionChip({ action }: { action: string }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-sm ${ACTION_TONE[action] ?? ACTION_TONE.other}`}>
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {ACTION_LABEL[action] ?? action}
    </span>
  );
}

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
      {event.conversation_id && <Button className="px-2 py-1"
        onClick={() => jump(event.conversation_id)}>跳转对话</Button>}
      {typeof detail?.path === "string" && <Button className="px-2 py-1"
        onClick={() => { void useWorkspaceStore.getState().openPath(detail.path as string); useWorkspaceStore.getState().closeDrawer(); }}>打开文件</Button>}
      {typeof detail?.commit === "string" && <Button className="px-2 py-1"
        onClick={() => { void useWorkspaceStore.getState().showCommit({ commit: detail.commit as string,
          subject: "关联提交", at: event.at }); showVersions(); }}>查看关联版本差异</Button>}
      {["input_detail", "result_detail"].map(key => typeof detail?.[key] === "string" &&
        <Button key={key} className="px-2 py-1"
          onClick={() => void load(detail[key] as string)}>
          {key === "input_detail" ? "查看输入详情" : "查看结果详情"}</Button>)}
    </div>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all text-sm leading-5">
      {detail ? JSON.stringify(detail, null, 2) : "正在读取详情…"}
    </pre>
    {attachment !== null && <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-all border-t pt-2">
      {typeof attachment === "string" ? attachment : JSON.stringify(attachment, null, 2)}</pre>}
  </div>;
}

function BandHeader({ band }: { band: ActivityBand }) {
  return <div className="border-y border-[#d8d0bb] bg-[#eee8d8] px-3 py-2">
    <div className="flex items-baseline gap-2">
      <span className="text-[13px] font-bold">{band.label}</span>
      {band.model && <span className="text-ui-muted">{band.model}</span>}
      <span className={`ml-auto whitespace-nowrap font-bold ${PHASE_TONE[band.status] ?? "text-mute"}`}>
        {STATUS[band.status] ?? band.status}
      </span>
    </div>
    <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 text-sm text-ui-muted">
      {band.subject && <span className="min-w-0 flex-1 truncate">{band.subject}</span>}
      <span className="whitespace-nowrap text-ui-muted">
        {duration(band)}{band.note ? ` · ${band.note}` : ""}
      </span>
    </div>
    {!band.complete && (
      <p className="mt-0.5 text-sm text-[#a1783b]">
        这一任务的开始事件在更早的记录里，先加载更早记录再看次数
      </p>
    )}
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
  const [detailEvent, setDetailEvent] = useState("");
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
  const scope = JSON.stringify(filters);
  const bands = useMemo(() => buildActivity(events), [events]);
  const counts = useMemo(() => actionCounts(bands), [bands]);
  const legacy = useMemo(() => hasLegacyEvents(events), [events]);
  const peak = Math.max(1, ...counts.map(item => item.count));

  useEffect(() => {
    if (!open || !workspace) return;
    setFilters(selected ? { conversation_id: selected } : {});
    setTab("events");
    setExpanded("");
    setDetailEvent("");
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
  function toggle(row: ActivityRow) {
    const next = expanded === row.key ? "" : row.key;
    setExpanded(next);
    setDetailEvent(next ? (row.events[row.events.length - 1]?.event_id ?? "") : "");
  }
  return <AppDialog open={open} onOpenChange={value => { if (!value) useWorkspaceStore.getState().closeDrawer(); }} title="活动记录" className="activity-dialog">
      <header className="flex flex-none flex-wrap items-center gap-3 border-b border-ui-edge px-4 py-3">
        <Button variant={tab === "events" ? "primary" : "secondary"} aria-pressed={tab === "events"}
          onClick={() => setTab("events")}>事件流</Button>
        <Button variant={tab === "versions" ? "primary" : "secondary"} aria-pressed={tab === "versions"}
          onClick={() => setTab("versions")}>文件版本</Button>
        <div className="flex-1" />
        <FontSizeControl />
      </header>
      {tab === "versions" ? <ChangesDrawer embedded /> : <>
        <div className="flex min-h-0 flex-1 flex-col-reverse md:flex-row">
          <main ref={scroll} className="min-h-0 min-w-0 flex-1 overflow-auto"
            onScroll={() => { const el = scroll.current; if (el && el.scrollHeight - el.scrollTop - el.clientHeight > 80) setFollow(false); }}>
            <div className="sticky top-0 z-10 activity-columns gap-2 border-b border-[#e2dbc6] bg-[#fbf8ef] px-3 py-2 font-bold">
              <span>时间</span><span>类型</span><span>事件内容</span><span className="text-right">状态</span>
            </div>
            {legacy && <p className="border-b border-[#e6d9b8] bg-[#fff4de] px-3 py-2 text-[12px] text-[#a1783b]">
                这份索引由旧版本服务生成，只有摘要与状态。重启服务后会重建索引，显示动作标签、对象与耗时。
              </p>}
            {before !== null && <Button className="m-3 px-3 py-1" disabled={loading}
              onClick={() => void older()}>加载更早记录</Button>}
            {events.length === 0 && <p className="p-6">{connection === "连接中" ? "正在读取记录…" : "没有符合条件的事件。"}</p>}
            {bands.map(band => <section key={band.key}>
              <BandHeader band={band} />
              {band.rows.map(row => <article key={row.key}>
                <Button aria-expanded={expanded === row.key}
                  className={`activity-columns w-full items-start gap-2 border-b border-[#e2dbc6] px-3 py-2.5 text-left hover:bg-white/60 ${
                    row.phase === "failed" ? "border-l-2 border-l-red" : ""}`}
                  onClick={() => toggle(row)}>
                  <time title={row.at} className="whitespace-nowrap text-sm text-ui-muted">{clock(row.at)}</time>
                  <span><ActionChip action={row.action} /></span>
                  <span className="min-w-0">
                    <span className="font-bold">{row.title}</span>
                    {row.subject && <span className="ml-1 break-all">{row.subject}</span>}
                    {row.note && <span className="ml-1 text-ui-muted">· {row.note}</span>}
                    {row.count > 1 && <span className="ml-1 text-ui-muted">· {row.count} 次</span>}
                  </span>
                  <span className={`whitespace-nowrap text-right ${PHASE_TONE[row.phase] ?? "text-mute"}`}>
                    {PHASE_LABEL[row.phase] ?? row.phase}
                  </span>
                </Button>
                {expanded === row.key && <div className="border-b border-[#e2dbc6] bg-white/50 px-3 py-2">
                  <ul className="space-y-1">
                    {row.events.map(item => <li key={item.event_id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <time className="text-ui-muted">{clock(item.at)}</time>
                      <span className="font-bold">{ACTION_LABEL[item.action] ?? item.action}</span>
                      <span>{item.title}</span>
                      {item.subject && <span className="break-all text-ui-muted">{item.subject}</span>}
                      {item.note && <span className="text-ui-muted">{item.note}</span>}
                      <Button className="ml-auto px-2 py-0.5 text-sm"
                        onClick={() => setDetailEvent(item.event_id)}>详情</Button>
                    </li>)}
                  </ul>
                  {detailEvent && row.events.some(item => item.event_id === detailEvent) &&
                    <Detail workspace={workspace} event={row.events.find(item => item.event_id === detailEvent)!}
                      jump={jump} showVersions={() => setTab("versions")} />}
                </div>}
              </article>)}
            </section>)}
          </main>
          <aside className="max-h-64 flex-none space-y-4 overflow-auto border-b border-[#e2dbc6] p-4 md:max-h-none md:w-80 md:border-b-0 md:border-l">
            {run && <div className="space-y-1 border-b border-[#e2dbc6] pb-3">
              <strong>最近任务 · {STATUS[run.status] ?? run.status}</strong>
              <p>{run.label ?? "写作"} · {run.model ?? "模型未记录"}</p>
              <p>{Math.max(0, Math.round((new Date(run.finished_at ?? Date.now()).getTime() - new Date(run.started_at).getTime()) / 1000))} 秒</p>
            </div>}
            <div className="grid grid-cols-4 gap-2 md:grid-cols-2">
              {([["事件", stats.events], ["模型请求", stats.models], ["工具调用", stats.tools], ["失败事件", stats.errors]] as const).map(([label, value]) =>
                <div key={label} className="rounded border border-[#e2dbc6] p-2.5">
                  <span className="text-sm text-ui-muted">{label}</span>
                  <strong className="block text-2xl leading-tight">{value}</strong>
                </div>)}
            </div>
            <div>
              <strong className="mb-2 block">动作分布</strong>
              {counts.length === 0 ? <p className="text-sm text-ui-muted">当前范围没有可统计的动作。</p> :
                <ul className="space-y-1.5">
                  {counts.map(item => {
                    return <li key={item.action}>
                      <div className="flex items-center justify-between gap-2 px-1">
                        <ActionChip action={item.action} />
                        <span>{item.count}</span>
                      </div>
                      <div className="mx-1 mt-1 h-1 overflow-hidden rounded bg-[#e6ebef]">
                        <div className="h-full rounded bg-[#9db8c8]"
                          style={{ width: `${Math.round(item.count / peak * 100)}%` }} />
                      </div>
                    </li>;
                  })}
                </ul>}
            </div>
            <label className="block">查看范围<select aria-label="活动范围" className="mt-1 w-full rounded border p-2"
              value={filters.conversation_id ?? ""} onChange={e => setFilters(current => ({ ...current, conversation_id: e.target.value }))}>
              <option value="">当前作品全部活动</option>{conversations.map(item => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select></label>
            <details className="border-t border-[#e2dbc6] pt-3">
              <summary className="cursor-pointer font-bold">记录设置</summary>
              <div className="mt-2 space-y-2">
                <label className="block"><input type="checkbox" checked={filters.verbose ?? false}
                  onChange={e => setFilters(current => ({ ...current, verbose: e.target.checked }))} /> 显示文字分片事件</label>
                <label className="block"><input type="checkbox" checked={capture} disabled={!settingsReady || savingSettings}
                  onChange={e => void configure(e.target.checked)} /> 保存后续模型输入快照</label>
                <p className="text-sm leading-5 text-ui-muted">快照保存在本机，可能包含作品正文。清理详情保留事件摘要与版本关联。</p>
                <label className="block">详情保留时间<select aria-label="详情保留时间" className="mt-1 w-full rounded border p-2" value={retention} disabled={!settingsReady || savingSettings}
                  onChange={e => void configure(capture, Number(e.target.value))}>
                  <option value={7}>7 天</option><option value={30}>30 天</option><option value={0}>直到手动清理</option>
                  {![0, 7, 30].includes(retention) && <option value={retention}>{retention} 天</option>}
                </select></label>
              </div>
            </details>
            <details className="border-t border-ui-edge pt-3"><summary className="cursor-pointer font-bold">维护工具</summary>
              <div className="mt-2 space-y-2">
                {!confirmClear ? <Button className="px-3 py-1" onClick={() => setConfirmClear(true)}>清理已保存详情</Button> :
                  <div><p className="mb-2">输入与工具结果详情将被删除，无法恢复。</p>
                    <Button className="mr-2 px-3 py-1" onClick={() => setConfirmClear(false)}>取消</Button>
                    <Button variant="primary" className="px-3 py-1" onClick={() => void activityRequest<{ removed: number }>(workspace, "/details", { method: "DELETE" })
                      .then(value => { setNotice(`已清理 ${value.removed} 份详情。`); setExpanded(""); setConfirmClear(false); })
                      .catch(reason => setError(activityError(reason)))}>确认清理详情</Button></div>}
                <Button className="px-3 py-1" onClick={() => void activityRequest(workspace, "/rebuild", { method: "POST" })
                  .then(() => setGeneration(value => value + 1)).catch(reason => setError(activityError(reason)))}>重建查询索引</Button>
              </div>
            </details>
          </aside>
        </div>
        <footer className="flex flex-none flex-wrap items-center gap-3 border-t border-[#e2dbc6] px-4 py-3">
          <span role="status">{connection}</span><span>已载入 {events.length} 条{unseen ? " · 有新事件" : ""}</span>
          <Button className="px-2 py-1" onClick={() => setGeneration(value => value + 1)}>重新连接</Button>
          <div className="flex-1" />
          <label><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> 自动跟随</label>
          <Button className="px-2 py-1" onClick={() => { if (browsing.current) setGeneration(value => value + 1); setFollow(true); }}>回到最新</Button>
          <label><input type="checkbox" checked={exportDetails} onChange={e => setExportDetails(e.target.checked)} /> 导出包含已保存详情</label>
          <Button variant="primary" className="px-3 py-1" onClick={() => void download()}>导出事件日志</Button>
        </footer>
      </>}
      {error && <p role="alert" className="max-h-24 overflow-auto border-t p-3 text-red-700">{error}</p>}
      {notice && <p role="status" className="border-t p-3">{notice}</p>}
  </AppDialog>;
}

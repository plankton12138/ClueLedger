/**
 * 活动记录的展示投影（0.8）。
 *
 * 后端给每条事件一份「动作说法」（action / phase / title / subject / note / pair_key）。
 * 这里把同一 pair_key 的开始与结束合成一行，再按任务分组，最后折叠同一任务内连续的同名文件动作。
 * 投影只影响界面：原始事件仍按时间保留在 rows.events 里，展开即可逐条查看。
 */

import type { ActivityEvent } from "../../api/activity";

export interface ActivityRow {
  key: string;
  at: string;
  action: string;
  phase: string;
  title: string;
  subject: string;
  note: string;
  duration_ms: number | null;
  count: number;
  events: ActivityEvent[];
}

export interface ActivityBand {
  key: string;
  kind: "writing" | "materials" | "operation";
  label: string;
  model: string;
  subject: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  note: string;
  complete: boolean;
  rows: ActivityRow[];
}

export const ACTION_LABEL: Record<string, string> = {
  conversation: "对话",
  model: "模型",
  tool: "工具",
  file: "文件",
  materials: "资料",
  version: "版本",
  operation: "操作",
  scope: "范围",
  context: "上下文",
  state: "状态",
  error: "错误",
  other: "其他",
};

export const ACTION_TONE: Record<string, string> = {
  conversation: "bg-blue-bg text-blue",
  model: "bg-muted text-sub",
  tool: "bg-green-bg text-green",
  file: "bg-amber-bg text-amber",
  materials: "bg-blue-bg text-blue",
  version: "bg-muted text-sub",
  operation: "bg-muted text-sub",
  scope: "bg-muted text-sub",
  context: "bg-muted text-sub",
  state: "bg-muted text-sub",
  error: "bg-red-bg text-red",
  other: "bg-muted text-sub",
};

export const PHASE_LABEL: Record<string, string> = {
  started: "已开始",
  running: "进行中",
  completed: "完成",
  failed: "失败",
  stopped: "已停止",
  interrupted: "中断",
  partial: "部分完成",
  unloaded: "未完整载入",
};

export const PHASE_TONE: Record<string, string> = {
  started: "text-blue",
  running: "text-blue",
  completed: "text-green",
  failed: "text-red",
  stopped: "text-mute",
  interrupted: "text-mute",
  partial: "text-amber",
  unloaded: "text-mute",
};

const SKIP = new Set(["run_phase", "conversation_meta"]);
const BAND_LABEL: Record<ActivityBand["kind"], string> = {
  writing: "写作",
  materials: "资料整理",
  operation: "作品操作",
};

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || ms <= 0) return "";
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} 秒`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  if (minutes < 60) return `${minutes} 分 ${seconds} 秒`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分`;
}

function gap(start: string, end: string): number | null {
  const value = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(value) && value >= 0 ? value : null;
}

function bandKind(event: ActivityEvent): ActivityBand["kind"] {
  if (event.action === "materials") return "materials";
  return event.turn_id ? "writing" : "operation";
}

function bandKey(event: ActivityEvent): string {
  if (event.turn_id) return `${event.conversation_id}:${event.turn_id}`;
  if (event.operation_id) return `operation:${event.operation_id}`;
  return "workspace";
}

/** 把同一 pair_key 的事件合成一行；文件变化接管写入工具行的显示。 */
function mergeRow(row: ActivityRow, event: ActivityEvent): ActivityRow {
  const events = [...row.events, event];
  const finished = event.phase !== "started";
  const duration = row.duration_ms ?? event.duration_ms ?? gap(row.at, event.at);
  if (event.action === "file") {
    return {
      ...row,
      action: "file",
      phase: event.phase,
      title: event.title,
      subject: event.subject,
      note: event.note,
      duration_ms: duration,
      events,
    };
  }
  return {
    ...row,
    phase: finished ? event.phase : row.phase,
    // 合成一行后按结果说法命名：请求模型 → 模型返回，作者看到的是这一行实际发生了什么。
    title: finished ? event.title : row.title,
    note: event.note || row.note,
    duration_ms: finished ? duration : row.duration_ms,
    events,
  };
}

/**
 * 同一任务内的重复动作合并成一行：文件按「写入/新建」跨对象合并，工具按同一对象合并。
 * 模型调用与对话事件各自成行——它们的先后顺序就是这一轮的执行节奏，折起来会看不出来。
 * 合并后按首次出现的位置排序；展开仍能看到每一次的原始事件与时间。
 */
function foldable(action: string): boolean {
  return action === "file" || action === "tool";
}

function groupRows(rows: ActivityRow[]): ActivityRow[] {
  const groups = new Map<string, ActivityRow[]>();
  const order: string[] = [];
  for (const row of rows) {
    const key = foldable(row.action)
      ? `${row.action}\u0000${row.title}\u0000${row.action === "file" ? "" : row.subject}`
      : row.key;
    if (!groups.has(key)) {
      groups.set(key, []);
      order.push(key);
    }
    groups.get(key)!.push(row);
  }
  const severity = (phase: string) => (phase === "failed" ? 3 : phase === "running" ? 2 : phase === "started" ? 1 : 0);
  return order.map(key => {
    const group = groups.get(key)!;
    if (group.length === 1) return group[0]!;
    const count = group.reduce((sum, row) => sum + row.count, 0);
    const durations = group.map(row => row.duration_ms).filter((value): value is number => value != null);
    const phase = group.map(row => row.phase).sort((a, b) => severity(b) - severity(a))[0] ?? group[0]!.phase;
    return {
      ...group[0]!,
      count,
      events: group.flatMap(row => row.events),
      subject: group[0]!.action === "file" ? `${count} 个文件` : group[0]!.subject,
      note: "",
      duration_ms: durations.length ? durations.reduce((sum, value) => sum + value, 0) : null,
      phase,
    };
  });
}

/**
 * 旧索引兜底：动作说法是后加的字段，旧版本服务写出的行只有类别、状态与摘要。
 * 缺字段时按摘要补出可读的一行，并在面板上提示重建索引，不把空白行丢给作者。
 */
const LEGACY_ACTION: Record<string, string> = {
  task: "conversation", model: "model", tool: "tool", context: "context",
  file: "file", state: "state", version: "version", operation: "operation",
};

export function hasLegacyEvents(events: ActivityEvent[]): boolean {
  return events.some(event => !event.action);
}

function display(event: ActivityEvent): ActivityEvent {
  if (event.action && event.title) return event;
  const [head = "", ...rest] = String(event.summary ?? "").split(" · ");
  return {
    ...event,
    action: event.action || LEGACY_ACTION[event.category] || "other",
    phase: event.phase || event.status || "completed",
    title: event.title || head || event.type,
    subject: event.subject || rest.join(" · "),
    note: event.note ?? "",
    pair_key: event.pair_key || event.event_id,
  };
}

export function buildActivity(events: ActivityEvent[]): ActivityBand[] {
  const bands = new Map<string, ActivityBand>();
  const rows = new Map<string, Map<string, ActivityRow>>();
  const opened = new Set<string>();
  const order: string[] = [];

  for (const raw of events) {
    if (SKIP.has(raw.type)) continue;
    const event = display(raw);
    const key = bandKey(event);
    let band = bands.get(key);
    if (!band) {
      band = {
        key,
        kind: bandKind(event),
        label: BAND_LABEL[bandKind(event)],
        model: "",
        subject: "",
        started_at: event.at,
        finished_at: null,
        status: "running",
        note: "",
        complete: false,
        rows: [],
      };
      bands.set(key, band);
      rows.set(key, new Map());
      order.push(key);
    }
    if (event.type === "turn_started" || event.type === "operation_started") opened.add(key);
    if (event.type === "turn_started") {
      band.kind = bandKind(event);
      band.label = BAND_LABEL[band.kind];
      band.model = event.note;
      band.subject = event.subject;
      band.started_at = event.at;
      continue;
    }
    if (event.type === "run_finished") {
      band.finished_at = event.at;
      band.status = event.phase;
      band.note = event.note;
      continue;
    }
    if (event.type === "operation_finished") {
      // 作品操作没有单独的摘要行：终态挂在分组条上，同时并进开始事件那一行。
      band.finished_at = event.at;
      band.status = event.phase;
      band.note = event.note;
    }
    const map = rows.get(key)!;
    const pair = event.pair_key || event.event_id;
    const current = map.get(pair);
    if (current) map.set(pair, mergeRow(current, event));
    else
      map.set(pair, {
        key: pair,
        at: event.at,
        action: event.action,
        phase: event.phase,
        title: event.title,
        subject: event.subject,
        note: event.note,
        duration_ms: event.duration_ms ?? null,
        count: 1,
        events: [event],
      });
  }

  return order.map(key => {
    const band = bands.get(key)!;
    const list = [...rows.get(key)!.values()].sort(
      (a, b) => a.at.localeCompare(b.at) || a.events[0]!.id - b.events[0]!.id,
    );
    const complete = opened.has(key);
    if (!complete && !band.finished_at) band.status = "unloaded";
    for (const row of list) {
      if (row.phase === "started" && band.status === "running") row.phase = "running";
    }
    return { ...band, complete, rows: groupRows(list) };
  });
}

/**
 * 侧栏的动作分布：按行计数，折叠后的文件批次算一次。
 *
 * 只列最主要的六类，其余并成「其他」——分类太细时这一栏比事件流本身还长，
 * 起不到「这轮做了什么」的概括作用。
 */
export function actionCounts(bands: ActivityBand[]): { action: string; count: number }[] {
  const counts = new Map<string, number>();
  for (const band of bands) {
    for (const row of band.rows) {
      counts.set(row.action, (counts.get(row.action) ?? 0) + 1);
    }
  }
  const preferred = ["conversation", "model", "tool", "file", "materials", "version"];
  const ranked = [...counts.entries()]
    .map(([action, count]) => ({ action, count }))
    .sort((a, b) => {
      const left = preferred.indexOf(a.action);
      const right = preferred.indexOf(b.action);
      return (left < 0 ? preferred.length : left) - (right < 0 ? preferred.length : right);
    });
  const head = ranked.slice(0, preferred.length);
  const rest = ranked.slice(preferred.length).reduce((sum, item) => sum + item.count, 0);
  return rest ? [...head, { action: "other", count: rest }] : head;
}

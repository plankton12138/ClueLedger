import { errorMessage } from "./errors";
import { WorkspaceApiError } from "./workspace";

export function activityError(reason: unknown): string {
  return errorMessage(reason, "活动记录");
}

export interface ActivityEvent {
  id: number;
  event_id: string;
  at: string;
  conversation_id: string;
  turn_id: string;
  type: string;
  category: string;
  status: string;
  action: string;
  phase: string;
  title: string;
  subject: string;
  note: string;
  pair_key: string;
  summary: string;
  duration_ms?: number | null;
  operation_id?: string | null;
}

export interface ActivityPage {
  items: ActivityEvent[];
  cursor: string;
  reset: boolean;
  has_more: boolean;
  next_before: number | null;
  stats: { events: number; models: number; tools: number; errors: number };
  run?: {
    turn_id: string;
    started_at: string;
    finished_at: string | null;
    model: string | null;
    status: string;
    origin?: string;
    label?: string;
  } | null;
}

export interface ActivityFilters {
  conversation_id?: string;
  turn_id?: string;
  category?: string;
  status?: string;
  q?: string;
  since?: string;
  until?: string;
  verbose?: boolean;
}

export function activityUrl(id: string, path = "", filters: object = {}): string {
  const query = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== "") query.set(key, String(value));
  });
  return `/api/file-workspaces/${encodeURIComponent(id)}/activity${path}?${query}`;
}

async function checked(response: Response): Promise<Response> {
  if (!response.ok) {
    const error = (await response.json().catch(() => null))?.error;
    throw new WorkspaceApiError(error?.code ?? `HTTP_${response.status}`,
      error?.why ?? "活动记录请求失败", error?.fix ?? "检查服务连接后重新连接", response.status, error?.where ?? "活动记录");
  }
  return response;
}

export async function activityRequest<T>(id: string, path = "", init?: RequestInit): Promise<T> {
  return (await checked(await fetch(activityUrl(id, path), {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  }))).json() as Promise<T>;
}

export async function activityPage(id: string, filters: ActivityFilters, before: number,
  signal?: AbortSignal): Promise<ActivityPage> {
  return (await checked(await fetch(activityUrl(id, "", { ...filters, before }), { signal }))).json();
}

export async function exportActivity(id: string, filters: ActivityFilters, includeDetails = false): Promise<Blob> {
  return (await checked(await fetch(activityUrl(id, "/export", { ...filters, include_details: includeDetails })))).blob();
}

export async function* activityStream(id: string, filters: ActivityFilters, after: string,
  signal: AbortSignal): AsyncGenerator<ActivityPage> {
  const response = await checked(await fetch(activityUrl(id, "/stream", { ...filters, after }), {
    signal, headers: { Accept: "text/event-stream" },
  }));
  if (!response.body) throw new Error("[ACTIVITY_STREAM_EMPTY] 活动记录：连接没有数据。建议：重新连接。");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { value, done } = await reader.read();
      if (done) throw new Error("[ACTIVITY_STREAM_CLOSED] 活动记录：连接已断开。建议：重新连接。");
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replace(/\r\n/g, "\n");
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) >= 0) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const data = frame.split("\n").filter(line => line.startsWith("data:"))
          .map(line => line.slice(5).trimStart()).join("\n");
        if (data) yield JSON.parse(data) as ActivityPage;
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

export function mergeActivity(current: ActivityEvent[], incoming: ActivityEvent[], reset = false): ActivityEvent[] {
  const items = new Map((reset ? [] : current).map(item => [item.event_id, item]));
  incoming.forEach(item => items.set(item.event_id, item));
  return [...items.values()].sort((a, b) => a.at.localeCompare(b.at) || a.id - b.id);
}

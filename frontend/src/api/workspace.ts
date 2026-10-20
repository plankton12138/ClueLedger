/**
 * 文件工作区客户端（0.4 V04-4）。
 *
 * 对应后端 `hastings/workspace/api.py` 的 `/api/file-workspaces`：目录、文件、搜索、
 * 导入、Git、会话与轮次。错误统一是 `{"error": {code, where, why, fix, details}}`，
 * 这里把它转成带 code 的异常，界面按 code 分支，不解析文本。
 *
 * 事件流用 fetch 读 `text/event-stream`，不依赖 EventSource：这样浏览器与 node 测试
 * 走同一段解析代码，也能带自定义游标与中止信号。
 */

export interface WorkspaceBinding {
  id: string;
  name: string;
  path: string;
  exists: boolean;
}

export interface FileEntry {
  path: string;
  kind: "chapter" | "setting";
  title: string;
  order: string;
  exists: boolean;
  size: number;
}

export interface Catalog {
  chapters: FileEntry[];
  settings: FileEntry[];
}

export interface FileView {
  path: string;
  kind: "chapter" | "setting";
  title: string;
  text: string;
  digest: string;
  size: number;
}

export interface SearchHit {
  path: string;
  line: number;
  text: string;
}

export interface SearchResult {
  hits: SearchHit[];
  unreadable: string[];
  truncated: boolean;
}

export type RunStatus = "completed" | "partial" | "failed" | "stopped" | "interrupted";
export type Recording = "committed" | "no_change" | "unrecorded";

export interface RunResult {
  status: RunStatus;
  recording: Recording;
  changed_paths: string[];
  commit: string | null;
  note: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

export interface TurnInfo {
  turn_id: string;
  conversation_id: string;
  started_at: string;
}

export interface WorkspaceEvent {
  seq: number;
  at: string;
  type:
    | "turn_started"
    | "tool_started"
    | "tool_finished"
    | "assistant_message"
    | "error"
    | "run_finished";
  turn_id?: string;
  text?: string;
  tool?: string;
  args?: Record<string, unknown>;
  ok?: boolean;
  summary?: string;
  code?: string;
  message?: string;
  status?: RunStatus;
  recording?: Recording;
  changed_paths?: string[];
  commit?: string | null;
  note?: string;
}

export interface GitChange {
  path: string;
  code: string;
  orig_path: string | null;
}

export interface GitStatus {
  initialized: boolean;
  branch: string | null;
  head: string | null;
  staged: string[];
  changes: GitChange[];
  conflicts: string[];
  clean: boolean;
}

export interface GitCommit {
  commit: string;
  at: string;
  subject: string;
}

const BASE = "/api/file-workspaces";

export class WorkspaceApiError extends Error {
  constructor(
    readonly code: string,
    readonly why: string,
    readonly fix: string,
    readonly status: number,
  ) {
    super(why);
    this.name = "WorkspaceApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    ...init,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; why?: string; fix?: string };
    } | null;
    const error = body?.error;
    throw new WorkspaceApiError(
      error?.code ?? `HTTP_${response.status}`,
      error?.why ?? "请求没有成功",
      error?.fix ?? "稍后重试；若持续失败，检查后端日志",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listWorkspaces(): Promise<WorkspaceBinding[]> {
  return request<WorkspaceBinding[]>("");
}

export function openWorkspace(path: string, create = false): Promise<WorkspaceBinding> {
  return request<WorkspaceBinding>("", {
    method: "POST",
    body: JSON.stringify({ path, create }),
  });
}

export function listFiles(workspaceId: string): Promise<Catalog> {
  return request<Catalog>(`/${workspaceId}/files`);
}

export function readFile(workspaceId: string, path: string): Promise<FileView> {
  return request<FileView>(`/${workspaceId}/file?path=${encodeURIComponent(path)}`);
}

export function searchText(workspaceId: string, query: string): Promise<SearchResult> {
  return request<SearchResult>(
    `/${workspaceId}/search?q=${encodeURIComponent(query)}`,
  );
}

export async function importManuscript(workspaceId: string, file: File): Promise<Catalog> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetch(`${BASE}/${workspaceId}/imports`, {
    method: "POST",
    body: form,
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; why?: string; fix?: string };
    } | null;
    throw new WorkspaceApiError(
      body?.error?.code ?? `HTTP_${response.status}`,
      body?.error?.why ?? "导入没有成功",
      body?.error?.fix ?? "确认文件是 UTF-8 的 .md 或 .txt",
      response.status,
    );
  }
  return (await response.json()) as Catalog;
}

export function gitStatus(workspaceId: string): Promise<GitStatus> {
  return request<GitStatus>(`/${workspaceId}/git/status`);
}

export function gitDiff(workspaceId: string): Promise<{ diff: string }> {
  return request<{ diff: string }>(`/${workspaceId}/git/diff`);
}

export function gitLog(workspaceId: string): Promise<{ commits: GitCommit[] }> {
  return request<{ commits: GitCommit[] }>(`/${workspaceId}/git/log`);
}

export function gitCommitDiff(workspaceId: string, commit: string): Promise<{ diff: string }> {
  return request<{ diff: string }>(`/${workspaceId}/git/commits/${commit}/diff`);
}

export function gitInit(workspaceId: string): Promise<GitStatus> {
  return request<GitStatus>(`/${workspaceId}/git/init`, { method: "POST" });
}

export function gitRecord(workspaceId: string, message = ""): Promise<{ commit: string | null }> {
  return request<{ commit: string | null }>(`/${workspaceId}/git/record`, {
    method: "POST",
    body: JSON.stringify({ message }),
  });
}

export function gitRevert(workspaceId: string, commit: string): Promise<{ commit: string | null }> {
  return request<{ commit: string | null }>(`/${workspaceId}/git/revert`, {
    method: "POST",
    body: JSON.stringify({ commit }),
  });
}

export function listConversations(workspaceId: string): Promise<Conversation[]> {
  return request<Conversation[]>(`/${workspaceId}/conversations`);
}

export function createConversation(workspaceId: string): Promise<Conversation> {
  return request<Conversation>(`/${workspaceId}/conversations`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export function conversationMessages(
  workspaceId: string,
  conversationId: string,
  after = 0,
): Promise<{ events: WorkspaceEvent[]; last_seq: number }> {
  return request<{ events: WorkspaceEvent[]; last_seq: number }>(
    `/${workspaceId}/conversations/${conversationId}/messages?after=${after}`,
  );
}

export function startTurn(
  workspaceId: string,
  conversationId: string,
  text: string,
  requestKey?: string,
): Promise<TurnInfo> {
  return request<TurnInfo>(`/${workspaceId}/conversations/${conversationId}/turns`, {
    method: "POST",
    body: JSON.stringify({ text, request_key: requestKey ?? null }),
  });
}

export function stopTurn(workspaceId: string, turnId: string): Promise<{ stopped: boolean }> {
  return request<{ stopped: boolean }>(`/${workspaceId}/turns/${turnId}/stop`, {
    method: "POST",
  });
}

/**
 * 读取事件流：每轮以 `run_finished` 结束，服务端随后关闭响应。
 * 解析只认 `data:` 行；`id:` 行用于调试，不参与状态。
 */
export async function* streamEvents(
  workspaceId: string,
  conversationId: string,
  after: number,
  signal?: AbortSignal,
): AsyncGenerator<WorkspaceEvent> {
  const url = `${BASE}/${workspaceId}/events?conversation_id=${encodeURIComponent(
    conversationId,
  )}&after=${after}&follow=true`;
  const response = await fetch(url, {
    headers: { Accept: "text/event-stream" },
    signal,
  });
  if (!response.ok || !response.body) {
    throw new WorkspaceApiError(
      `HTTP_${response.status}`,
      "事件流没有建立",
      "刷新对话；若持续失败，检查后端是否在运行",
      response.status,
    );
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const chunk = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const payload = chunk
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice(6))
          .join("\n");
        if (payload) yield JSON.parse(payload) as WorkspaceEvent;
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    await reader.cancel().catch(() => undefined);
  }
}

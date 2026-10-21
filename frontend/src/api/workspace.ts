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

/** 新作品的默认父目录（服务端解析出的「文档」目录）。 */
export interface DefaultLocation {
  root: string;
}

/** 系统文件夹对话框的结果；取消时 path 为 null。 */
export interface PickedDirectory {
  path: string | null;
}

export interface FileEntry {
  path: string;
  kind: "chapter" | "character" | "setting";
  title: string;
  order: string;
  exists: boolean;
  size: number;
}

export interface Catalog {
  chapters: FileEntry[];
  characters: FileEntry[];
  settings: FileEntry[];
}

export interface FileView {
  path: string;
  kind: "chapter" | "character" | "setting";
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

/** 本作品当前占用的那一轮；只含公开信息，不含凭据。 */
export interface ActiveTurn {
  conversation_id: string;
  turn_id: string;
  state: "accepted" | "running" | "stop_requested";
  model: string;
  connection: string;
  started_at: string;
}

/** 会话列表：一次读齐列表、上次看的会话与作品当前活动轮次。 */
export interface ConversationList {
  items: Conversation[];
  recent_id: string | null;
  active_turn: ActiveTurn | null;
  revision: string;
}

/** 首次发送的受理结果：会话与第一轮一起成立。 */
export interface AcceptedTurn {
  conversation_id: string;
  turn_id: string;
  started_at: string;
  title: string;
  request_key: string;
}

export interface TurnInfo {
  turn_id: string;
  conversation_id: string;
  started_at: string;
  title?: string;
}

/** 界面状态：上次看的正式会话与各会话草稿（``new`` 是未发送的新对话槽位）。 */
export interface ConversationUi {
  recent_id: string | null;
  drafts: Record<string, string>;
  revision: string;
}

export interface ConversationUiPatch {
  set_recent?: boolean;
  recent_id?: string | null;
  set_draft?: boolean;
  draft_target?: string;
  draft_text?: string;
}

/** 单条回复的完整性：块与响应各自成立，不代替整轮结果。 */
export type Completion = "completed" | "stopped" | "failed" | "interrupted" | "truncated";

/** 内容块类型：模型可见的公开文字，以及仅供查看的思考。 */
export type BlockKind = "text" | "thinking";

/** 应用确认的运行阶段；界面只按这些阶段说明状态，不猜进度。 */
export type RunPhase = "model" | "tool" | "record";

interface EventBase {
  seq: number;
  at: string;
}

/**
 * 会话事件（按 `type` 区分的联合类型）。
 *
 * 旧日志只有 `turn_started`、`tool_*`、`assistant_message`、`error`、`run_finished`，
 * 这些分支保持可读；流式事件是新增的，旧记录不会凭空多出内容。
 * 可选字段只用于兼容旧记录，新事件一律写全。
 */
export type WorkspaceEvent =
  | (EventBase & { type: "turn_started"; turn_id: string; text: string })
  | (EventBase & { type: "run_phase"; turn_id: string; phase: RunPhase })
  | (EventBase & { type: "assistant_started"; turn_id: string; message_id: string })
  | (EventBase & {
      type: "assistant_block_started";
      turn_id: string;
      message_id: string;
      block_id: string;
      kind: BlockKind;
      order: number;
    })
  | (EventBase & {
      type: "assistant_block_delta";
      turn_id: string;
      message_id: string;
      block_id: string;
      kind: BlockKind;
      part: number;
      text: string;
    })
  | (EventBase & {
      type: "assistant_block_finished";
      turn_id: string;
      message_id: string;
      block_id: string;
      kind: BlockKind;
      text: string;
      last_part: number;
      completion: Completion;
      duration_ms?: number | null;
    })
  | (EventBase & {
      type: "assistant_message";
      turn_id: string;
      message_id?: string;
      text: string;
      completion?: Completion;
    })
  | (EventBase & {
      type: "assistant_finished";
      turn_id: string;
      message_id: string;
      completion: Completion;
      stop_reason?: string;
      duration_ms?: number | null;
    })
  | (EventBase & {
      type: "tool_started";
      turn_id: string;
      tool_call_id?: string;
      tool: string;
      args?: Record<string, unknown>;
    })
  | (EventBase & {
      type: "tool_finished";
      turn_id: string;
      tool_call_id?: string;
      tool: string;
      args?: Record<string, unknown>;
      ok: boolean;
      summary?: string;
      duration_ms?: number | null;
    })
  | (EventBase & { type: "error"; turn_id: string; code?: string; message?: string })
  | (EventBase & {
      type: "run_finished";
      turn_id: string;
      status: RunStatus;
      recording: Recording;
      changed_paths?: string[];
      commit?: string | null;
      note?: string;
    });

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
  // headers 必须最后合并：`...init` 写在后面会把整个 headers 覆盖掉，
  // 带 If-Match 的写请求就会连 Content-Type 一起丢掉，后端解析不了 body 返回 422。
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; why?: string; fix?: string };
    } | null;
    const error = body?.error;
    throw new WorkspaceApiError(
      error?.code ?? `HTTP_${response.status}`,
      // 后端没给原因时至少留下状态码：否则界面只能显示「请求没有成功」，无从判断是
      // 路由不存在、参数被拒还是服务端异常。
      error?.why ?? `请求没有成功（HTTP ${response.status}）`,
      error?.fix ?? "稍后重试；若持续失败，按状态码查后端日志",
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export function listWorkspaces(): Promise<WorkspaceBinding[]> {
  return request<WorkspaceBinding[]>("");
}

export function fetchDefaultLocation(): Promise<DefaultLocation> {
  return request<DefaultLocation>("/default-location");
}

export function pickDirectory(initial = ""): Promise<PickedDirectory> {
  return request<PickedDirectory>("/pick-directory", {
    method: "POST",
    body: JSON.stringify({ initial }),
  });
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

export function listConversations(workspaceId: string): Promise<ConversationList> {
  return request<ConversationList>(`/${workspaceId}/conversations`);
}

/**
 * 首次发送：一次请求建会话并受理首轮。
 * 没有提示就没有会话——界面不再先建空记录。
 */
export function createConversationWithText(
  workspaceId: string,
  text: string,
  requestKey: string,
): Promise<AcceptedTurn> {
  return request<AcceptedTurn>(`/${workspaceId}/conversations`, {
    method: "POST",
    body: JSON.stringify({ text, request_key: requestKey }),
  });
}

export function renameConversation(
  workspaceId: string,
  conversationId: string,
  title: string,
): Promise<Conversation> {
  return request<Conversation>(`/${workspaceId}/conversations/${conversationId}`, {
    method: "PATCH",
    body: JSON.stringify({ title }),
  });
}

export function conversationMessages(
  workspaceId: string,
  conversationId: string,
  after = 0,
): Promise<{
  events: WorkspaceEvent[];
  last_seq: number;
  active_turn: ActiveTurn | null;
  revision: string;
}> {
  return request(
    `/${workspaceId}/conversations/${conversationId}/messages?after=${after}`,
  );
}

export function fetchConversationUi(workspaceId: string): Promise<ConversationUi> {
  return request<ConversationUi>(`/${workspaceId}/conversation-ui`);
}

export function patchConversationUi(
  workspaceId: string,
  patch: ConversationUiPatch,
  revision: string,
): Promise<ConversationUi> {
  return request<ConversationUi>(`/${workspaceId}/conversation-ui`, {
    method: "PATCH",
    headers: { "If-Match": revision },
    body: JSON.stringify(patch),
  });
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

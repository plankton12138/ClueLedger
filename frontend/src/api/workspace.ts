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

export type CharacterRole = "detective" | "assistant" | "culprit" | "suspect" | "victim";

export type ChapterType = "regular" | "prologue" | "epilogue" | "extra";

export interface FileEntry {
  path: string;
  kind: "chapter" | "character" | "setting";
  title: string;
  /** 服务端生成的显示名：目录、稿纸与模型目录共用，前端不再自行拼章序。 */
  display_title: string;
  order: string;
  chapter_number: number | null;
  chapter_type: ChapterType | null;
  roles: CharacterRole[];
  exists: boolean;
  size: number;
  /** 命名与元信息诊断；空数组表示没有需要提示的问题。 */
  metadata_warnings: string[];
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
  display_title: string;
  text: string;
  /** 阅读用正文：人物小传去掉了开头的元信息。 */
  display_text: string;
  digest: string;
  size: number;
  chapter_number: number | null;
  chapter_type: ChapterType | null;
  roles: CharacterRole[];
  metadata_warnings: string[];
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

/** 本轮带上来的材料：当前阅读位置、显式参考与客户端看到的版本。 */
export interface ContextInput {
  selected_path: string | null;
  /** 顺序即作者选择顺序；服务端按路径去重。 */
  reference_paths: string[];
  /** 客户端看到的版本，只对应本次当前路径与参考；不是服务端内容依据。 */
  observed_digests: Record<string, string>;
}

/** 首次发送与续聊共用同一个请求：界面生成快照，重试复用同一个对象。 */
export interface TurnInput {
  text: string;
  request_key: string;
  context: ContextInput;
  /** 用途：普通对话，或按作者选择更新章节索引。 */
  purpose?: "chat" | "update_chapter_index" | "review" | "update_disclosure" | "update_state";
  /** 索引用途要更新的章节；普通对话不能带。 */
  index_paths?: string[];
  /** 检查用途要核对的文件。 */
  review_paths?: string[];
}

/** 没有选择任何材料时的空上下文。 */
export const EMPTY_CONTEXT: ContextInput = {
  selected_path: null,
  reference_paths: [],
  observed_digests: {},
};

/** 草稿携带的参考：只选文件、还没写文字时也保留。 */
export interface DraftContext {
  selected_path: string | null;
  reference_paths: string[];
}

/** 界面状态：上次看的正式会话、各会话草稿与草稿携带的参考（`new` 是未发送的新对话槽位）。 */
export interface ConversationUi {
  recent_id: string | null;
  drafts: Record<string, string>;
  draft_contexts: Record<string, DraftContext>;
  revision: string;
}

export interface ConversationUiPatch {
  set_recent?: boolean;
  recent_id?: string | null;
  set_draft?: boolean;
  draft_target?: string;
  draft_text?: string;
  set_draft_context?: boolean;
  draft_selected_path?: string | null;
  draft_reference_paths?: string[];
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
      type: "context_prepared";
      turn_id: string;
      selected_path: string | null;
      references: { path: string; digest: string }[];
      provided: { path: string; start: number; end: number; length: number }[];
    })
  | (EventBase & {
      type: "state_injected";
      turn_id: string;
      profile: "writer" | "extract" | "review";
      parts: { key: string; chars: number }[];
      skipped: { key: string; reason: "budget" | "empty"; chars?: number }[];
      budget: { limit: number; used: number };
    })
  | (EventBase & {
      type: "reference_registered";
      turn_id: string;
      references: { path: string; digest: string; kind: string; required: boolean }[];
    })
  | (EventBase & {
      type: "review_requested";
      turn_id: string;
      requirement: string;
      authorization: string;
      targets: string[];
    })
  | (EventBase & {
      type: "review_finished";
      turn_id: string;
      report_id: string | null;
      status: ReviewStatus;
      targets: string[];
      findings: ReviewFinding[];
      uncovered: string[];
    })
  | (EventBase & { type: "task_scope_set"; turn_id: string } & TaskScope)
  | (EventBase & { type: "task_scope_extended"; turn_id: string } & TaskScope)
  | (EventBase & {
      type: "material_read";
      turn_id: string;
      path: string;
      digest: string;
      start_char: number;
      end_char: number;
      start_line: number;
      end_line: number;
      total_chars: number;
    })
  | (EventBase & {
      type: "run_finished";
      turn_id: string;
      status: RunStatus;
      recording: Recording;
      changed_paths?: string[];
      commit?: string | null;
      note?: string;
    });

/** 一章索引的当前状态：内容来自上次发布，状态按当前磁盘算出。 */
export type ChapterIndexStatus = "missing" | "valid" | "stale" | "source_missing" | "unavailable";

export interface ChapterIndexEvidence {
  index: number;
  start: number;
  end: number;
  quote: string;
}

export interface ChapterIndexEntry {
  kind: "event" | "character" | "place" | "clue";
  text: string;
  evidence: number[];
}

export interface ChapterIndexItem {
  source_path: string;
  source_digest: string;
  recipe_version: number;
  generated_at: string;
  summary: string;
  status: ChapterIndexStatus;
  evidence: ChapterIndexEvidence[];
  events: ChapterIndexEntry[];
  characters: ChapterIndexEntry[];
  places: ChapterIndexEntry[];
  clues: ChapterIndexEntry[];
}

export interface ChapterIndex {
  items: ChapterIndexItem[];
}

/** 一条身份披露：第几章起读者已知，或计划第几章披露。 */
export interface DisclosureFact {
  fact: string;
  chapter: number;
  /** text：正文核对过；declared：只有作者声明。 */
  source?: "text" | "declared";
}

export interface DisclosureEntity {
  path: string;
  name: string;
  surface: string;
  aliases: string[];
  roles: string[];
  revealed: DisclosureFact[];
  /** 声明了起始章，但正文还没写到那一章。 */
  upcoming: DisclosureFact[];
  /** 计划披露：只在这里显示，不进入写作上下文。 */
  planned: DisclosureFact[];
  overdue: boolean;
  /** 这个角色有没有可用的正文核对记录。 */
  record_status?: "valid" | "stale" | "source_missing" | null;
  verified?: number;
  warnings: string[];
}

export interface DisclosureConflict {
  path: string;
  name: string;
  kind: string;
  label: string;
  detail: string;
}

export interface DisclosureState {
  domain: "disclosure";
  current_chapter: number;
  entities: DisclosureEntity[];
  conflicts: DisclosureConflict[];
}

/** 一个角色截至当前章的最后一次原文核对状态。 */
export interface RosterCharacter {
  path: string;
  name: string;
  location: string;
  alive: boolean;
  stance: string;
  /** 这条状态出自第几章；没有核对记录时为 null。 */
  chapter: number | null;
  /** 距当前章差几章；0 或 null 表示不落后。 */
  lag: number | null;
  stale: boolean;
  warnings: string[];
}

export interface RosterState {
  domain: "roster";
  current_chapter: number;
  characters: RosterCharacter[];
  warnings: string[];
}

/** 一章的时间锚点：`when` 是原文的说法，不做日期换算。 */
export interface TimelineEntry {
  chapter: number;
  when: string;
  span: string;
  location: string;
}

export interface TimelineState {
  domain: "timeline";
  current_chapter: number;
  entries: TimelineEntry[];
  /** 有正文但没有锚点的章号。 */
  missing: number[];
  warnings: string[];
}

/** 一条线索：埋在哪一章、该在哪一章回收、现在什么状态。 */
export interface ClueEntry {
  clue: string;
  planted_chapter: number;
  due_chapter: number | null;
  status: "open" | "resolved" | "dropped";
  label: string;
  /** 计划回收章已过、仍未回收。 */
  overdue: boolean;
  supports: string;
  resolution_chapter: number | null;
  quote: string;
}

export interface ClueState {
  domain: "clues";
  current_chapter: number;
  entries: ClueEntry[];
  /** 超期未回收的线索内容。 */
  overdue: string[];
  warnings: string[];
}

export interface GlossaryTerm {
  term: string;
  kind: "person" | "place" | "org" | "item";
  label: string;
  aliases: string[];
  first_chapter: number;
  source: "declared" | "text";
}

export interface GlossaryState {
  domain: "glossary";
  current_chapter: number;
  terms: GlossaryTerm[];
  conflicts: { term: string; kind: string; detail: string }[];
  warnings: string[];
}

/** 本轮范围里的一条允许操作。 */
export interface TaskGrant {
  action: "create" | "update" | "delete" | "move";
  path: string;
  target?: string;
}

/** 本轮修改范围：模型登记的内容与应用保存的版本。 */
export interface TaskScope {
  version: number;
  mode: "read_only" | "edit";
  summary: string;
  authorization: string;
  authorization_seq: number | null;
  carried_from_seq: number | null;
  grants: TaskGrant[];
  denied_paths: string[];
  required_references: string[];
  extendable: boolean;
  extend_actions: string[];
}

/** 一处检查证据：路径、版本、字符区间与引文。 */
export interface ReviewEvidence {
  path: string;
  digest: string;
  start: number;
  end: number;
  quote: string;
}

/** 一条检查问题：确定程度区分「有依据」与「疑点」。 */
export interface ReviewFinding {
  kind: "continuity" | "disclosure" | "task_mismatch" | "other";
  certainty: "supported" | "suspected";
  description: string;
  suggestion: string;
  evidence: ReviewEvidence[];
}

export type ReviewStatus = "findings" | "no_findings" | "incomplete";

export interface ReviewReport {
  report_id: string;
  turn_id: string;
  target_paths: string[];
  target_digests: [string, string][];
  requirement: string;
  status: ReviewStatus;
  freshness: "valid" | "stale" | "source_missing";
  findings: ReviewFinding[];
  uncovered: string[];
  created_at: string;
}

export interface ReviewList {
  items: ReviewReport[];
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

export function fetchReviews(workspaceId: string, path?: string): Promise<ReviewList> {
  const suffix = path ? `?path=${encodeURIComponent(path)}` : "";
  return request<ReviewList>(`/${workspaceId}/reviews${suffix}`);
}

export function fetchChapterIndex(workspaceId: string): Promise<ChapterIndex> {
  return request<ChapterIndex>(`/${workspaceId}/chapter-index`);
}

/** 身份状态：这一版由小传元信息实时算出，没有派生记录。 */
export function fetchDisclosureState(workspaceId: string): Promise<DisclosureState> {
  return request<DisclosureState>(`/${workspaceId}/state`);
}

/** 人物状态：位置、生死、立场，只来自原文核对记录。 */
export function fetchRosterState(workspaceId: string): Promise<RosterState> {
  return request<RosterState>(`/${workspaceId}/state?domain=roster`);
}

/** 故事时间：一章一个锚点，缺锚点的章单独列出。 */
export function fetchTimelineState(workspaceId: string): Promise<TimelineState> {
  return request<TimelineState>(`/${workspaceId}/state?domain=timeline`);
}

/** 线索台账：只列未回收的进上下文，超期项排在最前。 */
export function fetchClueState(workspaceId: string): Promise<ClueState> {
  return request<ClueState>(`/${workspaceId}/state?domain=clues`);
}

/** 专有名词：人物来自小传声明，其他专名来自原文登记。 */
export function fetchGlossaryState(workspaceId: string): Promise<GlossaryState> {
  return request<GlossaryState>(`/${workspaceId}/state?domain=glossary`);
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
  turn: TurnInput,
): Promise<AcceptedTurn> {
  return request<AcceptedTurn>(`/${workspaceId}/conversations`, {
    method: "POST",
    body: JSON.stringify(turn),
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
  turn: TurnInput,
): Promise<TurnInfo> {
  return request<TurnInfo>(`/${workspaceId}/conversations/${conversationId}/turns`, {
    method: "POST",
    body: JSON.stringify(turn),
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

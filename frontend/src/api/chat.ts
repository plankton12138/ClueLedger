/**
 * 对话契约与 HTTP 客户端（Phase 19.2）。
 *
 * 本模块是「对话公开契约」的事实源：消息/轮次/事件/任务建议的数据形状（计划 §6.1），
 * 流式事件信封与序号规则（§6.3），以及面向 `/api/v2/projects/{project_id}` 的真实客户端
 * （§6.2）。模拟实现（`chatSimulator.ts`）实现同一 `ChatService` 接口——组件只依赖接口，
 * 因此「同一套组件切换模拟服务与真实 API」。
 *
 * 边界（四透镜②）：客户端不含任何写端口。候选接受、任务取消与重试沿用既有
 * `/agent-jobs` 领域接口，不在聊天侧另造执行真相。
 */

import { V2Error, type V2ErrorBody, request as requestJson } from "./v2";

export type ChatRole = "author" | "assistant" | "system";

/** 对话轮次状态（计划 §4.1）：准备发送 → 排队 → 回复中 → 完成/已停止/失败/已中断。 */
export type TurnStatus =
  | "queued"
  | "running"
  | "completed"
  | "stopped"
  | "failed"
  | "interrupted";

export type MessageKind =
  | "text"
  | "task_draft"
  | "task_execution"
  | "candidate"
  | "result";

export type MessageStatus = "streaming" | "completed" | "stopped" | "failed";

/** 上下文引用：已保存内容带版本，未保存摘录一次性附加并明确标注（§3.4）。 */
export interface ChatReference {
  ref_id: string;
  kind: "chapter" | "artifact" | "selection";
  target_id: string;
  title: string;
  version: string;
  saved: boolean;
  excerpt?: string;
}

export interface ChatErrorBody {
  code: string;
  category: string;
  where: string;
  why: string;
  fix: string;
  details?: Record<string, unknown>;
}

export interface ChatMessage {
  message_id: string;
  conversation_id: string;
  turn_id: string | null;
  role: ChatRole;
  kind: MessageKind;
  text: string;
  refs: ChatReference[];
  /** 本轮实际模型（发送时定格，不随后续切换改写）。 */
  model: string | null;
  status: MessageStatus;
  error: ChatErrorBody | null;
  created_at: string;
  payload?: TaskDraftPayload | TaskExecutionPayload | CandidatePayload | ResultPayload;
}

export interface ChatTurn {
  turn_id: string;
  conversation_id: string;
  status: TurnStatus;
  model: string | null;
  end_reason: string | null;
  idempotency_key: string;
  retry_of: string | null;
}

export interface ChatConversation {
  conversation_id: string;
  project_id: string;
  title: string;
  created_at: string;
  updated_at: string;
}

/** 四类创作入口（§4.3）：规划 / 章节起草 / 检查 / 定向修订。 */
export type TaskKind = "plan" | "chapter_draft" | "check" | "targeted_revision";

export const taskKindLabels: Record<TaskKind, string> = {
  plan: "规划故事与节拍",
  chapter_draft: "生成章节草稿",
  check: "检查故事一致性",
  targeted_revision: "定向修订正文",
};

/** 受限 TaskDraft：建议本身不创建 Job，开始按钮在范围齐全时才可用。 */
export interface TaskDraftPayload {
  draft_id: string;
  revision: number;
  task_kind: TaskKind;
  instruction: string;
  goal: string;
  target_ids: string[];
  base_version: string | null;
  refs: ChatReference[];
  basis: string[];
  missing: (string | { code: string; where: string; why: string; fix: string })[];
  source_message_id: string | null;
  job_ref: string | null;
}

export type TaskExecutionState =
  | "queued"
  | "running"
  | "waiting"
  | "completed"
  | "cancelled"
  | "failed";

export interface TaskExecutionPayload {
  job_ref: string;
  task_kind: TaskKind;
  state: TaskExecutionState;
  stage: string;
  model: string;
  draft_id: string;
  candidate_message_id: string | null;
  error: ChatErrorBody | null;
  change_ref?: string | null;
}

export interface CandidatePayload {
  job_ref: string;
  summary: string;
  target_ids: string[];
  change_ref: string | null;
  accepted: boolean;
}

export interface ResultPayload {
  job_ref: string;
  commit_id: string;
  target_ids: string[];
  summary: string;
}

// ---------------------------------------------------------------------------
// 流式事件（§6.3）
// ---------------------------------------------------------------------------

export type ChatEvent =
  | { type: "turn.accepted"; turn: ChatTurn }
  | { type: "turn.started"; turn_id: string; message_id: string; model: string }
  | { type: "message.delta"; turn_id: string; text: string }
  | { type: "message.completed"; turn_id: string; text: string; end_reason: string }
  | {
      type: "turn.cancelled" | "turn.failed" | "turn.interrupted";
      turn_id: string;
      reason: string;
      error?: ChatErrorBody;
    }
  | { type: "task.draft.created"; draft: TaskDraftPayload; message_id: string }
  | { type: "task.draft.updated"; draft: TaskDraftPayload }
  | { type: "task.draft.abandoned"; draft_id: string }
  | { type: "task.updated"; message_id: string; execution: TaskExecutionPayload }
  | {
      type: "task.linked";
      draft_id: string;
      job_ref: string;
      task_kind: TaskKind;
      model: string;
      message_id: string;
    }
  | {
      type: "candidate.ready";
      job_ref: string;
      message_id: string;
      candidate: CandidatePayload;
    }
  | { type: "result.accepted"; job_ref: string; message_id: string; result: ResultPayload };

/** 事件信封：序号仅在会话内递增，与 Job / 业务 Commit 游标分别管理。 */
export interface ChatEventEnvelope {
  project_id: string;
  conversation_id: string;
  turn_id: string | null;
  sequence: number;
  event: ChatEvent;
}

export interface ChatCapabilities {
  protocol: string;
  streaming: boolean;
  task_drafts: boolean;
  /** true = 模拟服务（开发/测试入口），界面必须常驻「模拟模式」标识。 */
  simulated: boolean;
}

/** 连接状态：断线是连接事实，不把模型执行标记成失败（§4.1）。 */
export type ChatConnectionStatus = "connecting" | "open" | "closed";

export interface SendTurnInput {
  text: string;
  refs: ChatReference[];
  idempotency_key: string;
}

/** 接着讨论（V03-1.5）：引用已保存作者消息启动轮次，与 text 互斥（服务端校验）。 */
export interface ContinueTurnInput {
  source_message_ids: string[];
  idempotency_key: string;
}

export interface SendTurnResult {
  turn: ChatTurn;
  user_message: ChatMessage;
}

export interface TaskDraftScope {
  goal?: string;
  target_ids?: string[];
  refs?: ChatReference[];
}

export interface ChatService {
  readonly kind: "simulator" | "api";
  capabilities(): ChatCapabilities;
  currentModel(): string | null;
  listConversations(projectId: string): Promise<ChatConversation[]>;
  createConversation(projectId: string): Promise<ChatConversation>;
  loadMessages(projectId: string, conversationId: string): Promise<ChatMessage[]>;
  sendTurn(
    projectId: string,
    conversationId: string,
    input: SendTurnInput,
  ): Promise<SendTurnResult>;
  /** 引用已保存消息继续讨论：主消息归入新轮次（不复制），服务端幂等可重放。 */
  continueTurn(
    projectId: string,
    conversationId: string,
    input: ContinueTurnInput,
  ): Promise<SendTurnResult>;
  cancelTurn(projectId: string, conversationId: string, turnId: string): Promise<void>;
  retryTurn(projectId: string, conversationId: string, turnId: string): Promise<ChatTurn>;
  adjustDraft(
    projectId: string,
    conversationId: string,
    draftId: string,
    expectedRevision: number,
    patch: { instruction?: string; goal?: string; target_ids?: string[] },
  ): Promise<TaskDraftPayload>;
  draftFromMessage(
    projectId: string,
    conversationId: string,
    messageId: string,
    taskKind: TaskKind,
    scope?: TaskDraftScope,
  ): Promise<{ draft: TaskDraftPayload; message_id: string }>;
  createDraft(
    projectId: string,
    conversationId: string,
    taskKind: TaskKind,
    instruction?: string,
    scope?: TaskDraftScope,
  ): Promise<{ draft: TaskDraftPayload; message_id: string }>;
  abandonDraft(projectId: string, conversationId: string, draftId: string): Promise<void>;
  startDraft(
    projectId: string,
    conversationId: string,
    draftId: string,
    expectedRevision: number,
  ): Promise<{ job_ref: string }>;
  retryTask(projectId: string, conversationId: string, jobRef: string): Promise<void>;
  acceptCandidate(
    projectId: string,
    conversationId: string,
    messageId: string,
    expectedHead: string | null,
  ): Promise<ResultPayload>;
  cancelTask(projectId: string, conversationId: string, jobRef: string): Promise<void>;
  subscribe(
    projectId: string,
    conversationId: string,
    listener: (envelope: ChatEventEnvelope) => void,
    onStatus?: (status: ChatConnectionStatus, detail?: string) => void,
  ): () => void;
  /** 用户切换当前模型后，后续轮次使用新选择；已启动轮次保留原绑定。 */
  setCurrentModel?(model: string): void;
}

/** 模拟器可见的项目状态快照（只读，用于计算缺失项，不写入任何项目内容）。 */
export interface SimulatorProjectView {
  model: string;
  hasAcceptedBeats: boolean;
  currentChapter: { chapter_id: string; title: string; base: string } | null;
}

// ---------------------------------------------------------------------------
// SSE 帧解析（纯函数，便于钉死跨分片与非法帧行为）
// ---------------------------------------------------------------------------

/**
 * 按 SSE 约定切分缓冲：空行分帧，`data:` 行承载 JSON 信封。
 * 注释（`: heartbeat`）与事件名等其它字段被忽略；未完成的尾帧留在 `rest`。
 */
export function parseSseFrames(buffer: string): {
  envelopes: ChatEventEnvelope[];
  rest: string;
} {
  const chunks = buffer.split("\n\n");
  const rest = chunks.pop() ?? "";
  const envelopes: ChatEventEnvelope[] = [];
  for (const chunk of chunks) {
    for (const line of chunk.split("\n")) {
      if (!line.startsWith("data:")) continue;
      const payload = line.slice(5).trim();
      if (payload === "") continue;
      try {
        const parsed = JSON.parse(payload) as Partial<ChatEventEnvelope>;
        if (
          parsed &&
          typeof parsed.sequence === "number" &&
          typeof parsed.conversation_id === "string" &&
          parsed.event !== undefined
        ) {
          envelopes.push(parsed as ChatEventEnvelope);
        }
      } catch {
        /* 非法帧不能破坏后续合法帧 */
      }
    }
  }
  return { envelopes, rest };
}

// ---------------------------------------------------------------------------
// 能力探测（§19.2：缺少新版端点 → 重启后端指引，不显示假空列表）
// ---------------------------------------------------------------------------

export const CHAT_BACKEND_MISMATCH =
  "前后端版本不匹配：当前后端尚未加载对话接口。请重启后端服务，再刷新页面。";
export const CHAT_BACKEND_UNREACHABLE =
  "无法连接后端对话服务。请确认服务已启动，然后重试。";

export type CapabilityReason = "ok" | "missing_endpoints" | "unreachable";

export interface ChatCapabilityProbe {
  supported: boolean;
  protocol: string | null;
  reason: CapabilityReason;
  detail: string;
}

/** 探测项目对话端点是否可用（GET /conversations）。404/405 ≠ 网络故障。 */
export async function probeChatCapability(
  projectId: string,
  fetchImpl: typeof fetch = fetch,
): Promise<ChatCapabilityProbe> {
  try {
    const res = await fetchImpl(
      `/api/v2/projects/${encodeURIComponent(projectId)}/conversations`,
      { headers: { Accept: "application/json" } },
    );
    if (res.status === 404 || res.status === 405) {
      return {
        supported: false,
        protocol: null,
        reason: "missing_endpoints",
        detail: CHAT_BACKEND_MISMATCH,
      };
    }
    if (!res.ok) {
      return {
        supported: false,
        protocol: null,
        reason: "unreachable",
        detail: `对话服务返回 HTTP ${res.status}；确认后端已启动后重试。`,
      };
    }
    const body = (await res.json().catch(() => ({}))) as { protocol?: unknown };
    return {
      supported: true,
      protocol: typeof body.protocol === "string" ? body.protocol : "chat.v1",
      reason: "ok",
      detail: "",
    };
  } catch {
    return {
      supported: false,
      protocol: null,
      reason: "unreachable",
      detail: CHAT_BACKEND_UNREACHABLE,
    };
  }
}

// ---------------------------------------------------------------------------
// HTTP 客户端
// ---------------------------------------------------------------------------

export interface HttpChatServiceOptions {
  /** 模型标签来源（当前选择）；未注入时显示“未配置”。 */
  modelLabel?: () => string | null;
  fetchImpl?: typeof fetch;
  /** 断线重连首次退避（毫秒），测试注入小值。 */
  retryBaseMs?: number;
}

const projectPrefix = (projectId: string) =>
  `/api/v2/projects/${encodeURIComponent(projectId)}`;

export function createHttpChatService(
  projectId: string,
  options: HttpChatServiceOptions = {},
): ChatService {
  const fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));
  const retryBase = options.retryBaseMs ?? 400;
  const startKeys = new Map<string, string>();

  const conversationUrl = (conversationId: string) =>
    `${projectPrefix(projectId)}/conversations/${encodeURIComponent(conversationId)}`;

  async function listConversations(): Promise<ChatConversation[]> {
    const body = await requestJson<
      { conversations?: ChatConversation[] } | ChatConversation[]
    >(`${projectPrefix(projectId)}/conversations`);
    return Array.isArray(body) ? body : (body.conversations ?? []);
  }

  async function createConversation(): Promise<ChatConversation> {
    return requestJson<ChatConversation>(`${projectPrefix(projectId)}/conversations`, {});
  }

  async function loadMessages(conversationId: string): Promise<ChatMessage[]> {
    const body = await requestJson<{ messages?: ChatMessage[] } | ChatMessage[]>(
      `${conversationUrl(conversationId)}/messages`,
    );
    return Array.isArray(body) ? body : (body.messages ?? []);
  }

  async function sendTurn(conversationId: string, input: SendTurnInput) {
    return requestJson<SendTurnResult>(`${conversationUrl(conversationId)}/turns`, {
      text: input.text,
      refs: input.refs,
      idempotency_key: input.idempotency_key,
    });
  }

  async function continueTurn(conversationId: string, input: ContinueTurnInput) {
    // text 与 source_message_ids 服务端互斥：接着讨论请求不携带 text。
    return requestJson<SendTurnResult>(`${conversationUrl(conversationId)}/turns`, {
      source_message_ids: input.source_message_ids,
      idempotency_key: input.idempotency_key,
    });
  }

  async function cancelTurn(conversationId: string, turnId: string): Promise<void> {
    await requestJson(
      `${conversationUrl(conversationId)}/turns/${encodeURIComponent(turnId)}/cancel`,
      {},
    );
  }

  async function retryTurn(conversationId: string, turnId: string): Promise<ChatTurn> {
    return requestJson<ChatTurn>(
      `${conversationUrl(conversationId)}/turns/${encodeURIComponent(turnId)}/retry`,
      {},
    );
  }

  async function createDraft(
    conversationId: string,
    taskKind: TaskKind,
    instruction = "",
    scope: TaskDraftScope = {},
  ) {
    return requestJson<{ draft: TaskDraftPayload; message_id: string }>(
      `${projectPrefix(projectId)}/task-drafts`,
      {
        conversation_id: conversationId,
        task_kind: taskKind,
        instruction,
        ...scope,
      },
    );
  }

  async function draftFromMessage(
    conversationId: string,
    messageId: string,
    taskKind: TaskKind,
    scope: TaskDraftScope = {},
  ) {
    return requestJson<{ draft: TaskDraftPayload; message_id: string }>(
      `${projectPrefix(projectId)}/task-drafts`,
      {
        conversation_id: conversationId,
        source_message_id: messageId,
        task_kind: taskKind,
        ...scope,
      },
    );
  }

  async function adjustDraft(
    _conversationId: string,
    draftId: string,
    expectedRevision: number,
    patch: { instruction?: string; goal?: string; target_ids?: string[] },
  ) {
    const body = await requestJson<{ draft: TaskDraftPayload }>(
      `${projectPrefix(projectId)}/task-drafts/${encodeURIComponent(draftId)}`,
      { expected_revision: expectedRevision, ...patch },
      "PATCH",
    );
    return body.draft;
  }

  async function abandonDraft(_conversationId: string, draftId: string): Promise<void> {
    await requestJson(
      `${projectPrefix(projectId)}/task-drafts/${encodeURIComponent(draftId)}/abandon`,
      {},
    );
  }

  async function startDraft(
    _conversationId: string,
    draftId: string,
    expectedRevision: number,
  ) {
    const storageKey = `hastings.chat.start:${projectId}:${draftId}:${expectedRevision}`;
    let key = startKeys.get(storageKey);
    try { key ??= sessionStorage.getItem(storageKey) ?? undefined; } catch { /* 本次会话去重 */ }
    key ??= crypto.randomUUID();
    startKeys.set(storageKey, key);
    try { sessionStorage.setItem(storageKey, key); } catch { /* 本次会话去重 */ }
    return requestJson<{ job_ref: string }>(
      `${projectPrefix(projectId)}/task-drafts/${encodeURIComponent(draftId)}/start`,
      { expected_revision: expectedRevision, idempotency_key: key },
    );
  }

  async function retryTask(conversationId: string, jobRef: string): Promise<void> {
    // 任务重试沿用既有 Job 领域规则，不在聊天侧重建调度。
    await requestJson(
      `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobRef)}/run`,
      { idempotency_key: crypto.randomUUID() },
    );
    void conversationId;
  }

  async function cancelTask(conversationId: string, jobRef: string): Promise<void> {
    await requestJson(
      `${projectPrefix(projectId)}/agent-jobs/${encodeURIComponent(jobRef)}/cancel`,
      { idempotency_key: crypto.randomUUID() },
    );
    void conversationId;
  }

  async function acceptCandidate(
    conversationId: string,
    messageId: string,
    expectedHead: string | null,
  ): Promise<ResultPayload> {
    if (!expectedHead) {
      throw new V2Error(
        {
          code: "CHAT_MISSING_BASE_VERSION",
          category: "violation",
          where: "chat.accept",
          why: "项目版本尚未读取，无法确认接受候选的基础版本",
          fix: "等正文与版本读取完成后再接受候选，避免把候选提交到过期版本",
          details: { message_id: messageId },
        } satisfies V2ErrorBody,
        400,
      );
    }
    // 候选接受走既有审阅规则（服务端把候选消息映射回 Job/Change 再复用接受接口），
    // 聊天文本不代替审批（§3.3、§19.5）。
    return requestJson<ResultPayload>(
      `${conversationUrl(conversationId)}/messages/${encodeURIComponent(messageId)}/accept`,
      { expected_head: expectedHead, idempotency_key: crypto.randomUUID() },
    );
  }

  function subscribe(
    conversationId: string,
    listener: (envelope: ChatEventEnvelope) => void,
    onStatus?: (status: ChatConnectionStatus, detail?: string) => void,
  ): () => void {
    const controller = new AbortController();
    let cursor = 0;
    let closed = false;
    let backoff = retryBase;
    const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

    const run = async () => {
      while (!closed) {
        onStatus?.("connecting");
        try {
          // 重新订阅不重复调用模型：只按序号补齐（§6.3）。
          const res = await fetchImpl(
            `${conversationUrl(conversationId)}/events?after=${cursor}`,
            {
              headers: { Accept: "text/event-stream" },
              signal: controller.signal,
            },
          );
          if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);
          onStatus?.("open");
          backoff = retryBase;
          const reader = res.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          for (;;) {
            const { value, done } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const { envelopes, rest } = parseSseFrames(buffer);
            buffer = rest;
            for (const envelope of envelopes) {
              if (envelope.sequence > cursor) cursor = envelope.sequence;
              listener(envelope);
            }
          }
          if (closed) return;
          onStatus?.("closed", "服务端结束推送");
        } catch (e) {
          if (closed || controller.signal.aborted) return;
          onStatus?.("closed", e instanceof Error ? e.message : String(e));
        }
        await delay(backoff);
        backoff = Math.min(backoff * 2, 4000);
      }
    };
    void run();
    return () => {
      closed = true;
      controller.abort();
    };
  }

  return {
    kind: "api",
    capabilities: () => ({
      protocol: "chat.v1",
      streaming: true,
      task_drafts: true,
      simulated: false,
    }),
    currentModel: () => options.modelLabel?.() ?? null,
    listConversations: () => listConversations(),
    createConversation: () => createConversation(),
    loadMessages: (_projectId, conversationId) => loadMessages(conversationId),
    sendTurn: (_projectId, conversationId, input) => sendTurn(conversationId, input),
    continueTurn: (_projectId, conversationId, input) =>
      continueTurn(conversationId, input),
    cancelTurn: (_projectId, conversationId, turnId) => cancelTurn(conversationId, turnId),
    retryTurn: (_projectId, conversationId, turnId) => retryTurn(conversationId, turnId),
    adjustDraft: (_projectId, conversationId, draftId, revision, patch) =>
      adjustDraft(conversationId, draftId, revision, patch),
    draftFromMessage: (_projectId, conversationId, messageId, taskKind, scope) =>
      draftFromMessage(conversationId, messageId, taskKind, scope),
    createDraft: (_projectId, conversationId, taskKind, instruction, scope) =>
      createDraft(conversationId, taskKind, instruction, scope),
    abandonDraft: (_projectId, conversationId, draftId) =>
      abandonDraft(conversationId, draftId),
    startDraft: (_projectId, conversationId, draftId, revision) =>
      startDraft(conversationId, draftId, revision),
    retryTask: (_projectId, conversationId, jobRef) =>
      retryTask(conversationId, jobRef),
    acceptCandidate: (_projectId, conversationId, messageId, expectedHead) =>
      acceptCandidate(conversationId, messageId, expectedHead),
    cancelTask: (_projectId, conversationId, jobRef) => cancelTask(conversationId, jobRef),
    subscribe: (_projectId, conversationId, listener, onStatus) =>
      subscribe(conversationId, listener, onStatus),
  };
}

/**
 * 可控模拟器（Phase 19.1，19.2 迁入契约同层）：开发/测试入口启用的对话服务替身。
 *
 * 与真实 HTTP 客户端实现同一 `ChatService` 契约（见 api/chat.ts），
 * 因此组件可以「同一套组件切换模拟服务与真实 API」。
 *
 * 边界：只在开发模式或显式 `?chatMock=1` 入口启用；界面常驻「模拟模式」标识；
 * 没有任何项目写端口（聊天不产生创作 Job/Commit，候选接受只在模拟层回放引用）。
 */
import type {
  CandidatePayload,
  ChatCapabilities,
  ChatConversation,
  ChatErrorBody,
  ChatEvent,
  ChatEventEnvelope,
  ChatMessage,
  ChatService,
  ChatTurn,
  ContinueTurnInput,
  ResultPayload,
  SendTurnInput,
  SendTurnResult,
  SimulatorProjectView,
  TaskDraftPayload,
  TaskExecutionPayload,
  TaskKind,
} from "./chat";
import { taskKindLabels } from "./chat";

/** 演示场景：由作者输入的关键词选择，走查清单逐项对应。 */
export type Scenario =
  | "discussion"
  | "plan_suggest"
  | "draft_suggest"
  | "check_suggest"
  | "revision_suggest"
  | "failure"
  | "long";

export function scenarioFor(text: string): Scenario {
  const t = text.trim();
  if (t === "") return "discussion";
  if (t.includes("模拟失败") || t.includes("演示失败")) return "failure";
  if (t.includes("长回复")) return "long";
  if (/(写|起草|生成|来一?段)/.test(t) && /(第.章|这章|本章|草稿|正文|开篇)/.test(t))
    return "draft_suggest";
  if (/(规划|大纲|节拍|故事结构|分幕)/.test(t)) return "plan_suggest";
  if (/(检查|审计|一致|知情|线索|漏洞|矛盾)/.test(t)) return "check_suggest";
  if (/(修订|改成|换成|设定变化|如果.*会怎样|动机变)/.test(t))
    return "revision_suggest";
  return "discussion";
}

const CHUNK_MS = 60;
const PHASE_MS = 220;
const MODEL_FALLBACK = "模拟模型 · mock-local";

interface SimConversation {
  conversation: ChatConversation;
  messages: ChatMessage[];
  sequence: number;
  turns: Map<string, ChatTurn>;
  /** 运行中的轮次控制器：取消后不再产生任何分片。 */
  running: Map<string, { cancelled: boolean }>;
  drafts: Map<string, TaskDraftPayload>;
  executions: Map<string, TaskExecutionPayload>;
  /** 已取消任务：迟到输出不得发布为可接受候选。 */
  cancelledJobs: Set<string>;
}

export class SimulatorService implements ChatService {
  readonly kind = "simulator" as const;
  private conversations = new Map<string, SimConversation[]>();
  private listeners = new Map<string, Set<(e: ChatEventEnvelope) => void>>();
  private idSeed = 0;
  private view: SimulatorProjectView = {
    model: MODEL_FALLBACK,
    hasAcceptedBeats: false,
    currentChapter: null,
  };
  /** 固定时序，便于 E2E 与人工走查；不引入随机抖动。 */
  readonly timing = { chunkMs: CHUNK_MS, phaseMs: PHASE_MS };

  constructor(view?: Partial<SimulatorProjectView>) {
    if (view) this.view = { ...this.view, ...view };
  }

  setView(view: Partial<SimulatorProjectView>) {
    this.view = { ...this.view, ...view };
  }

  capabilities(): ChatCapabilities {
    return {
      protocol: "chat.v1",
      streaming: true,
      task_drafts: true,
      simulated: true,
    };
  }

  currentModel(): string | null {
    return this.view.model;
  }

  // -- 会话 ----------------------------------------------------------------

  async listConversations(projectId: string): Promise<ChatConversation[]> {
    return (this.conversations.get(projectId) ?? []).map((c) => c.conversation);
  }

  async createConversation(projectId: string): Promise<ChatConversation> {
    const now = new Date().toISOString();
    const conversation: ChatConversation = {
      conversation_id: this.newId("conv"),
      project_id: projectId,
      title: "新对话",
      created_at: now,
      updated_at: now,
    };
    const rows = this.conversations.get(projectId) ?? [];
    rows.push({
      conversation,
      messages: [],
      sequence: 0,
      turns: new Map(),
      running: new Map(),
      drafts: new Map(),
      executions: new Map(),
      cancelledJobs: new Set(),
    });
    this.conversations.set(projectId, rows);
    return conversation;
  }

  async loadMessages(
    _projectId: string,
    conversationId: string,
  ): Promise<ChatMessage[]> {
    const conv = this.find(conversationId);
    return conv ? [...conv.messages] : [];
  }

  // -- 轮次 ----------------------------------------------------------------

  async sendTurn(
    projectId: string,
    conversationId: string,
    input: SendTurnInput,
  ): Promise<SendTurnResult> {
    const conv = this.require(projectId, conversationId);
    // 发送幂等：同一幂等键重复提交返回同一轮次，不再次调用模型。
    for (const turn of conv.turns.values()) {
      if (turn.idempotency_key === input.idempotency_key) {
        return {
          turn,
          user_message:
            conv.messages.find((m) => m.turn_id === turn.turn_id && m.role === "author") ??
            this.authorMessage(conv, input),
        };
      }
    }
    const userMessage = this.authorMessage(conv, input);
    conv.messages.push(userMessage);
    const turn: ChatTurn = {
      turn_id: this.newId("turn"),
      conversation_id: conversationId,
      status: "queued",
      model: this.view.model,
      end_reason: null,
      idempotency_key: input.idempotency_key,
      retry_of: null,
    };
    conv.turns.set(turn.turn_id, turn);
    this.emit(projectId, conversationId, turn.turn_id, { type: "turn.accepted", turn });
    void this.runTurn(projectId, conv, turn, input);
    return { turn, user_message: userMessage };
  }

  /**
   * 接着讨论（V03-1.5，镜像后端 send_turn_from_messages 语义）：
   * - 主消息（第一条引用）reparent 进新轮次——同一 message_id，不复制；
   * - 引用源必须是本会话内的作者消息（跨会话/助手消息拒绝）；
   * - 幂等重放返回同一轮次与同一主消息，不再产生新回复。
   */
  async continueTurn(
    projectId: string,
    conversationId: string,
    input: ContinueTurnInput,
  ): Promise<SendTurnResult> {
    const conv = this.require(projectId, conversationId);
    if (input.source_message_ids.length === 0)
      throw new Error("接着讨论至少引用一条已保存消息");
    // 幂等重放：同一幂等键返回原轮次与其主消息（reparent 后按 turn 查找）。
    for (const turn of conv.turns.values()) {
      if (turn.idempotency_key === input.idempotency_key) {
        return {
          turn,
          user_message:
            conv.messages.find((m) => m.turn_id === turn.turn_id && m.role === "author") ??
            this.newRejected(conv, "原轮次没有可回读的主消息"),
        };
      }
    }
    const sources = input.source_message_ids.map((id) =>
      conv.messages.find((m) => m.message_id === id),
    );
    if (sources.some((s) => !s || s.role !== "author")) {
      throw new Error(
        "引用的消息不存在、属于其他讨论或不是作者消息；只能引用本讨论内保存的想法继续。",
      );
    }
    const primary = sources[0]!;
    const turn: ChatTurn = {
      turn_id: this.newId("turn"),
      conversation_id: conversationId,
      status: "queued",
      model: this.view.model,
      end_reason: null,
      idempotency_key: input.idempotency_key,
      retry_of: null,
    };
    conv.turns.set(turn.turn_id, turn);
    // 主消息归入新轮次（upsert 语义）：保留原 message_id，不复制记录。
    primary.turn_id = turn.turn_id;
    this.emit(projectId, conversationId, turn.turn_id, { type: "turn.accepted", turn });
    void this.runTurn(projectId, conv, turn, {
      text: primary.text,
      refs: primary.refs,
      idempotency_key: input.idempotency_key,
    });
    return { turn, user_message: primary };
  }

  private newRejected(conv: SimConversation, why: string): ChatMessage {
    return {
      message_id: `missing-${this.newId("msg")}`,
      conversation_id: conv.conversation.conversation_id,
      turn_id: null,
      role: "system",
      kind: "text",
      text: why,
      refs: [],
      model: null,
      status: "failed",
      error: null,
      created_at: new Date().toISOString(),
    };
  }

  async cancelTurn(
    projectId: string,
    conversationId: string,
    turnId: string,
  ): Promise<void> {    const conv = this.require(projectId, conversationId);
    const control = conv.running.get(turnId);
    const turn = conv.turns.get(turnId);
    if (!turn) return;
    // 服务端取消：置旗标，迟到分片永远不会被生成。
    if (control) control.cancelled = true;
    if (turn.status === "running" || turn.status === "queued") {
      turn.status = "stopped";
      turn.end_reason = "author_cancelled";
      const assistant = conv.messages.find(
        (m) => m.turn_id === turnId && m.role === "assistant" && m.status === "streaming",
      );
      if (assistant) assistant.status = "stopped";
      this.emit(projectId, conversationId, turnId, {
        type: "turn.cancelled",
        turn_id: turnId,
        reason: "作者停止回复",
      });
    }
  }

  async retryTurn(
    projectId: string,
    conversationId: string,
    turnId: string,
  ): Promise<ChatTurn> {
    const conv = this.require(projectId, conversationId);
    const previous = conv.turns.get(turnId);
    if (!previous) throw new Error("原轮次不存在，无法重试");
    const turn: ChatTurn = {
      turn_id: this.newId("turn"),
      conversation_id: conversationId,
      status: "queued",
      // 默认重试使用原轮次的模型绑定（§4.1）。
      model: previous.model,
      end_reason: null,
      idempotency_key: this.newId("idem"),
      retry_of: turnId,
    };
    conv.turns.set(turn.turn_id, turn);
    this.emit(projectId, conversationId, turn.turn_id, { type: "turn.accepted", turn });
    const original = conv.messages.find(
      (m) => m.turn_id === turnId && m.role === "author",
    );
    void this.runTurn(projectId, conv, turn, {
      text: original?.text ?? "",
      refs: original?.refs ?? [],
      idempotency_key: turn.idempotency_key,
    });
    return turn;
  }

  // -- 任务草稿 -------------------------------------------------------------

  async adjustDraft(
    projectId: string,
    conversationId: string,
    draftId: string,
    expectedRevision: number,
    patch: { instruction?: string; goal?: string; target_ids?: string[] },
  ): Promise<TaskDraftPayload> {
    const conv = this.require(projectId, conversationId);
    const draft = conv.drafts.get(draftId);
    if (!draft) throw new Error("任务建议不存在或已放弃");
    if (draft.revision !== expectedRevision)
      throw new Error(
        `这份建议刚被修改过（当前修订 ${draft.revision}）。重新查看后再调整，避免覆盖别人的改动。`,
      );
    draft.revision += 1;
    if (patch.instruction !== undefined) draft.instruction = patch.instruction;
    if (patch.goal !== undefined) draft.goal = patch.goal;
    if (patch.target_ids !== undefined) draft.target_ids = patch.target_ids;
    this.recomputeMissing(draft);
    this.emit(projectId, conversationId, null, {
      type: "task.draft.updated",
      draft,
    });
    return draft;
  }

  async abandonDraft(
    projectId: string,
    conversationId: string,
    draftId: string,
  ): Promise<void> {
    const conv = this.require(projectId, conversationId);
    conv.drafts.delete(draftId);
    this.emit(projectId, conversationId, null, {
      type: "task.draft.abandoned",
      draft_id: draftId,
    });
  }

  async draftFromMessage(
    projectId: string,
    conversationId: string,
    messageId: string,
    taskKind: TaskKind,
  ): Promise<{ draft: TaskDraftPayload; message_id: string }> {
    const conv = this.require(projectId, conversationId);
    const source = conv.messages.find((m) => m.message_id === messageId);
    if (!source) throw new Error("原消息不存在，无法准备任务");
    const chapter = this.view.currentChapter;
    const draft: TaskDraftPayload = {
      draft_id: this.newId("draft"),
      revision: 1,
      task_kind: taskKind,
      instruction: source.text.slice(0, 400),
      goal:
        taskKind === "plan"
          ? "产出故事前提与节拍候选"
          : taskKind === "chapter_draft"
            ? `基于已接受节拍起草${chapter?.title ? `「${chapter.title}」` : "章节"}正文`
            : taskKind === "check"
              ? "检查人物知情与线索公平性"
              : "按设定变化定向修订受影响正文",
      target_ids: taskKind === "chapter_draft" && chapter ? [chapter.chapter_id] : [],
      base_version: taskKind === "chapter_draft" && chapter ? chapter.base || null : null,
      refs: source.refs,
      basis: [`基于消息 ${messageId.slice(0, 12)} 整理的讨论上下文`],
      missing: [],
      source_message_id: messageId,
      job_ref: null,
    };
    this.recomputeMissing(draft);
    conv.drafts.set(draft.draft_id, draft);
    const draftMessageId = this.newId("msg");
    conv.messages.push({
      message_id: draftMessageId,
      conversation_id: conversationId,
      turn_id: source.turn_id,
      role: "assistant",
      kind: "task_draft",
      text: "",
      refs: draft.refs,
      model: source.model,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
      payload: draft,
    });
    this.emit(projectId, conversationId, source.turn_id, {
      type: "task.draft.created",
      draft,
      message_id: draftMessageId,
    });
    return { draft, message_id: draftMessageId };
  }

  async createDraft(
    projectId: string,
    conversationId: string,
    taskKind: TaskKind,
    instruction = "",
  ): Promise<{ draft: TaskDraftPayload; message_id: string }> {
    const conv = this.require(projectId, conversationId);
    const chapter = this.view.currentChapter;
    const draft: TaskDraftPayload = {
      draft_id: this.newId("draft"),
      revision: 1,
      task_kind: taskKind,
      instruction,
      goal:
        taskKind === "plan"
          ? "产出故事前提与节拍候选"
          : taskKind === "chapter_draft"
            ? `基于已接受节拍起草${chapter?.title ? `「${chapter.title}」` : "章节"}正文`
            : taskKind === "check"
              ? "检查人物知情与线索公平性"
              : "按设定变化定向修订受影响正文",
      target_ids: taskKind === "chapter_draft" && chapter ? [chapter.chapter_id] : [],
      base_version: taskKind === "chapter_draft" && chapter ? chapter.base || null : null,
      refs: [],
      basis: ["作者从输入框显式发起"],
      missing: [],
      source_message_id: null,
      job_ref: null,
    };
    this.recomputeMissing(draft);
    conv.drafts.set(draft.draft_id, draft);
    const draftMessageId = this.newId("msg");
    conv.messages.push({
      message_id: draftMessageId,
      conversation_id: conversationId,
      turn_id: null,
      role: "system",
      kind: "task_draft",
      text: "",
      refs: [],
      model: this.view.model,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
      payload: draft,
    });
    this.emit(projectId, conversationId, null, {
      type: "task.draft.created",
      draft,
      message_id: draftMessageId,
    });
    return { draft, message_id: draftMessageId };
  }

  async retryTask(
    projectId: string,
    conversationId: string,
    jobRef: string,
  ): Promise<void> {
    const conv = this.require(projectId, conversationId);
    const execution = conv.executions.get(jobRef);
    if (!execution) throw new Error("任务不存在或已结束");
    if (!["failed", "cancelled"].includes(execution.state))
      throw new Error("只有失败或已取消的任务可以重试");
    conv.cancelledJobs.delete(jobRef);
    execution.state = "queued";
    execution.stage = "已重新排队";
    execution.error = null;
    const message = conv.messages.find(
      (m) =>
        m.kind === "task_execution" &&
        (m.payload as TaskExecutionPayload).job_ref === jobRef,
    );
    if (message) message.payload = { ...execution };
    void this.runExecution(projectId, conv, execution);
  }

  async startDraft(
    projectId: string,
    conversationId: string,
    draftId: string,
    expectedRevision: number,
  ): Promise<{ job_ref: string }> {
    const conv = this.require(projectId, conversationId);
    const draft = conv.drafts.get(draftId);
    if (!draft) throw new Error("任务建议不存在或已放弃");
    if (draft.revision !== expectedRevision)
      throw new Error(
        `这份建议刚被修改过（当前修订 ${draft.revision}）。重新查看后再开始，避免重复启动。`,
      );
    if (draft.missing.length > 0)
      throw new Error(
        `范围还不齐全：${draft.missing.join("；")}。先补齐再开始，避免生成偏离目标的内容。`,
      );
    if (draft.job_ref) return { job_ref: draft.job_ref };
    const jobRef = this.newId("job");
    draft.job_ref = jobRef;
    const execution: TaskExecutionPayload = {
      job_ref: jobRef,
      task_kind: draft.task_kind,
      state: "queued",
      stage: "已排队",
      model: this.view.model,
      draft_id: draftId,
      candidate_message_id: null,
      error: null,
    };
    conv.executions.set(jobRef, execution);
    const messageId = this.newId("msg");
    conv.messages.push({
      message_id: messageId,
      conversation_id: conversationId,
      turn_id: null,
      role: "system",
      kind: "task_execution",
      text: "",
      refs: [],
      model: this.view.model,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
      payload: execution,
    });
    this.emit(projectId, conversationId, null, {
      type: "task.linked",
      draft_id: draftId,
      job_ref: jobRef,
      task_kind: draft.task_kind,
      model: this.view.model,
      message_id: messageId,
    });
    void this.runExecution(projectId, conv, execution);
    return { job_ref: jobRef };
  }

  async acceptCandidate(
    projectId: string,
    conversationId: string,
    messageId: string,
    expectedHead: string | null,
  ): Promise<ResultPayload> {
    void expectedHead;
    const conv = this.require(projectId, conversationId);
    const message = conv.messages.find((m) => m.message_id === messageId);
    if (!message || message.kind !== "candidate")
      throw new Error("候选消息不存在，无法接受");
    const candidate = message.payload as CandidatePayload;
    if (candidate.accepted) throw new Error("这份候选已经接受过了");
    const execution = conv.executions.get(candidate.job_ref);
    const commitId = `sim-${this.newId("commit").slice(4, 12)}`;
    const result: ResultPayload = {
      job_ref: candidate.job_ref,
      commit_id: commitId,
      target_ids: candidate.target_ids,
      summary: candidate.summary,
    };
    candidate.accepted = true;
    if (execution) {
      execution.state = "completed";
      execution.stage = "已完成";
      execution.candidate_message_id = messageId;
    }
    const resultMessageId = this.newId("msg");
    conv.messages.push({
      message_id: resultMessageId,
      conversation_id: conversationId,
      turn_id: null,
      role: "system",
      kind: "result",
      text: "",
      refs: [],
      model: candidate.job_ref ? this.view.model : null,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
      payload: result,
    });
    this.emit(projectId, conversationId, null, {
      type: "result.accepted",
      job_ref: candidate.job_ref,
      message_id: resultMessageId,
      result,
    });
    return result;
  }

  async cancelTask(
    projectId: string,
    conversationId: string,
    jobRef: string,
  ): Promise<void> {
    const conv = this.require(projectId, conversationId);
    const execution = conv.executions.get(jobRef);
    if (!execution) throw new Error("任务不存在或已结束");
    if (["completed", "cancelled", "failed"].includes(execution.state)) return;
    execution.state = "cancelled";
    execution.stage = "已取消";
    conv.cancelledJobs.add(jobRef);
    const message = conv.messages.find(
      (m) => m.kind === "task_execution" && (m.payload as TaskExecutionPayload).job_ref === jobRef,
    );
    if (message) message.payload = { ...execution };
  }

  subscribe(
    projectId: string,
    conversationId: string,
    listener: (envelope: ChatEventEnvelope) => void,
  ): () => void {
    const key = this.roomKey(projectId, conversationId);
    const rooms = this.listeners.get(key) ?? new Set();
    rooms.add(listener);
    this.listeners.set(key, rooms);
    return () => {
      rooms.delete(listener);
    };
  }

  // -- 内部：轮次执行 --------------------------------------------------------

  private async runTurn(
    projectId: string,
    conv: SimConversation,
    turn: ChatTurn,
    input: SendTurnInput,
  ) {
    const scenario = scenarioFor(input.text);
    const script = replyScript(scenario, input.text);
    const assistantId = this.newId("msg");
    const assistant: ChatMessage = {
      message_id: assistantId,
      conversation_id: conv.conversation.conversation_id,
      turn_id: turn.turn_id,
      role: "assistant",
      kind: "text",
      text: "",
      refs: input.refs,
      model: turn.model,
      status: "streaming",
      error: null,
      created_at: new Date().toISOString(),
    };
    conv.messages.push(assistant);
    turn.status = "running";
    const control = { cancelled: false };
    conv.running.set(turn.turn_id, control);
    this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
      type: "turn.started",
      turn_id: turn.turn_id,
      message_id: assistantId,
      model: turn.model ?? MODEL_FALLBACK,
    });
    if (scenario === "failure") {
      await this.delay(CHUNK_MS);
      if (!this.alive(conv, turn.turn_id)) return;
      assistant.text = script.chunks[0] ?? "";
      this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
        type: "message.delta",
        turn_id: turn.turn_id,
        text: assistant.text,
      });
      const error: ChatErrorBody = {
        code: "CHAT_PROVIDER_UNAVAILABLE",
        category: "environment",
        where: "chat.turn",
        why: "模型连接中断：本地推理服务没有响应（演示注入的环境故障）",
        fix: "确认本地模型服务已启动后重试；已收到的部分回复会保留，可用重试继续这一轮",
      };
      await this.delay(CHUNK_MS);
      if (!this.alive(conv, turn.turn_id)) return;
      turn.status = "failed";
      turn.end_reason = "provider_unavailable";
      assistant.status = "failed";
      assistant.error = error;
      this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
        type: "turn.failed",
        turn_id: turn.turn_id,
        reason: error.why,
        error,
      });
      conv.running.delete(turn.turn_id);
      return;
    }
    for (const chunk of script.chunks) {
      await this.delay(CHUNK_MS);
      if (!this.alive(conv, turn.turn_id)) return;
      assistant.text += chunk;
      this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
        type: "message.delta",
        turn_id: turn.turn_id,
        text: chunk,
      });
    }
    if (!this.alive(conv, turn.turn_id)) return;
    conv.running.delete(turn.turn_id);
    turn.status = "completed";
    turn.end_reason = "stop";
    assistant.status = "completed";
    this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
      type: "message.completed",
      turn_id: turn.turn_id,
      text: assistant.text,
      end_reason: "stop",
    });
    if (scenario !== "discussion" && scenario !== "long") {
      await this.delay(PHASE_MS);
      if (!this.notCancelled(conv, turn.turn_id)) return;
      const draft = this.buildDraft(scenario, input, assistant.message_id);
      conv.drafts.set(draft.draft_id, draft);
      const draftMessageId = this.newId("msg");
      conv.messages.push({
        message_id: draftMessageId,
        conversation_id: conv.conversation.conversation_id,
        turn_id: turn.turn_id,
        role: "assistant",
        kind: "task_draft",
        text: "",
        refs: draft.refs,
        model: turn.model,
        status: "completed",
        error: null,
        created_at: new Date().toISOString(),
        payload: draft,
      });
      this.emit(projectId, conv.conversation.conversation_id, turn.turn_id, {
        type: "task.draft.created",
        draft,
        message_id: draftMessageId,
      });
    }
  }

  private async runExecution(
    projectId: string,
    conv: SimConversation,
    execution: TaskExecutionPayload,
  ) {
    const stages: [TaskExecutionPayload["state"], string][] = [
      ["running", `正在${taskKindLabels[execution.task_kind]}`],
      ["running", "正在整理候选与依据"],
      ["waiting", "候选已返回，等待作者审阅"],
    ];
    for (const [state, stage] of stages) {
      await this.delay(PHASE_MS);
      if (conv.cancelledJobs.has(execution.job_ref)) return;
      execution.state = state;
      execution.stage = stage;
      const message = conv.messages.find(
        (m) =>
          m.kind === "task_execution" &&
          (m.payload as TaskExecutionPayload).job_ref === execution.job_ref,
      );
      if (message) message.payload = { ...execution };
    }
    const candidateMessageId = this.newId("msg");
    execution.candidate_message_id = candidateMessageId;
    const candidate: CandidatePayload = {
      job_ref: execution.job_ref,
      summary: `${taskKindLabels[execution.task_kind]}：候选已生成，接受前正式内容不变`,
      target_ids: this.draftTargets(conv, execution.draft_id),
      change_ref: null,
      accepted: false,
    };
    conv.messages.push({
      message_id: candidateMessageId,
      conversation_id: conv.conversation.conversation_id,
      turn_id: null,
      role: "system",
      kind: "candidate",
      text: "",
      refs: [],
      model: execution.model,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
      payload: candidate,
    });
    this.emit(projectId, conv.conversation.conversation_id, null, {
      type: "candidate.ready",
      job_ref: execution.job_ref,
      message_id: candidateMessageId,
      candidate,
    });
  }

  // -- 内部：草稿构造 --------------------------------------------------------

  private buildDraft(
    scenario: Scenario,
    input: SendTurnInput,
    sourceMessageId: string,
  ): TaskDraftPayload {
    const kind: TaskKind =
      scenario === "plan_suggest"
        ? "plan"
        : scenario === "draft_suggest"
          ? "chapter_draft"
          : scenario === "check_suggest"
            ? "check"
            : "targeted_revision";
    const chapter = this.view.currentChapter;
    const targetIds =
      kind === "chapter_draft" && chapter ? [chapter.chapter_id] : [];
    const baseVersion =
      kind === "chapter_draft" && chapter ? chapter.base || null : null;
    const draft: TaskDraftPayload = {
      draft_id: this.newId("draft"),
      revision: 1,
      task_kind: kind,
      instruction: input.text,
      goal:
        kind === "plan"
          ? "产出故事前提与节拍候选"
          : kind === "chapter_draft"
            ? `基于已接受节拍起草${chapter?.title ? `「${chapter.title}」` : "章节"}正文`
            : kind === "check"
              ? "检查人物知情与线索公平性"
              : "按设定变化定向修订受影响正文",
      target_ids: targetIds,
      base_version: baseVersion,
      refs: input.refs,
      basis: ["本次对话上下文", "当前项目已保存内容"],
      missing: [],
      source_message_id: sourceMessageId,
      job_ref: null,
    };
    this.recomputeMissing(draft);
    return draft;
  }

  /** 缺失项是「哪里错、为什么、怎样继续」的具体入口，不是空泛的“参数不足”。 */
  private recomputeMissing(draft: TaskDraftPayload) {
    const missing: string[] = [];
    if (draft.task_kind === "chapter_draft") {
      if (!this.view.hasAcceptedBeats)
        missing.push("还没有已接受的节拍：先完成规划候选并接受，再生成章节草稿");
      if (!draft.base_version)
        missing.push("缺少可靠的基础版本：先在创作台保存正文，再开始生成");
    }
    if (draft.task_kind === "targeted_revision" && draft.target_ids.length === 0)
      missing.push("还没有指定修订对象：在引用条添加章节或故事对象");
    if (draft.refs.some((r) => !r.saved) && draft.task_kind !== "check")
      missing.push("包含未保存摘录：先保存正文或改为引用已保存版本");
    draft.missing = missing;
  }

  private draftTargets(conv: SimConversation, draftId: string): string[] {
    return conv.drafts.get(draftId)?.target_ids ?? [];
  }

  // -- 内部：基础设施 --------------------------------------------------------

  private authorMessage(conv: SimConversation, input: SendTurnInput): ChatMessage {
    return {
      message_id: this.newId("msg"),
      conversation_id: conv.conversation.conversation_id,
      turn_id: null,
      role: "author",
      kind: "text",
      text: input.text,
      refs: input.refs,
      model: this.view.model,
      status: "completed",
      error: null,
      created_at: new Date().toISOString(),
    };
  }

  /** 测试/走查辅助：只落一条作者消息（不启动轮次），模拟「已保存的想法」。 */
  newAuthorMessageForTest(
    projectId: string,
    conversationId: string,
    text: string,
  ): ChatMessage {
    const conv = this.require(projectId, conversationId);
    const message = this.authorMessage(conv, {
      text,
      refs: [],
      idempotency_key: this.newId("idem"),
    });
    conv.messages.push(message);
    return message;
  }

  /** 轮次是否仍在运行：取消或进程级中断后迟到分片一律无效。 */
  private alive(conv: SimConversation, turnId: string): boolean {
    const turn = conv.turns.get(turnId);
    if (!turn || turn.status !== "running") return false;
    return this.notCancelled(conv, turnId);
  }

  /** 只看取消旗标：终态之后的收尾事件（如任务建议）不被取消误伤。 */
  private notCancelled(conv: SimConversation, turnId: string): boolean {
    const control = conv.running.get(turnId);
    return !control?.cancelled;
  }

  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  private nextSequence(conv: SimConversation): number {
    conv.sequence += 1;
    return conv.sequence;
  }

  private emit(
    projectId: string,
    conversationId: string,
    turnId: string | null,
    event: ChatEvent,
  ) {
    // 序号仅在会话内递增，随信封下发（§6.3）；先持久化后推送的同序关系由
    // 存储层保证，模拟器按发射顺序分配。
    const conv = this.find(conversationId);
    const envelope: ChatEventEnvelope = {
      project_id: projectId,
      conversation_id: conversationId,
      turn_id: turnId,
      sequence: conv ? this.nextSequence(conv) : 0,
      event,
    };
    const rooms = this.listeners.get(this.roomKey(projectId, conversationId));
    if (!rooms) return;
    for (const listener of [...rooms]) listener(envelope);
  }

  private roomKey(projectId: string, conversationId: string) {
    return `${projectId}::${conversationId}`;
  }

  private find(conversationId: string): SimConversation | undefined {
    for (const rows of this.conversations.values()) {
      const found = rows.find((c) => c.conversation.conversation_id === conversationId);
      if (found) return found;
    }
    return undefined;
  }

  private require(projectId: string, conversationId: string): SimConversation {
    const rows = this.conversations.get(projectId) ?? [];
    const conv = rows.find((c) => c.conversation.conversation_id === conversationId);
    if (!conv)
      throw new Error(
        `会话 ${conversationId} 不属于项目 ${projectId}（引用越界：对话按项目隔离）`,
      );
    return conv;
  }

  private newId(prefix: string): string {
    this.idSeed += 1;
    return `${prefix}-${Date.now().toString(36)}-${this.idSeed.toString(36)}`;
  }
}

interface ReplyScript {
  chunks: string[];
}

/** 演示回复脚本：分片即模型增量输出（19.3 起换成真实 Provider 分片）。 */
export function replyScript(scenario: Scenario, input: string): ReplyScript {
  const topic = input.trim().slice(0, 24) || "这个构想";
  switch (scenario) {
    case "plan_suggest":
      return {
        chunks: [
          `围绕「${topic}」，可以先搭一条三幕骨架：`,
          "\n\n- 第一幕：怀表在雨夜易手，信任出现第一道裂缝；",
          "- 第二幕：第二具尸体带来反转，协助者的动机需要重写；",
          "- 第三幕：真相依赖一枚不在场的物证。",
          "\n\n我已把这份方向整理成任务建议。确认范围后点击「开始」，才会真正创建创作任务。",
        ],
      };
    case "draft_suggest":
      return {
        chunks: [
          `这一章的核心是「交接」：动作要短，解释要少。`,
          "\n\n建议顺序：环境压迫 → 怀表特写 → 对话留白 → 信任裂纹。",
          "\n\n我已准备章节草稿任务建议；只有在已有接受节拍、基础版本可靠时才能开始。",
        ],
      };
    case "check_suggest":
      return {
        chunks: [
          `先检查三条线：`,
          "\n\n- 协助者在第一幕末尾不应知道怀表的内衬密码；",
          "- 线索「湿伞」出现过两次，第二次没有回收；",
          "- 姐姐的不在场证明依赖时钟，与第一章的时间线有冲突。",
          "\n\n可以按检查任务跑一遍确定性规则；没有发现也会明确告诉我。",
        ],
      };
    case "revision_suggest":
      return {
        chunks: [
          `如果把动机从「保护」改成「顶替」，受影响的不只是协助者：`,
          "\n\n- 第二章的密室手法需要新的解释；",
          "- 姐姐的撤案动机要补一条；",
          "- 已保存正文里三处「信任」表述要定向修订。",
          "\n\n这只是讨论。要改正式内容，需要走变更影响与批准流程——我已整理成定向修订建议。",
        ],
      };
    case "failure":
      return {
        chunks: ["我先说结论：这一章的问题不在结构，而在——"],
      };
    case "long":
      return {
        chunks: [
          `长回复演示：模拟模型会分很多片返回，便于验证滚动跟随、停止与断线恢复。`,
          ...Array.from({ length: 24 }, (_, i) => `\n\n第 ${i + 1} 段：这里是被裁掉的长文本，用来占位并测试增量渲染。`),
        ],
      };
    default:
      return {
        chunks: [
          `关于「${topic}」，我的看法是：`,
          "\n\n悬念不来自隐瞒信息，而来自读者比人物早半步知道真相。",
          "如果要强化这一层，可以让协助者在第一章就露出一个无法自证的动作。",
          "\n\n这只是讨论，不会改动你的正文、账本或任务。需要推进时，我会整理成任务建议等你确认。",
        ],
      };
  }
}

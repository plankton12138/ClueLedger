/**
 * 会话事件的归并（V05-2B）。
 *
 * 事件流是一条追加记录，界面上要看到的是「一轮里谁先说了什么」：作者要求、模型的
 * 思考与公开文字、工具执行、本轮结果。这里把这串事件折成按轮次分组的展示结构，
 * 实时接收与从头回放走同一个函数，因此两条路径必须得到相同结果。
 *
 * 三条边界：
 *
 * - **同一条回复只有一个位置**：一次响应由 `message_id` 标识，文字块按内容位置插入，
 *   正式公开文本（`assistant_message`）只更新那条消息，不再生成第二个气泡。
 * - **只改变化的那一块**：未受影响的轮次与内容块保持原来的对象引用，界面据此跳过重绘。
 * - **缺口不猜**：局部增量批次缺号时抛 `NeedsConversationReload`，由调用方回读日志；
 *   回读仍有缺口的记录标为不完整，不无限重连、也不补造文字。
 */

import type {
  BlockKind,
  Completion,
  ReviewFinding,
  ReviewStatus,
  RunPhase,
  RunResult,
  TaskGrant,
  TaskModuleGrant,
  TaskScope,
  WorkspaceEvent,
} from "../api/workspace";

/** 块与回复的展示状态：流式中还没有终态。 */
export type BlockCompletion = Completion | "streaming";

export interface ChatBlock {
  id: string;
  kind: BlockKind;
  order: number;
  text: string;
  /** 下一段应有的批次号；用于去重与识别缺口。 */
  nextPart: number;
  completion: BlockCompletion;
  durationMs: number | null;
}

export interface ChatMessage {
  id: string;
  blocks: ChatBlock[];
  /** 正式公开文本（`assistant_message`）；流式期间为空，由文字块拼出。 */
  text: string;
  completion: BlockCompletion;
  stopReason: string;
  durationMs: number | null;
}

export interface ChatTool {
  /** 工具调用 ID；旧记录没有它时退回事件序号。 */
  id: string;
  name: string;
  args: Record<string, unknown>;
  /** null 表示还在执行。 */
  ok: boolean | null;
  summary: string;
  durationMs: number | null;
}

export type TurnItem =
  | { kind: "message"; key: string; message: ChatMessage }
  | { kind: "tool"; key: string; tool: ChatTool };

export interface TurnError {
  key: string;
  code: string;
  message: string;
}

/** 本轮材料：只记路径、版本与已提供的区间，不保存原文。 */
export interface TurnMaterial {
  path: string;
  digest: string;
  /** 作者指定或明确要求的参考：写作品前必须读完。 */
  required: boolean;
  /** 已提供的原文区间（服务端字符下标，左闭右开）。 */
  spans: [number, number][];
  length: number;
}

export interface ChatTurn {
  id: string;
  prompt: string;
  at: string;
  items: TurnItem[];
  errors: TurnError[];
  result: RunResult | null;
  /** 局部增量缺号且未能由完整块校正：说明记录不完整。 */
  incomplete: boolean;
  /** 这一轮实际提供过的材料；没有指定参考时为空。 */
  materials: TurnMaterial[];
  /** 这一轮登记的修改范围；没有登记时为 null（纯讨论）。 */
  scope: TaskScope | null;
  /** 这一轮的检查报告摘要；没有检查时为 null。 */
  review: TurnReview | null;
  /** 这一轮装配进提示词的分区；没有装配记录时为 null。 */
  injection: TurnInjection | null;
}

/** 本轮注入：只记分区规模、跳过原因与未装入条目，不含注入正文。 */
export interface TurnInjection {
  profile: "writer" | "extract" | "review" | "materials";
  parts: TurnInjectionPart[];
  skipped: { key: string; reason: "budget" | "empty"; chars?: number }[];
  limit: number;
  used: number;
}

/** 一个分区：没装进去的候选带 id、原因与继续查看的入口。 */
export interface TurnInjectionPart {
  key: string;
  chars: number;
  excluded?: TurnExcludedEntry[];
}

export interface TurnExcludedEntry {
  id: string;
  reason: "budget" | "limit";
  chars?: number;
  location?: string;
}

/** 一轮检查的结果：只放展示需要的内容，完整报告由接口回看。 */
export interface TurnReview {
  reportId: string | null;
  status: ReviewStatus;
  findings: ReviewFinding[];
  uncovered: string[];
}

/** 已提供字符数：重叠区间合并后求和，避免重复累计。 */
export function materialReadChars(material: TurnMaterial): number {
  const spans = [...material.spans].sort((a, b) => a[0] - b[0]);
  let total = 0;
  let cursor = 0;
  for (const [start, end] of spans) {
    const from = Math.max(start, cursor);
    if (end > from) total += end - from;
    cursor = Math.max(cursor, end);
  }
  return total;
}

/** 是否已经提供了该版本全文；空文件算读完。 */
export function materialComplete(material: TurnMaterial): boolean {
  return material.length === 0 || materialReadChars(material) >= material.length;
}

/** 任务卡：本轮授权、约束、未读必读材料与执行事实；全部来自同一条事件流。 */
export interface TaskCardFacts {
  summary: string;
  authorization: string;
  authorizationSeq: number | null;
  carriedFromSeq: number | null;
  version: number | null;
  mode: "read_only" | "edit" | null;
  grants: TaskGrant[];
  moduleGrants: TaskModuleGrant[];
  libraryPublish: boolean;
  deniedPaths: string[];
  extendable: boolean;
  requiredCount: number;
  requiredComplete: boolean;
  requiredUnread: { path: string; remaining: number }[];
  status: RunResult["status"] | null;
  recording: RunResult["recording"] | null;
  changedPaths: string[];
  reviewStatus: ReviewStatus | null;
}

export function taskCardFacts(turn: ChatTurn): TaskCardFacts {
  const scope = turn.scope;
  const required = turn.materials.filter((material) => material.required);
  const unread = required.filter((material) => !materialComplete(material));
  return {
    summary: scope?.summary ?? "",
    authorization: scope?.authorization ?? "",
    authorizationSeq: scope?.authorization_seq ?? null,
    carriedFromSeq: scope?.carried_from_seq ?? null,
    version: scope?.version ?? null,
    mode: scope?.mode ?? null,
    grants: scope?.grants ?? [],
    moduleGrants: scope?.module_grants ?? [],
    libraryPublish: scope?.library_publish ?? false,
    deniedPaths: scope?.denied_paths ?? [],
    extendable: scope?.extendable ?? false,
    requiredCount: required.length,
    requiredComplete: required.length > 0 && unread.length === 0,
    requiredUnread: unread.map((material) => ({
      path: material.path,
      remaining: Math.max(0, material.length - materialReadChars(material)),
    })),
    status: turn.result?.status ?? null,
    recording: turn.result?.recording ?? null,
    changedPaths: turn.result?.changed_paths ?? [],
    reviewStatus: turn.review?.status ?? null,
  };
}

/** 任务卡是否有内容：没有范围、没有必读材料、也没有结果时不显示。 */
export function hasTaskCard(facts: TaskCardFacts): boolean {
  return (
    facts.mode !== null ||
    facts.requiredCount > 0 ||
    facts.status !== null ||
    facts.reviewStatus !== null
  );
}

/** 实时状态收到缺批时抛出：需要回读本会话日志，不重发用户请求。 */
export class NeedsConversationReload extends Error {
  constructor() {
    super("会话事件批次不连续");
    this.name = "NeedsConversationReload";
  }
}

const EMPTY_TURN = (id: string, at: string): ChatTurn => ({
  id,
  prompt: "",
  at,
  items: [],
  errors: [],
  result: null,
  incomplete: false,
  materials: [],
  scope: null,
  review: null,
  injection: null,
});

// -- 结构更新：只替换受影响的节点 -------------------------------------------

function withMaterial(
  turn: ChatTurn,
  path: string,
  digest: string,
  required: boolean,
  spans: [number, number][],
  length: number,
): ChatTurn {
  const index = turn.materials.findIndex((item) => item.path === path);
  if (index < 0) {
    const material: TurnMaterial = {
      path,
      digest,
      required,
      spans: spans.filter(([start, end]) => end > start),
      length,
    };
    return { ...turn, materials: [...turn.materials, material] };
  }
  const current = turn.materials[index]!;
  const material: TurnMaterial = {
    ...current,
    // 版本变化后不拼接新旧区间：只保留当前版本的读取记录。
    digest: digest || current.digest,
    required: required || current.required,
    spans: digest && digest !== current.digest ? [] : current.spans,
    length: length || current.length,
  };
  material.spans = [...material.spans, ...spans.filter(([start, end]) => end > start)];
  const materials = turn.materials.slice();
  materials[index] = material;
  return { ...turn, materials };
}

function withTurn(
  turns: ChatTurn[],
  turnId: string,
  update: (turn: ChatTurn) => ChatTurn,
  at = "",
): ChatTurn[] {
  const index = turns.findIndex((turn) => turn.id === turnId);
  if (index < 0) {
    const created = update(EMPTY_TURN(turnId, at));
    return [...turns, created];
  }
  const current = turns[index]!;
  const next = update(current);
  if (next === current) return turns;
  const copy = turns.slice();
  copy[index] = next;
  return copy;
}

function withMessage(
  turn: ChatTurn,
  messageId: string,
  update: (message: ChatMessage) => ChatMessage,
): ChatTurn {
  const index = turn.items.findIndex(
    (item) => item.kind === "message" && item.message.id === messageId,
  );
  if (index < 0) {
    const created = update(newMessage(messageId));
    return { ...turn, items: [...turn.items, { kind: "message", key: created.id, message: created }] };
  }
  const current = turn.items[index]!;
  if (current.kind !== "message") return turn;
  const next = update(current.message);
  if (next === current.message) return turn;
  const items = turn.items.slice();
  items[index] = { kind: "message", key: next.id, message: next };
  return { ...turn, items };
}

function withBlock(
  message: ChatMessage,
  blockId: string,
  update: (block: ChatBlock) => ChatBlock,
  fallback: () => ChatBlock,
): ChatMessage {
  const index = message.blocks.findIndex((block) => block.id === blockId);
  if (index < 0) {
    return { ...message, blocks: [...message.blocks, update(fallback())] };
  }
  const current = message.blocks[index]!;
  const next = update(current);
  if (next === current) return message;
  const blocks = message.blocks.slice();
  blocks[index] = next;
  return { ...message, blocks };
}

function newMessage(id: string): ChatMessage {
  return {
    id,
    blocks: [],
    text: "",
    completion: "streaming",
    stopReason: "",
    durationMs: null,
  };
}

function newBlock(id: string, kind: BlockKind, order: number): ChatBlock {
  return { id, kind, order, text: "", nextPart: 0, completion: "streaming", durationMs: null };
}

function sortedBlocks(blocks: ChatBlock[]): ChatBlock[] {
  return [...blocks].sort((left, right) => left.order - right.order);
}

// -- 归并 -------------------------------------------------------------------

/** 把一条事件并入展示结构；重复事件不改变结果。 */
export function appendEvent(turns: ChatTurn[], event: WorkspaceEvent): ChatTurn[] {
  switch (event.type) {
    case "turn_started": {
      if (turns.some((turn) => turn.id === event.turn_id)) return turns;
      return [
        ...turns,
        { ...EMPTY_TURN(event.turn_id, event.at), prompt: event.text },
      ];
    }
    case "run_phase":
      // 阶段只用于状态行，不进入消息结构。
      return turns;
    case "assistant_started": {
      const turn = turns.find((item) => item.id === event.turn_id);
      const exists = turn?.items.some(
        (item) => item.kind === "message" && item.message.id === event.message_id,
      );
      if (exists) return turns;
      return withTurn(turns, event.turn_id, (current) =>
        withMessage(current, event.message_id, (message) => message),
      );
    }
    case "assistant_block_started": {
      return withTurn(turns, event.turn_id, (turn) =>
        withMessage(turn, event.message_id, (message) => {
          if (message.blocks.some((block) => block.id === event.block_id)) return message;
          return {
            ...message,
            blocks: [...message.blocks, newBlock(event.block_id, event.kind, event.order)],
          };
        }),
      );
    }
    case "assistant_block_delta": {
      return withTurn(turns, event.turn_id, (turn) =>
        withMessage(turn, event.message_id, (message) =>
          withBlock(
            message,
            event.block_id,
            (block) => {
              if (event.part < block.nextPart) return block; // 已经收过这一段
              if (event.part > block.nextPart) throw new NeedsConversationReload();
              return { ...block, text: block.text + event.text, nextPart: event.part + 1 };
            },
            () => newBlock(event.block_id, event.kind, message.blocks.length),
          ),
        ),
      );
    }
    case "assistant_block_finished": {
      return withTurn(turns, event.turn_id, (turn) =>
        withMessage(turn, event.message_id, (message) =>
          withBlock(
            message,
            event.block_id,
            (block) => ({
              ...block,
              // 完整结束值是校对依据：覆盖已收增量，不再拼接一次。
              text: event.text,
              nextPart: Math.max(block.nextPart, event.last_part + 1),
              completion: event.completion,
              durationMs: event.duration_ms ?? null,
            }),
            () => newBlock(event.block_id, event.kind, message.blocks.length),
          ),
        ),
      );
    }
    case "assistant_message": {
      if (!event.message_id) {
        // 旧记录没有响应标识：单独展示为一条消息，不补造过程。
        const message: ChatMessage = {
          id: `legacy:${event.seq}`,
          blocks: [
            {
              ...newBlock(`legacy:${event.seq}:text:0`, "text", 0),
              text: event.text,
              completion: "completed",
            },
          ],
          text: event.text,
          completion: event.completion ?? "completed",
          stopReason: "",
          durationMs: null,
        };
        return withTurn(turns, event.turn_id, (turn) => ({
          ...turn,
          items: [...turn.items, { kind: "message", key: message.id, message }],
        }));
      }
      const messageId = event.message_id;
      return withTurn(turns, event.turn_id, (turn) =>
        withMessage(turn, messageId, (message) => ({
          ...message,
          text: event.text,
          completion: event.completion ?? message.completion,
        })),
      );
    }
    case "assistant_finished": {
      return withTurn(turns, event.turn_id, (turn) =>
        withMessage(turn, event.message_id, (message) => ({
          ...message,
          completion: event.completion,
          stopReason: event.stop_reason ?? "",
          durationMs: event.duration_ms ?? null,
        })),
      );
    }
    case "review_finished": {
      return withTurn(turns, event.turn_id, (turn) => ({
        ...turn,
        review: {
          reportId: event.report_id,
          status: event.status,
          findings: event.findings ?? [],
          uncovered: event.uncovered ?? [],
        },
      }));
    }
    case "task_scope_set":
    case "task_scope_extended": {
      const { seq, at, type, turn_id, ...scope } = event;
      void seq;
      void at;
      void type;
      void turn_id;
      return withTurn(turns, event.turn_id, (turn) =>
        // 只前进不后退：迟到的旧版本事件不能覆盖已经登记的更大版本。
        turn.scope && turn.scope.version > scope.version ? turn : { ...turn, scope },
      );
    }
    case "state_injected": {
      return withTurn(turns, event.turn_id, (turn) => ({
        ...turn,
        injection: {
          profile: event.profile,
          parts: event.parts,
          skipped: event.skipped,
          limit: event.budget.limit,
          used: event.budget.used,
        },
      }));
    }
    case "context_prepared": {
      const digests = new Map(event.references.map((item) => [item.path, item.digest]));
      return withTurn(turns, event.turn_id, (turn) => {
        let next = turn;
        for (const item of event.provided) {
          next = withMaterial(next, item.path, digests.get(item.path) ?? "", true, [
            [item.start, item.end],
          ], item.length);
        }
        for (const reference of event.references) {
          if (next.materials.some((material) => material.path === reference.path)) continue;
          next = withMaterial(next, reference.path, reference.digest, true, [], 0);
        }
        return next;
      });
    }
    case "reference_registered": {
      return withTurn(turns, event.turn_id, (turn) => {
        let next = turn;
        for (const reference of event.references) {
          next = withMaterial(next, reference.path, reference.digest, reference.required, [], 0);
        }
        return next;
      });
    }
    case "material_read": {
      return withTurn(turns, event.turn_id, (turn) =>
        withMaterial(
          turn,
          event.path,
          event.digest,
          turn.materials.find((item) => item.path === event.path)?.required ?? false,
          [[event.start_char, event.end_char]],
          event.total_chars,
        ),
      );
    }
    case "tool_started": {
      const tool: ChatTool = {
        id: event.tool_call_id ?? `tool:${event.seq}`,
        name: event.tool,
        args: event.args ?? {},
        ok: null,
        summary: "",
        durationMs: null,
      };
      return withTurn(turns, event.turn_id, (turn) => ({
        ...turn,
        items: [...turn.items, { kind: "tool", key: tool.id, tool }],
      }));
    }
    case "tool_finished": {
      return withTurn(turns, event.turn_id, (turn) => {
        const index = findRunningTool(turn, event.tool_call_id, event.tool);
        if (index < 0) {
          const tool: ChatTool = {
            id: event.tool_call_id ?? `tool:${event.seq}`,
            name: event.tool,
            args: event.args ?? {},
            ok: event.ok,
            summary: event.summary ?? "",
            durationMs: event.duration_ms ?? null,
          };
          return { ...turn, items: [...turn.items, { kind: "tool", key: tool.id, tool }] };
        }
        const items = turn.items.slice();
        const current = items[index]!;
        if (current.kind !== "tool") return turn;
        const tool: ChatTool = {
          ...current.tool,
          // 结束事件带同一份定位参数：旧记录靠它补齐展示。
          args: Object.keys(current.tool.args).length ? current.tool.args : (event.args ?? {}),
          ok: event.ok,
          summary: event.summary ?? "",
          durationMs: event.duration_ms ?? null,
        };
        items[index] = { kind: "tool", key: current.key, tool };
        return { ...turn, items };
      });
    }
    case "error": {
      return withTurn(turns, event.turn_id, (turn) => ({
        ...turn,
        errors: [
          ...turn.errors,
          {
            key: `error:${event.seq}`,
            code: event.code ?? "TURN_FAILED",
            message: event.message ?? "这一轮没有完成",
          },
        ],
      }));
    }
    case "run_finished": {
      return withTurn(turns, event.turn_id, (turn) => ({
        ...turn,
        result: {
          status: event.status,
          recording: event.recording,
          changed_paths: event.changed_paths ?? [],
          commit: event.commit ?? null,
          note: event.note ?? "",
        },
      }));
    }
    default:
      return turns;
  }
}

function findRunningTool(turn: ChatTurn, callId: string | undefined, name: string): number {
  if (callId) {
    const byId = turn.items.findIndex(
      (item) => item.kind === "tool" && item.tool.id === callId,
    );
    if (byId >= 0) return byId;
  }
  // 旧记录没有调用 ID：退回同名且仍在执行的那一条。
  for (let index = turn.items.length - 1; index >= 0; index -= 1) {
    const item = turn.items[index]!;
    if (item.kind === "tool" && item.tool.name === name && item.tool.ok === null) return index;
  }
  return -1;
}

/** 从头回放：与实时接收使用同一套归并规则。 */
export function buildTurns(events: WorkspaceEvent[]): ChatTurn[] {
  let turns: ChatTurn[] = [];
  for (const event of events) {
    try {
      turns = appendEvent(turns, event);
    } catch (error) {
      if (!(error instanceof NeedsConversationReload)) throw error;
      // 日志本身有缺号：保留其余内容，标记这一轮记录不完整，不猜缺失文字。
      turns = withTurn(turns, event.turn_id, (turn) => ({ ...turn, incomplete: true }), event.at);
    }
  }
  return turns;
}

// -- 读取辅助 ---------------------------------------------------------------

/** 该条回复的公开正文：完成后以正式文本为准，流式期间由文字块拼出。 */
export function publicText(message: ChatMessage): string {
  if (message.text.trim()) return message.text;
  return sortedBlocks(message.blocks)
    .filter((block) => block.kind === "text" && block.text.trim())
    .map((block) => block.text)
    .join("\n\n");
}

/** 按内容位置排列的块；思考与文字按模型原始顺序交替。 */
export function orderedBlocks(message: ChatMessage): ChatBlock[] {
  return sortedBlocks(message.blocks);
}

/** 在会话里按响应 ID 找回那条消息；宽阅读视图用它绑定同一个对象。 */
export function findMessage(turns: ChatTurn[], messageId: string): ChatMessage | null {
  for (const turn of turns) {
    for (const item of turn.items) {
      if (item.kind === "message" && item.message.id === messageId) return item.message;
    }
  }
  return null;
}

/** 这一轮是否有可观看的运行过程（思考或工具）。 */
export function hasProcess(turn: ChatTurn): boolean {
  return turn.items.some(
    (item) =>
      item.kind === "tool" ||
      (item.kind === "message" && item.message.blocks.some((block) => block.kind === "thinking")),
  );
}

const PHASE_LABEL: Record<RunPhase, string> = {
  model: "等待模型响应",
  tool: "正在执行工具",
  record: "正在记录历史",
};

/** 阶段说明；没有可靠阶段时不显示占位进度。 */
export function phaseLabel(phase: RunPhase | null): string {
  return phase ? PHASE_LABEL[phase] : "等待模型响应";
}

/** 耗时展示：不足一秒给毫秒，超过给一位小数的秒；没有记录时不显示。 */
export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return "";
  if (ms < 1) return "<1 毫秒";
  if (ms < 1000) return `${Math.round(ms)} 毫秒`;
  return `${(ms / 1000).toFixed(1)} 秒`;
}

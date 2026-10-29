/**
 * 操作事件归并（1.0.1 P2）。
 *
 * 事件来自同一条会话日志，但属于不同的代次。归并器只做三件事：按操作与游标去重、
 * 按代次分开输出、暂停后不再接收新文本。它不判断任务是否成功——那由操作快照说明。
 */

export interface OperationStreamState {
  operationId: string;
  cursor: number;
  generation: number;
  paused: boolean;
  /** 收到比当前更新的代次：需要先取快照，不能把新代次的字接进旧流。 */
  needsSnapshot: boolean;
  /** 键是 `代次:块`，旧代次的内容保留用于回看。 */
  blocks: Record<string, string>;
}

export type OperationEvent = {
  seq: number;
  operation_id: string;
  generation: number;
} & (
  | { type: "attempt_started" }
  | { type: "assistant_block_delta"; block_id: string; text: string }
  | { type: "stop_requested" }
  | { type: "run_finished"; status: string }
);

export function emptyOperationStream(operationId: string, generation = 1): OperationStreamState {
  return {
    operationId,
    cursor: 0,
    generation,
    paused: false,
    needsSnapshot: false,
    blocks: {},
  };
}

export function reduceOperationEvent(
  state: OperationStreamState,
  event: OperationEvent,
): OperationStreamState {
  if (event.operation_id !== state.operationId || event.seq <= state.cursor) return state;

  if (event.type === "attempt_started") {
    if (event.generation <= state.generation) return { ...state, cursor: event.seq };
    // 新一代开始：旧代的已显示内容保留，暂停标记清掉。
    return {
      ...state,
      cursor: event.seq,
      generation: event.generation,
      paused: false,
    };
  }

  if (event.generation < state.generation) return { ...state, cursor: event.seq };
  if (event.generation > state.generation) return { ...state, needsSnapshot: true };

  const next = { ...state, cursor: event.seq };
  if (event.type === "stop_requested") return { ...next, paused: true };
  if (event.type === "run_finished") return next;
  if (state.paused) return next;
  const key = `${event.generation}:${event.block_id}`;
  return {
    ...next,
    blocks: { ...state.blocks, [key]: (state.blocks[key] ?? "") + event.text },
  };
}

/** 当前代次的输出；旧代次只用于回看，不混进正在显示的内容。 */
export function currentBlocks(state: OperationStreamState): Record<string, string> {
  const prefix = `${state.generation}:`;
  const result: Record<string, string> = {};
  for (const [key, text] of Object.entries(state.blocks)) {
    if (key.startsWith(prefix)) result[key.slice(prefix.length)] = text;
  }
  return result;
}

/** 只用于浏览器渲染回归：受控 SSE 使用真实客户端、归并和组件，不调用模型或写作品。 */
import { createRoot } from "react-dom/client";

import type { WorkspaceEvent } from "../../src/api/workspace";
import ConversationPanel from "../../src/components/workspace/ConversationPanel";
import { useConversationsStore } from "../../src/state/conversations";
import { useModelsStore } from "../../src/state/models";

declare global {
  interface Window {
    __renderStats: { markdown: number; turns: Record<string, number> };
    chatFixture: {
      ready: boolean;
      push: (count: number) => void;
      finish: () => void;
      text: () => string;
      reset: () => void;
    };
  }
}

window.__renderStats = { markdown: 0, turns: {} };
const initial: WorkspaceEvent[] = [];
let seq = 0;
const event = (fields: object): WorkspaceEvent => ({ seq: ++seq, at: "", ...fields } as WorkspaceEvent);
const longText = Array.from({ length: 500 }, (_, i) =>
  `- 第${i + 1}条观察：核对人物、时间与原文。**保留证据**，讨论中的假设需要继续确认。\n\n`,
).join("");

for (const id of ["history1", "history2", "live"]) {
  const turn_id = `t_${id}`;
  const message_id = `m_${id}`;
  const block_id = `${message_id}:thinking:0`;
  initial.push(
    event({ type: "turn_started", turn_id, text: id === "live" ? "继续核对" : "整理人物" }),
    event({ type: "assistant_started", turn_id, message_id }),
    event({ type: "assistant_block_started", turn_id, message_id, block_id, kind: "thinking", order: 0 }),
    event({ type: "assistant_block_delta", turn_id, message_id, block_id, kind: "thinking", part: 0, text: longText }),
  );
  if (id !== "live") initial.push(
    event({ type: "assistant_block_finished", turn_id, message_id, block_id, kind: "thinking", text: longText, last_part: 0, completion: "completed" }),
    event({ type: "assistant_finished", turn_id, message_id, completion: "completed" }),
    event({ type: "run_finished", turn_id, status: "completed", recording: "no_change" }),
  );
}
const initialSeq = seq;
let part = 1;
let text = longText;
let active: object | null = { conversation_id: "c1", turn_id: "t_live", state: "running", model: "test", connection: "test", started_at: "" };
let output: ReadableStreamDefaultController<Uint8Array>;
const encoder = new TextEncoder();
const json = (data: unknown) => new Response(JSON.stringify(data), { headers: { "Content-Type": "application/json" } });
const emit = (events: WorkspaceEvent[]) => output.enqueue(encoder.encode(events.map(row => `data: ${JSON.stringify(row)}\n\n`).join("")));

window.fetch = async (input) => {
  const url = String(input);
  if (url.includes("/events?")) return new Response(new ReadableStream({
    start(controller) { output = controller; window.chatFixture.ready = true; },
  }), { headers: { "Content-Type": "text/event-stream" } });
  if (url.includes("/messages?")) return json({ events: initial, last_seq: initialSeq, active_turn: active, revision: "r1" });
  if (url.includes("conversation-ui")) return json({ recent_id: "c1", drafts: {}, revision: "r1" });
  if (url.endsWith("/conversations")) return json({ items: [{ id: "c1", title: "长思考回归", created_at: "", updated_at: "" }], recent_id: "c1", active_turn: active, revision: "r1" });
  throw new Error(`夹具没有定义请求：${url}`);
};

window.chatFixture = {
  ready: false,
  push(count) {
    const rows: WorkspaceEvent[] = [];
    for (let i = 0; i < count; i++) {
      const addition = ` 新片段${part}。\n`;
      text += addition;
      rows.push(event({ type: "assistant_block_delta", turn_id: "t_live", message_id: "m_live", block_id: "m_live:thinking:0", kind: "thinking", part: part++, text: addition }));
    }
    emit(rows);
  },
  finish() {
    active = null;
    emit([
      event({ type: "assistant_block_finished", turn_id: "t_live", message_id: "m_live", block_id: "m_live:thinking:0", kind: "thinking", text, last_part: part - 1, completion: "completed" }),
      event({ type: "assistant_finished", turn_id: "t_live", message_id: "m_live", completion: "completed" }),
      event({ type: "run_finished", turn_id: "t_live", status: "completed", recording: "no_change" }),
    ]);
    output.close();
  },
  text: () => text,
  reset: () => useConversationsStore.getState().reset(),
};
useModelsStore.setState({ selection: { connection_id: "test", model: "test", available: true, source: "automatic", reason: "", thinking_level: null, thinking_levels: [] } });
createRoot(document.getElementById("root")!).render(<ConversationPanel />);
void useConversationsStore.getState().load("audit");

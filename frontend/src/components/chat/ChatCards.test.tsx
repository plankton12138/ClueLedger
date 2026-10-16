import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { TaskDraftCard } from "./ChatCards";
import { applyChatEvent, emptyView } from "../../state/chat";
import type { ChatMessage, TaskDraftPayload } from "../../api/chat";

const draft = {
  draft_id: "d", revision: 1, task_kind: "check", instruction: "检查线索", goal: "",
  target_ids: [], base_version: "base", refs: [], basis: [], source_message_id: null,
  job_ref: null, missing: [{ code: "TASK_NO_TARGETS", where: "task.target_ids",
    why: "没有检查对象", fix: "选择要检查的对象" }],
} as unknown as TaskDraftPayload;
const message: ChatMessage = {
  message_id: "draft-message", conversation_id: "c", turn_id: null, role: "assistant",
  kind: "task_draft", text: "", refs: [], model: null, status: "completed", error: null,
  created_at: "", payload: draft,
};

describe("真实任务准备契约", () => {
  it("结构化缺失项展示原因和解决办法", () => {
    const html = renderToStaticMarkup(<TaskDraftCard message={message} abandoned={false}
      onAdjust={() => {}} onStart={() => {}} onAbandon={() => {}} />);
    expect(html).toContain("没有检查对象");
    expect(html).toContain("选择要检查的对象");
  });
  it("启动事件绑定建议并新增独立执行卡", () => {
    const view = applyChatEvent({ ...emptyView(), messages: [message] }, {
      project_id: "p", conversation_id: "c", turn_id: null, sequence: 2,
      event: { type: "task.linked", draft_id: "d", job_ref: "j", task_kind: "check",
        model: "m", message_id: "execution-message" },
    });
    expect((view.messages[0].payload as TaskDraftPayload).job_ref).toBe("j");
    expect(view.messages[1].kind).toBe("task_execution");
  });
});

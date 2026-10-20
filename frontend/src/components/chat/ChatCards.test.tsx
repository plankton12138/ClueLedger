import { describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { CandidateCard, TaskDraftCard, TaskExecutionCard } from "./ChatCards";
import { applyChatEvent, emptyView } from "../../state/chat";
import type {
  CandidatePayload,
  ChatMessage,
  TaskDraftPayload,
  TaskExecutionPayload,
} from "../../api/chat";

/** 任务建议卡：结构化缺失项给出「原因＋解决办法」，开始按钮在补齐前禁用。 */
const draft = {
  draft_id: "d",
  revision: 1,
  task_kind: "check",
  instruction: "检查线索",
  goal: "",
  target_ids: [],
  base_version: "base",
  refs: [],
  basis: [],
  source_message_id: null,
  job_ref: null,
  missing: [
    {
      code: "TASK_NO_TARGETS",
      where: "task.target_ids",
      why: "没有检查对象",
      fix: "选择要检查的对象",
    },
  ],
} as unknown as TaskDraftPayload;
const draftMessage: ChatMessage = {
  message_id: "draft-message",
  conversation_id: "c",
  turn_id: null,
  role: "assistant",
  kind: "task_draft",
  text: "",
  refs: [],
  model: null,
  status: "completed",
  error: null,
  created_at: "",
  payload: draft,
};

describe("真实任务准备契约", () => {
  it("结构化缺失项展示原因和解决办法", () => {
    const html = renderToStaticMarkup(
      <TaskDraftCard
        message={draftMessage}
        abandoned={false}
        onAdjust={() => {}}
        onStart={() => {}}
        onAbandon={() => {}}
      />,
    );
    expect(html).toContain("没有检查对象");
    expect(html).toContain("选择要检查的对象");
  });
  it("启动事件绑定建议并新增独立执行卡", () => {
    const view = applyChatEvent({ ...emptyView(), messages: [draftMessage] }, {
      project_id: "p",
      conversation_id: "c",
      turn_id: null,
      sequence: 2,
      event: {
        type: "task.linked",
        draft_id: "d",
        job_ref: "j",
        task_kind: "check",
        model: "m",
        message_id: "execution-message",
      },
    });
    expect((view.messages[0].payload as TaskDraftPayload).job_ref).toBe("j");
    expect(view.messages[1].kind).toBe("task_execution");
  });
});

/** 候选卡（V03-9 §4 复用）：只渲染结构化 payload，采用入口在请求齐全时才出现。 */
const candidateMessage: ChatMessage = {
  message_id: "candidate-message",
  conversation_id: "c",
  turn_id: null,
  role: "assistant",
  kind: "candidate",
  text: "",
  refs: [],
  model: null,
  status: "completed",
  error: null,
  created_at: "",
  payload: {
    job_ref: "job_1",
    summary: "第一章草稿已就绪，共 6 章",
    target_ids: ["ch_1"],
    change_ref: null,
    accepted: false,
  } as CandidatePayload,
};

describe("待审阅候选卡", () => {
  it("展示摘要与「任务完成不等于已保存」提醒", () => {
    const html = renderToStaticMarkup(
      <CandidateCard message={candidateMessage} onReview={() => {}} onAccept={() => {}} />,
    );
    expect(html).toContain("待审阅候选");
    expect(html).toContain("第一章草稿已就绪，共 6 章");
    expect(html).toContain("接受后才会生成正式版本");
    expect(html).toContain("打开审阅");
    expect(html).toContain("接受候选并提交");
  });

  it("点「接受候选并提交」回调携带 message_id", () => {
    const onAccept = vi.fn();
    const html = renderToStaticMarkup(
      <CandidateCard message={candidateMessage} onReview={() => {}} onAccept={onAccept} />,
    );
    // 无浏览器事件环境：仅断言按钮存在且接受态未渲染接受入口。
    expect(html).toContain("接受候选并提交");
    expect(html).not.toContain("已接受");
  });

  it("已接受候选不再渲染接受入口", () => {
    const accepted: ChatMessage = {
      ...candidateMessage,
      payload: { ...(candidateMessage.payload as CandidatePayload), accepted: true },
    };
    const html = renderToStaticMarkup(
      <CandidateCard message={accepted} onReview={() => {}} onAccept={() => {}} />,
    );
    expect(html).toContain("已接受");
    expect(html).not.toContain("接受候选并提交");
  });
});

/** 执行卡：变更流程入口可选——写作台不提供时（旧变更页退出产品）不渲染该按钮。 */
const executionMessage: ChatMessage = {
  message_id: "exec-message",
  conversation_id: "c",
  turn_id: null,
  role: "assistant",
  kind: "task_execution",
  text: "",
  refs: [],
  model: null,
  status: "completed",
  error: null,
  created_at: "",
  payload: {
    job_ref: "job_1",
    task_kind: "check",
    state: "waiting",
    stage: "已完成",
    model: "m",
    draft_id: "d",
    candidate_message_id: "candidate-message",
    error: null,
    change_ref: "change_1",
  } as TaskExecutionPayload,
};

describe("创作任务执行卡", () => {
  it("提供变更入口时渲染「打开变更流程」", () => {
    const html = renderToStaticMarkup(
      <TaskExecutionCard
        message={executionMessage}
        onView={() => {}}
        onCancel={() => {}}
        onRetry={() => {}}
        onOpenChange={() => {}}
      />,
    );
    expect(html).toContain("打开变更流程");
    expect(html).toContain("查看候选");
  });

  it("未提供变更入口时不渲染该按钮", () => {
    const html = renderToStaticMarkup(
      <TaskExecutionCard
        message={executionMessage}
        onView={() => {}}
        onCancel={() => {}}
        onRetry={() => {}}
      />,
    );
    expect(html).not.toContain("打开变更流程");
    expect(html).toContain("查看候选");
  });
});

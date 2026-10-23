import { describe, expect, it } from "vitest";
import type { ActivityEvent } from "../../api/activity";
import { actionCounts, buildActivity, formatDuration, hasLegacyEvents } from "./activityRows";

let sequence = 0;

function ev(extra: Partial<ActivityEvent>): ActivityEvent {
  sequence += 1;
  return {
    id: sequence,
    event_id: `e${sequence}`,
    at: `2026-10-23T10:00:${String(sequence).padStart(2, "0")}Z`,
    conversation_id: "c1",
    turn_id: "t1",
    type: "tool_started",
    category: "tool",
    status: "started",
    action: "tool",
    phase: "started",
    title: "读取",
    subject: "",
    note: "",
    pair_key: `tool:c${sequence}`,
    summary: "",
    duration_ms: null,
    ...extra,
  };
}

describe("活动记录投影", () => {
  it("同一调用的开始与结束合成一行，保留开始时间与耗时", () => {
    const bands = buildActivity([
      ev({ type: "turn_started", action: "conversation", title: "收到要求", subject: "写第一章", note: "deepseek-flash", pair_key: "turn:t1" }),
      ev({ type: "tool_started", pair_key: "tool:c1", subject: "正文/0001.md" }),
      ev({ type: "tool_finished", phase: "completed", pair_key: "tool:c1", subject: "正文/0001.md", note: "1,170 字 · 0.2 秒", duration_ms: 200 }),
      ev({ type: "run_finished", action: "conversation", phase: "completed", title: "本轮结束", note: "1 个文件 · 已提交", pair_key: "turn:t1" }),
    ]);
    expect(bands).toHaveLength(1);
    expect(bands[0]).toMatchObject({ label: "写作", status: "completed", note: "1 个文件 · 已提交" });
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({
      action: "tool", phase: "completed", title: "读取", subject: "正文/0001.md", duration_ms: 200,
    });
    expect(bands[0]!.rows[0]!.events).toHaveLength(2);
  });

  it("写入工具行由文件变化接管，显示路径与大小", () => {
    const bands = buildActivity([
      ev({ type: "tool_started", title: "写入", pair_key: "tool:c9" }),
      ev({ type: "tool_finished", phase: "completed", title: "写入", pair_key: "tool:c9", duration_ms: 40 }),
      ev({
        type: "file_changed", category: "file", action: "file", phase: "completed", title: "写入文件",
        subject: "设定集/真相.md", note: "10.4 KB", pair_key: "tool:c9",
      }),
    ]);
    const row = bands[0]!.rows[0]!;
    expect(row).toMatchObject({ action: "file", title: "写入文件", subject: "设定集/真相.md", note: "10.4 KB" });
    expect(row.events.map(item => item.type)).toEqual(["tool_started", "tool_finished", "file_changed"]);
  });

  it("同一任务内连续三个以上的同名文件动作折成一行", () => {
    const writes = ["甲", "乙", "丙", "丁"].map((name, index) =>
      ev({
        type: "file_changed", category: "file", action: "file", phase: "completed",
        title: "新建文件", subject: `设定集/角色小传/${name}.md`, note: "1.0 KB",
        pair_key: `tool:w${index}`, duration_ms: 100,
      }),
    );
    const bands = buildActivity(writes);
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({ count: 4, subject: "4 个文件", duration_ms: 400 });
    expect(bands[0]!.rows[0]!.events).toHaveLength(4);
  });

  it("同一文件被反复读取时按次数合并，不丢失对象", () => {
    const reads = Array.from({ length: 5 }, () =>
      ev({
        type: "tool_finished", phase: "completed", title: "读取",
        subject: "正文/0001 六点的白鸦馆.md", note: "456 字", pair_key: `tool:r${sequence + 1}`,
        duration_ms: 10,
      }),
    );
    const bands = buildActivity(reads);
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({
      action: "tool", count: 5, subject: "正文/0001 六点的白鸦馆.md", duration_ms: 50,
    });
  });

  it("两条同名文件动作也合并，展开仍可逐条查看", () => {
    const bands = buildActivity([
      ev({ type: "file_changed", category: "file", action: "file", phase: "completed", title: "写入文件", subject: "a.md" }),
      ev({ type: "file_changed", category: "file", action: "file", phase: "completed", title: "写入文件", subject: "b.md" }),
    ]);
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({ count: 2, subject: "2 个文件" });
  });

  it("资料任务单独成组，模型请求与回复合成一行", () => {
    const bands = buildActivity([
      ev({
        type: "turn_started", action: "materials", title: "开始整理", subject: "线索与伏笔 · 第 1 章",
        note: "deepseek-flash", pair_key: "turn:t2",
      }),
      ev({ type: "model_request_started", action: "model", title: "请求模型", note: "资料整理", pair_key: "model:op1" }),
      ev({ type: "model_request_finished", action: "model", phase: "completed", title: "模型返回", note: "输出 1,204 tokens · 12.0 秒", pair_key: "model:op1", duration_ms: 12000 }),
      ev({ type: "assistant_started", action: "model", title: "模型回复", pair_key: "model:op1" }),
    ]);
    expect(bands[0]).toMatchObject({ label: "资料整理", subject: "线索与伏笔 · 第 1 章" });
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({
      action: "model", phase: "completed", title: "模型返回", note: "输出 1,204 tokens · 12.0 秒",
    });
  });

  it("任务仍在运行时，只有开始事件的行显示进行中", () => {
    const bands = buildActivity([
      ev({ type: "turn_started", action: "conversation", title: "收到要求", pair_key: "turn:t1" }),
      ev({ type: "tool_started", pair_key: "tool:c5" }),
    ]);
    expect(bands[0]!.status).toBe("running");
    expect(bands[0]!.rows[0]!.phase).toBe("running");
  });

  it("开始事件不在已载入范围内的任务标记为未完整载入", () => {
    const bands = buildActivity([
      ev({
        type: "run_finished", action: "conversation", phase: "completed", title: "本轮结束",
        pair_key: "turn:t9",
      }),
    ]);
    expect(bands[0]!.complete).toBe(false);
    expect(bands[0]!.status).toBe("completed");
    expect(bands[0]!.rows).toHaveLength(0);
  });

  it("没有开始也没有终态的残余事件按未完整载入处理", () => {
    const bands = buildActivity([ev({ type: "tool_started", pair_key: "tool:c6" })]);
    expect(bands[0]!.complete).toBe(false);
    expect(bands[0]!.status).toBe("unloaded");
    expect(bands[0]!.rows[0]!.phase).toBe("started");
  });

  it("动作分布按合并后的行计数，并按固定顺序排列", () => {
    const bands = buildActivity([
      ev({ type: "tool_started", pair_key: "tool:a", subject: "a.md" }),
      ev({ type: "tool_started", pair_key: "tool:b", subject: "b.md" }),
      ev({ type: "file_changed", category: "file", action: "file", phase: "completed", title: "写入文件", pair_key: "tool:c" }),
    ]);
    expect(actionCounts(bands)).toEqual([{ action: "tool", count: 2 }, { action: "file", count: 1 }]);
  });

  it("作品操作由结束事件收口，不一直显示进行中", () => {
    const bands = buildActivity([
      ev({ type: "operation_started", turn_id: "", action: "operation", title: "补记版本", pair_key: "operation:o1", operation_id: "o1" }),
      ev({
        type: "operation_finished", turn_id: "", action: "operation", title: "补记版本", phase: "completed",
        pair_key: "operation:o1", operation_id: "o1", note: "2.0 秒", duration_ms: 2000,
      }),
    ]);
    expect(bands[0]).toMatchObject({ label: "作品操作", status: "completed", note: "2.0 秒" });
    expect(bands[0]!.rows).toHaveLength(1);
    expect(bands[0]!.rows[0]).toMatchObject({ phase: "completed", duration_ms: 2000 });
  });

  it("动作分布最多列六类，其余并成其他", () => {
    const actions = ["conversation", "model", "tool", "file", "materials", "version", "operation", "context"];
    const bands = buildActivity(actions.map((action, index) =>
      ev({ type: "tool_started", action, pair_key: `tool:${index}` })));
    const counts = actionCounts(bands);
    expect(counts.map(item => item.action)).toEqual([
      "conversation", "model", "tool", "file", "materials", "version", "other",
    ]);
    expect(counts.at(-1)!.count).toBe(2);
  });

  it("旧索引只有摘要时也能给出可读的一行，并标记需要重建", () => {
    const legacy = {
      ...ev({ type: "tool_finished", status: "completed", summary: "工具结果 · 读取 正文/0001.md" }),
      action: "", phase: "", title: "", subject: "", note: "", pair_key: "",
    } as unknown as ActivityEvent;
    const bands = buildActivity([legacy]);
    expect(bands[0]!.rows[0]).toMatchObject({
      action: "tool", title: "工具结果", subject: "读取 正文/0001.md", phase: "completed",
    });
    expect(hasLegacyEvents([legacy])).toBe(true);
    expect(hasLegacyEvents([ev({})])).toBe(false);
  });

  it("耗时按毫秒、秒、分显示", () => {
    expect(formatDuration(120)).toBe("120 毫秒");
    expect(formatDuration(12_400)).toBe("12.4 秒");
    expect(formatDuration(455_000)).toBe("7 分 35 秒");
    expect(formatDuration(null)).toBe("");
  });
});

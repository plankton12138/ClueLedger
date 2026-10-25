/**
 * 会话归并（V05-2B）。
 *
 * 固定事件序列验证三件事：同一条回复持续更新而不是新增气泡；重复、缺批与最终校正
 * 各按约定处理；实时接收与从头回放得到相同结果。
 */

import { describe, expect, it } from "vitest";

import type { WorkspaceEvent } from "../api/workspace";
import {
  appendEvent,
  buildTurns,
  formatDuration,
  materialComplete,
  materialReadChars,
  NeedsConversationReload,
  orderedBlocks,
  phaseLabel,
  publicText,
  hasTaskCard,
  taskCardFacts,
  type ChatTurn,
} from "./chatMessages";

let seq = 0;

function next(extra: Record<string, unknown>): WorkspaceEvent {
  seq += 1;
  return { seq, at: "2026-10-21T12:00:00+08:00", ...extra } as WorkspaceEvent;
}

function reset(): void {
  seq = 0;
}

function turnStarted(id = "t1", text = "写第一章"): WorkspaceEvent {
  return next({ type: "turn_started", turn_id: id, text });
}

function started(messageId: string, turnId = "t1"): WorkspaceEvent {
  return next({ type: "assistant_started", turn_id: turnId, message_id: messageId });
}

function blockStarted(
  blockId: string,
  kind: "text" | "thinking" = "text",
  order = 0,
  messageId = "m1",
): WorkspaceEvent {
  return next({
    type: "assistant_block_started",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind,
    order,
  });
}

function delta(blockId: string, part: number, text: string, messageId = "m1"): WorkspaceEvent {
  return next({
    type: "assistant_block_delta",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind: "text",
    part,
    text,
  });
}

function blockFinished(
  blockId: string,
  text: string,
  lastPart: number,
  messageId = "m1",
  completion = "completed",
): WorkspaceEvent {
  return next({
    type: "assistant_block_finished",
    turn_id: "t1",
    message_id: messageId,
    block_id: blockId,
    kind: "text",
    text,
    last_part: lastPart,
    completion,
    duration_ms: 120,
  });
}

function fold(events: WorkspaceEvent[]): ChatTurn[] {
  return events.reduce<ChatTurn[]>((turns, event) => appendEvent(turns, event), []);
}

describe("归并：同一条回复持续更新", () => {
  it("多次增量更新同一条消息，不新增气泡", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "旅馆的灯"),
      delta("m1:text:0", 1, "熄灭了。"),
    ]);

    expect(turns).toHaveLength(1);
    expect(turns[0]!.items).toHaveLength(1);
    const item = turns[0]!.items[0]!;
    expect(item.kind).toBe("message");
    if (item.kind !== "message") throw new Error("应为消息");
    expect(item.message.blocks).toHaveLength(1);
    expect(item.message.blocks[0]!.text).toBe("旅馆的灯熄灭了。");
    expect(item.message.blocks[0]!.nextPart).toBe(2);
    // 流式期间没有正式公开文本：复制取当前已收到的文字。
    expect(publicText(item.message)).toBe("旅馆的灯熄灭了。");
  });

  it("正式公开文本更新同一条消息，不生成第二个气泡", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "草稿多半句"),
      blockFinished("m1:text:0", "定稿全文。", 0),
      next({
        type: "assistant_message",
        turn_id: "t1",
        message_id: "m1",
        text: "定稿全文。",
        completion: "completed",
      }),
      next({
        type: "assistant_finished",
        turn_id: "t1",
        message_id: "m1",
        completion: "completed",
        stop_reason: "stop",
        duration_ms: 900,
      }),
    ]);

    expect(turns[0]!.items).toHaveLength(1);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(publicText(item.message)).toBe("定稿全文。");
    expect(item.message.completion).toBe("completed");
    expect(item.message.durationMs).toBe(900);
    // 完整结束值覆盖增量，不拼接两次。
    expect(item.message.blocks[0]!.text).toBe("定稿全文。");
  });

  it("思考与文字按内容位置穿插，思考不进入公开文本", () => {
    reset();
    const turns = fold([
      turnStarted(),
      started("m1"),
      blockStarted("m1:thinking:0", "thinking", 0),
      delta("m1:thinking:0", 0, "先看时间线。"),
      blockStarted("m1:text:1", "text", 1),
      delta("m1:text:1", 0, "时间线是雪夜。"),
    ]);

    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(orderedBlocks(item.message).map((block) => block.kind)).toEqual(["thinking", "text"]);
    expect(publicText(item.message)).toBe("时间线是雪夜。");
  });

  it("未受影响的轮次保持引用，界面可以跳过重绘", () => {
    reset();
    const first = fold([turnStarted("t1", "第一轮"), started("m1", "t1")]);
    const turns = [first[0]!, ...fold([turnStarted("t2", "第二轮"), started("m2", "t2")])];

    const updated = appendEvent(turns, {
      seq: 900,
      at: "2026-10-21T12:00:01+08:00",
      type: "assistant_block_started",
      turn_id: "t2",
      message_id: "m2",
      block_id: "m2:text:0",
      kind: "text",
      order: 0,
    });

    expect(updated[0]).toBe(turns[0]);
    expect(updated[1]).not.toBe(turns[1]);
  });
});

describe("归并：重复与缺口", () => {
  it("重复的增量与重复的整条事件都不改变结果", () => {
    reset();
    const base = [turnStarted(), started("m1"), blockStarted("m1:text:0"), delta("m1:text:0", 0, "一")];
    const turns = fold(base);
    const again = appendEvent(turns, base[3]!);
    expect(again).toBe(turns);
    // 重复的 part 不重复追加文字。
    const repeated = appendEvent(turns, { ...base[3]!, seq: 99 });
    expect(repeated).toBe(turns);
  });

  it("缺号时要求回读，而不是猜缺失文字", () => {
    reset();
    const turns = fold([turnStarted(), started("m1"), blockStarted("m1:text:0"), delta("m1:text:0", 0, "一")]);
    expect(() => appendEvent(turns, delta("m1:text:0", 2, "三"))).toThrow(NeedsConversationReload);
  });

  it("回读仍有缺口时标记记录不完整，其余内容照常展示", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "一"),
      delta("m1:text:0", 2, "三"),
      blockFinished("m1:text:0", "一三", 2),
    ]);
    expect(turns[0]!.incomplete).toBe(true);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(publicText(item.message)).toBe("一三");
  });

  it("实时接收与从头回放得到相同展示", () => {
    reset();
    const events = [
      turnStarted(),
      next({ type: "run_phase", turn_id: "t1", phase: "model" }),
      started("m1"),
      blockStarted("m1:text:0"),
      delta("m1:text:0", 0, "先写下"),
      blockFinished("m1:text:0", "先写下这一段。", 0),
      next({ type: "assistant_message", turn_id: "t1", message_id: "m1", text: "先写下这一段。" }),
      next({ type: "assistant_finished", turn_id: "t1", message_id: "m1", completion: "completed" }),
    ];
    const live = fold(events);
    const replay = buildTurns(events);
    expect(replay).toEqual(live);
    expect(replay).toHaveLength(1);
  });
});

describe("归并：工具与结果", () => {
  it("同名工具按调用 ID 分别配对", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "tool_started", turn_id: "t1", tool_call_id: "c1", tool: "read_file", args: { path: "正文/1.md" } }),
      next({ type: "tool_started", turn_id: "t1", tool_call_id: "c2", tool: "read_file", args: { path: "设定集/线索.md" } }),
      next({
        type: "tool_finished",
        turn_id: "t1",
        tool_call_id: "c2",
        tool: "read_file",
        ok: true,
        summary: "读到了",
        duration_ms: 30,
      }),
      next({ type: "tool_finished", turn_id: "t1", tool_call_id: "c1", tool: "read_file", ok: false, summary: "没有这个文件" }),
    ]);

    const tools = turns[0]!.items.filter((item) => item.kind === "tool");
    expect(tools).toHaveLength(2);
    if (tools[0]!.kind !== "tool" || tools[1]!.kind !== "tool") throw new Error("应为工具");
    expect(tools[0]!.tool.args.path).toBe("正文/1.md");
    expect(tools[0]!.tool.ok).toBe(false);
    expect(tools[1]!.tool.args.path).toBe("设定集/线索.md");
    expect(tools[1]!.tool.ok).toBe(true);
    expect(tools[1]!.tool.durationMs).toBe(30);
  });

  it("旧记录没有调用 ID 时按同名未完成配对", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "tool_started", turn_id: "t1", tool: "read_file", args: { path: "正文/1.md" } }),
      next({ type: "tool_finished", turn_id: "t1", tool: "read_file", args: { path: "正文/1.md" }, ok: true }),
    ]);
    const tools = turns[0]!.items.filter((item) => item.kind === "tool");
    expect(tools).toHaveLength(1);
  });

  it("旧记录的公开回复单独展示", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "assistant_message", turn_id: "t1", text: "旧版本的回复。" }),
    ]);
    const item = turns[0]!.items[0]!;
    if (item.kind !== "message") throw new Error("应为消息");
    expect(item.message.id).toBe("legacy:2");
    expect(publicText(item.message)).toBe("旧版本的回复。");
  });

  it("整轮结果与错误各自挂在轮次上", () => {
    reset();
    const turns = fold([
      turnStarted(),
      next({ type: "error", turn_id: "t1", code: "MODEL_CALL_FAILED", message: "502" }),
      next({
        type: "run_finished",
        turn_id: "t1",
        status: "failed",
        recording: "no_change",
        changed_paths: [],
        commit: null,
        note: "",
      }),
    ]);
    expect(turns[0]!.errors[0]!.code).toBe("MODEL_CALL_FAILED");
    expect(turns[0]!.result?.status).toBe("failed");
  });
});

describe("读取辅助", () => {
  it("阶段与耗时的文字不伪造进度", () => {
    expect(phaseLabel("model")).toBe("等待模型响应");
    expect(phaseLabel("tool")).toBe("正在执行工具");
    expect(phaseLabel(null)).toBe("等待模型响应");
    expect(formatDuration(320)).toBe("320 毫秒");
    expect(formatDuration(0)).toBe("<1 毫秒");
    expect(formatDuration(1500)).toBe("1.5 秒");
    expect(formatDuration(null)).toBe("");
  });
});

describe("本轮范围", () => {
  const scope = (version: number, overrides: Record<string, unknown> = {}) => ({
    type: "task_scope_set" as const,
    turn_id: "t1",
    version,
    mode: "edit" as const,
    summary: "改写第三章",
    authorization: "只改第三章",
    authorization_seq: 1,
    carried_from_seq: null,
    grants: [{ action: "update" as const, path: "正文/0003 书房.md" }],
    denied_paths: [],
    required_references: [],
    extendable: false,
    extend_actions: [],
    ...overrides,
  });

  it("登记与追加按版本前进，迟到的旧版本不覆盖", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      next(scope(1)),
      next({
        ...scope(2, {
          type: "task_scope_extended",
          grants: [
            { action: "update", path: "正文/0003 书房.md" },
            { action: "update", path: "正文/0004 书房外.md" },
          ],
        }),
      }),
      next(scope(1)),
    ]);

    const current = turns[0]!.scope!;
    expect(current.version).toBe(2);
    expect(current.grants).toHaveLength(2);
  });

  it("只读范围也记录，用于说明这一轮不写文件", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      next(scope(1, { mode: "read_only", grants: [], summary: "检查第三章" })),
    ]);

    const current = turns[0]!.scope!;
    expect(current.mode).toBe("read_only");
    expect(current.grants).toEqual([]);
  });

  it("没有登记范围的轮次不带范围", () => {
    reset();
    const turns = buildTurns([turnStarted()]);
    expect(turns[0]!.scope).toBeNull();
  });
});

describe("检查报告", () => {
  it("报告归并到本轮，区分有依据与疑点", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      next({
        type: "review_finished",
        turn_id: "t1",
        report_id: "r_1",
        status: "findings",
        targets: ["正文/0003 书房.md"],
        findings: [
          {
            kind: "continuity",
            certainty: "suspected",
            description: "时间线可能对不上",
            suggestion: "补一句交代",
            evidence: [
              {
                path: "正文/0003 书房.md",
                digest: "d1",
                start: 0,
                end: 4,
                quote: "书房",
              },
            ],
          },
        ],
        uncovered: [],
      }),
    ]);

    const review = turns[0]!.review!;
    expect(review.status).toBe("findings");
    expect(review.reportId).toBe("r_1");
    expect(review.findings[0]!.certainty).toBe("suspected");
  });

  it("未完成的检查写明没覆盖的部分，不冒充通过", () => {
    reset();
    const turns = buildTurns([
      turnStarted(),
      next({
        type: "review_finished",
        turn_id: "t1",
        report_id: null,
        status: "incomplete",
        targets: ["正文/0003 书房.md"],
        findings: [],
        uncovered: ["这一轮没有生成检查报告"],
      }),
    ]);

    const review = turns[0]!.review!;
    expect(review.status).toBe("incomplete");
    expect(review.uncovered).toEqual(["这一轮没有生成检查报告"]);
    expect(review.reportId).toBeNull();
  });
});

describe("本轮材料", () => {
  it("预读与读取区间合并成同一份材料，重叠不重复累计", () => {
    reset();
    const prepared = next({
      type: "context_prepared",
      turn_id: "t1",
      selected_path: "正文/0003 书房.md",
      references: [{ path: "正文/0001 参考.md", digest: "d1" }],
      provided: [{ path: "正文/0001 参考.md", start: 0, end: 100, length: 400 }],
    });
    const read = next({
      type: "material_read",
      turn_id: "t1",
      path: "正文/0001 参考.md",
      digest: "d1",
      start_char: 80,
      end_char: 400,
      start_line: 3,
      end_line: 12,
      total_chars: 400,
    });

    const turns = buildTurns([turnStarted(), prepared, read]);

    const material = turns[0]!.materials[0]!;
    expect(material.path).toBe("正文/0001 参考.md");
    expect(material.required).toBe(true);
    expect(material.length).toBe(400);
    // 0-100 与 80-400 合并后是 400 个字符，不是 420。
    expect(materialReadChars(material)).toBe(400);
    expect(materialComplete(material)).toBe(true);
  });

  it("版本变化后不拼接旧区间", () => {
    reset();
    const first = next({
      type: "material_read",
      turn_id: "t1",
      path: "正文/0001 参考.md",
      digest: "d1",
      start_char: 0,
      end_char: 50,
      start_line: 1,
      end_line: 2,
      total_chars: 100,
    });
    const second = next({
      type: "material_read",
      turn_id: "t1",
      path: "正文/0001 参考.md",
      digest: "d2",
      start_char: 0,
      end_char: 10,
      start_line: 1,
      end_line: 1,
      total_chars: 120,
    });

    const turns = buildTurns([turnStarted(), first, second]);

    const material = turns[0]!.materials[0]!;
    expect(material.digest).toBe("d2");
    expect(materialReadChars(material)).toBe(10);
    expect(materialComplete(material)).toBe(false);
  });

  it("登记的参考即使还没读也出现在材料里", () => {
    reset();
    const registered = next({
      type: "reference_registered",
      turn_id: "t1",
      references: [{ path: "设定集/角色小传/林远.md", digest: "d9", kind: "discovered", required: false }],
    });

    const turns = buildTurns([turnStarted(), registered]);

    const material = turns[0]!.materials[0]!;
    expect(material.required).toBe(false);
    expect(material.spans).toEqual([]);
    expect(materialReadChars(material)).toBe(0);
  });
});

describe("本轮注入", () => {
  it("记录分区规模、跳过原因与未装入条目，不含注入正文", () => {
    reset();
    const injected = next({
      type: "state_injected",
      turn_id: "t1",
      profile: "writer",
      parts: [
        { key: "instructions", chars: 1200 },
        { key: "catalog", chars: 300 },
        {
          key: "index",
          chars: 900,
          excluded: [
            {
              id: "chapter-index:正文/0002.md",
              reason: "budget",
              chars: 300,
              location: "read_file 正文/0002.md",
            },
          ],
        },
      ],
      skipped: [{ key: "clues", reason: "budget", chars: 120 }],
      budget: { limit: 20000, used: 2400 },
    });

    const turns = buildTurns([turnStarted(), injected]);

    const injection = turns[0]!.injection!;
    expect(injection.profile).toBe("writer");
    expect(injection.parts.map((part) => part.key)).toEqual(["instructions", "catalog", "index"]);
    expect(injection.parts[2]!.excluded).toEqual([
      {
        id: "chapter-index:正文/0002.md",
        reason: "budget",
        chars: 300,
        location: "read_file 正文/0002.md",
      },
    ]);
    expect(injection.skipped).toEqual([{ key: "clues", reason: "budget", chars: 120 }]);
    expect(injection.used).toBe(2400);
    expect(injection.limit).toBe(20000);
  });
});

describe("任务卡", () => {
  it("把授权、未读必读材料与执行事实收在同一份投影里", () => {
    reset();
    const events = [
      turnStarted(),
      next({
        type: "task_scope_set",
        turn_id: "t1",
        version: 1,
        mode: "edit",
        summary: "改写第一章",
        authorization: "写第一章",
        authorization_seq: 1,
        carried_from_seq: null,
        grants: [{ action: "update", path: "正文/0001 雨夜.md" }],
        denied_paths: ["设定集/真相.md"],
        required_references: ["设定集/线索.md"],
        extendable: false,
        extend_actions: [],
      }),
      next({
        type: "reference_registered",
        turn_id: "t1",
        references: [
          { path: "设定集/线索.md", digest: "d1", kind: "file", required: true },
        ],
      }),
      next({
        type: "material_read",
        turn_id: "t1",
        path: "设定集/线索.md",
        digest: "d1",
        start_char: 0,
        end_char: 3,
        start_line: 1,
        end_line: 1,
        total_chars: 10,
      }),
      next({
        type: "run_finished",
        turn_id: "t1",
        status: "completed",
        recording: "committed",
        changed_paths: ["正文/0001 雨夜.md"],
        commit: "abc1234",
        note: "",
      }),
    ];

    const facts = taskCardFacts(buildTurns(events)[0]!);

    expect(hasTaskCard(facts)).toBe(true);
    expect(facts.mode).toBe("edit");
    expect(facts.authorization).toBe("写第一章");
    expect(facts.authorizationSeq).toBe(1);
    expect(facts.grants.map((grant) => grant.path)).toEqual(["正文/0001 雨夜.md"]);
    expect(facts.deniedPaths).toEqual(["设定集/真相.md"]);
    expect(facts.requiredCount).toBe(1);
    expect(facts.requiredComplete).toBe(false);
    expect(facts.requiredUnread).toEqual([{ path: "设定集/线索.md", remaining: 7 }]);
    expect(facts.status).toBe("completed");
    expect(facts.recording).toBe("committed");
    expect(facts.changedPaths).toEqual(["正文/0001 雨夜.md"]);
  });

  it("没有范围、必读材料与结果时不显示任务卡", () => {
    reset();
    const facts = taskCardFacts(buildTurns([turnStarted()])[0]!);
    expect(hasTaskCard(facts)).toBe(false);
  });
});

describe("本轮灵感引用（I3）", () => {
  it("受理事件里的固定修订进入轮次，旧事件没有该字段时为空", () => {
    reset();
    const withIdeas = next({
      type: "turn_started",
      turn_id: "t1",
      text: "按这条灵感展开",
      context: {
        idea_references: [["idea_1", "irev_1"]],
        idea_revisions: [
          { idea_id: "idea_1", revision_id: "irev_1", title: "", preview: "灯塔在退潮时才会露出第二层台阶。" },
        ],
      },
    });
    const legacy = next({ type: "turn_started", turn_id: "t2", text: "旧记录" });
    const turns = buildTurns([withIdeas, legacy]);
    expect(turns[0]?.ideas).toEqual([
      {
        idea_id: "idea_1",
        revision_id: "irev_1",
        preview: "灯塔在退潮时才会露出第二层台阶。",
      },
    ]);
    expect(turns[1]?.ideas).toEqual([]);
  });
});

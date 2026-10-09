/** AuditPage 纯函数剧本（P7 T9，D7）：修订建议模板组装。 */

import { describe, expect, it } from "vitest";
import type { AuditCheckWire } from "../api/rest";
import { buildRevisionRequest } from "./AuditPage";

const check = (over: Partial<AuditCheckWire>): AuditCheckWire => ({
  rule: "foreshadow_overdue",
  severity: "error",
  message: "伏笔「怀表」逾期未回收。",
  anchor_chapter: null,
  anchor_name: null,
  ...over,
});

describe("buildRevisionRequest", () => {
  it("模板首行 + 逐条编号（error/warn 中文标签 + 锚定章节）", () => {
    const message = buildRevisionRequest("ch03", [
      check({ rule: "timeline_conflict", severity: "error", message: "第 1 天「宴席」与「密室」重叠。", anchor_chapter: "ch01" }),
      check({ rule: "clue_planting", severity: "warn", message: "线索「手枪」揭晓先于呈现。" }),
    ]);
    expect(message).toContain("以下是第 3 章审计发现的确定性问题，请逐条核对并修订：");
    expect(message).toContain("1. [错误] timeline_conflict：第 1 天「宴席」与「密室」重叠。（问题章节：ch01）");
    expect(message).toContain("2. [警告] clue_planting：线索「手枪」揭晓先于呈现。");
  });

  it("非 chNN 章节 id 原样展示（chapterNumber 口径）", () => {
    expect(buildRevisionRequest("prologue", [check({})])).toContain("第 prologue 章");
  });
});

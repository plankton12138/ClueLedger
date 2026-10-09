/**
 * 线索公平性剧本（P7 T3，计划 D4）：D7 审计同口径——非红鲱鱼中已呈现
 * （status != planned）占比；空账本 → null；红鲱鱼不入分母。
 */

import { describe, expect, it } from "vitest";
import { clueFairness } from "./clueFairness";

function clue(name: string, status: string, isRedHerring = false): Record<string, unknown> {
  return { name, status, is_red_herring: isRedHerring };
}

describe("clueFairness", () => {
  it("非红鲱鱼中已呈现占比（红鲱鱼不入分母）", () => {
    const entities = [
      clue("怀表", "presented"),
      clue("车票", "planned"),
      clue("密信", "interpreted"),
      clue("口红印", "planned", true), // 红鲱鱼：无论什么状态都排除
    ];
    expect(clueFairness(entities)).toEqual({ presented: 2, total: 3, rate: 2 / 3 });
  });

  it("全部 planned → rate 0（合法结果，不是 null）", () => {
    expect(clueFairness([clue("a", "planned"), clue("b", "planned")])).toEqual({
      presented: 0,
      total: 2,
      rate: 0,
    });
  });

  it("空账本 → null（与 T8 审计 metrics None 口径一致）", () => {
    expect(clueFairness([])).toBeNull();
  });

  it("全为红鲱鱼 → null（无真实线索可谈公平）", () => {
    expect(clueFairness([clue("x", "presented", true)])).toBeNull();
  });

  it("status 缺失或空串按 planned 处理（未登记 ≠ 已呈现）", () => {
    const entities: Record<string, unknown>[] = [
      { name: "a", is_red_herring: false },
      { name: "b", status: "", is_red_herring: false },
      { name: "c", status: "discovered", is_red_herring: false },
    ];
    expect(clueFairness(entities)).toEqual({ presented: 1, total: 3, rate: 1 / 3 });
  });
});

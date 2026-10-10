/**
 * 生命周期视图映射纯函数（P8 T7，D7）：六态徽章配色 / 完备度速览 / n/m 进度。
 */

import { describe, expect, it } from "vitest";
import { chapterProgress, ledgerSummary, stageMeta } from "./story";

describe("stageMeta", () => {
  it("六阶段各有文案与配色", () => {
    expect(stageMeta("intake").label).toBe("构想接收");
    expect(stageMeta("bible_drafting").label).toBe("起草 Bible");
    expect(stageMeta("bible_confirmed").label).toBe("Bible 已确认");
    expect(stageMeta("writing").label).toBe("写作中");
    expect(stageMeta("revising").label).toBe("修订维护");
    expect(stageMeta("complete").label).toBe("终稿");
    // 配色类齐全：边框与文字类都存在
    for (const stage of [
      "intake",
      "bible_drafting",
      "bible_confirmed",
      "writing",
      "revising",
      "complete",
    ]) {
      expect(stageMeta(stage).className).toContain("border-");
      expect(stageMeta(stage).className).toContain("text-");
    }
  });

  it("未知阶段降级为原样展示，不崩徽章", () => {
    expect(stageMeta("archived")).toEqual({
      label: "archived",
      className: "border-line text-paper/60",
    });
  });
});

describe("ledgerSummary", () => {
  it("五账本计数一行带过，手法带锁定态", () => {
    expect(
      ledgerSummary({
        characters: 2,
        clues: 6,
        foreshadows: 8,
        methods: 1,
        methods_locked: 1,
        outlines: 3,
      }),
    ).toBe("角色 2 · 线索 6 · 伏笔 8 · 手法 1/1 锁定 · 章纲 3");
  });
});

describe("chapterProgress", () => {
  it("有章纲时报 n/m", () => {
    expect(chapterProgress({ written: 2, planned: 7 })).toBe("章 2/7");
  });

  it("无章纲只报已写数，避免 0/0 误导", () => {
    expect(chapterProgress({ written: 0, planned: 0 })).toBe("章 0");
    expect(chapterProgress({ written: 3, planned: 0 })).toBe("章 3");
  });
});

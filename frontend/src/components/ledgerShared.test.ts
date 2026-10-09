/**
 * 账本共享件纯函数剧本（P7 T5，计划 D5）：筛选排序 / 章节号解析。
 * RiskCell 渲染走浏览器人工验收（项目测试基建为 node 环境纯函数剧本）。
 */

import { describe, expect, it } from "vitest";
import { ledgerRows, parseChapterNumber, type LedgerQuery } from "./ledgerShared";

const riskByName = new Map([
  ["雪夜密室", "overdue"],
  ["钟摆停点", "near"],
  ["缺失的手稿", "normal"],
]);

const foreshadows = [
  { name: "钟摆停点", plant_chapter: "ch03", expected_resolve_chapter: "ch05", status: "planted" },
  { name: "雪夜密室", plant_chapter: "ch01", expected_resolve_chapter: "ch04", status: "planted" },
  { name: "缺失的手稿", plant_chapter: "ch02", expected_resolve_chapter: "ch06", status: "resolved" },
];

describe("parseChapterNumber", () => {
  it("chNN（大小写/空白容忍）→ 章号；非 chNN 形态返回 null", () => {
    expect(parseChapterNumber("ch03")).toBe(3);
    expect(parseChapterNumber(" CH12 ")).toBe(12);
    expect(parseChapterNumber("序章")).toBeNull();
    expect(parseChapterNumber("")).toBeNull();
  });
});

describe("ledgerRows", () => {
  const q = (over: Partial<LedgerQuery> = {}) => ({
    search: "",
    status: "all",
    risk: "all",
    sortKey: "plant" as const,
    ...over,
  });

  it("search 全字段包含（大小写不敏感），其他账本无状态/风险/排序逻辑", () => {
    const characters: Record<string, unknown>[] = [
      { name: "Hastings", identity: "叙述者" },
      { name: "波洛", identity: "侦探" },
    ];
    expect(ledgerRows("character", characters, q({ search: "波" }), riskByName)).toHaveLength(1);
    expect(ledgerRows("character", characters, q({ search: "hastings" }), riskByName)[0]?.["name"]).toBe(
      "Hastings",
    );
  });

  it("伏笔按埋设章升序（默认排序键）", () => {
    const rows = ledgerRows("foreshadow", foreshadows, q(), riskByName);
    expect(rows.map((r) => r["plant_chapter"])).toEqual(["ch01", "ch02", "ch03"]);
  });

  it("排序键切换为预计回收章", () => {
    const rows = ledgerRows("foreshadow", foreshadows, q({ sortKey: "resolve" }), riskByName);
    expect(rows.map((r) => r["expected_resolve_chapter"])).toEqual(["ch04", "ch05", "ch06"]);
  });

  it("状态与风险筛选叠加（风险来自 riskByName join）", () => {
    const rows = ledgerRows(
      "foreshadow",
      foreshadows,
      q({ status: "planted", risk: "overdue" }),
      riskByName,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0]?.["name"]).toBe("雪夜密室");
  });

  it("坏锚点排到最后，不被拖乱整体顺序", () => {
    const messy = [
      { name: "无锚", plant_chapter: "序章", expected_resolve_chapter: "" },
      { name: "有锚", plant_chapter: "ch02", expected_resolve_chapter: "ch03" },
    ];
    const rows = ledgerRows("foreshadow", messy, q(), riskByName);
    expect(rows.map((r) => r["name"])).toEqual(["有锚", "无锚"]);
  });
});

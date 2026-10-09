/**
 * 全局搜索纯函数剧本（P6 T11，D9）：文档构建 / 档位排序 / 组序与封顶 / 空查询。
 */

import { describe, expect, it } from "vitest";
import {
  buildChapterDoc,
  buildLedgerDocs,
  searchIndex,
  type SearchDoc,
} from "./search";

describe("buildChapterDoc", () => {
  it("带标题章：名称「第N章 标题」，正文可检索", () => {
    const doc = buildChapterDoc("ch01", "# 第1章 风雪夜归人\n\n雪落无声。");
    expect(doc.name).toBe("第1章 风雪夜归人");
    expect(doc.chapterId).toBe("ch01");
    expect(doc.haystack).toContain("风雪夜归人");
    expect(doc.haystack).toContain("雪落无声");
  });

  it("无标题章（正文无首行标题）：名称回落章号，正文仍可检索", () => {
    const doc = buildChapterDoc("ch03", "她推开门，屋里空无一人。");
    expect(doc.name).toBe("第3章");
    expect(doc.haystack).toContain("她推开门");
  });

  it("非 chNN 命名的章节 id 直接以 id 为名（标题解析只认「第N章」首行）", () => {
    const doc = buildChapterDoc("序章", "她推开门，屋里空无一人。");
    expect(doc.name).toBe("序章");
    expect(doc.haystack).toContain("她推开门");
  });
});

describe("buildLedgerDocs", () => {
  it("name 进展示名，字符串字段拼进检索面（数组顿号连接）", () => {
    const docs = buildLedgerDocs("character", [
      {
        name: "陆远",
        identity: "侦探",
        motivation: "查明旧友死因",
        secret: "",
        arc: "developing",
        relationships: ["林岚: 对手", "冰室: 风雪夜遇"],
      },
    ]);
    expect(docs[0]?.name).toBe("陆远");
    expect(docs[0]?.haystack).toContain("侦探");
    expect(docs[0]?.haystack).toContain("查明旧友死因");
    expect(docs[0]?.haystack).toContain("林岚: 对手、冰室: 风雪夜遇");
  });

  it("布尔字段不参与检索（是/否对关键词无意义）", () => {
    const docs = buildLedgerDocs("clue", [
      { name: "车票", content: "北上的车票", is_red_herring: true, is_genuine: false },
    ]);
    expect(docs[0]?.haystack).not.toContain("是");
  });

  it("空账本 → 空文档集", () => {
    expect(buildLedgerDocs("foreshadow", [])).toEqual([]);
  });
});

describe("searchIndex", () => {
  const docs: SearchDoc[] = [
    { kind: "chapter", chapterId: "ch01", name: "第1章 雪夜", haystack: "第1章 雪夜\n风声大作" },
    { kind: "character", name: "陆远", haystack: "陆远 侦探 查明死因" },
    { kind: "character", name: "林岚", haystack: "林岚 律师 雪夜同行" },
    { kind: "clue", name: "车票", haystack: "车票 北上的车票 雪夜" },
    { kind: "foreshadow", name: "怀表", haystack: "怀表 停摆的怀表 雪夜" },
  ];

  it("空查询 → 空结果（下拉不展示）", () => {
    expect(searchIndex(docs, "")).toEqual([]);
    expect(searchIndex(docs, "   ")).toEqual([]);
  });

  it("按组序输出：章节 → 角色 → 线索 → 伏笔", () => {
    const hits = searchIndex(docs, "雪夜");
    expect(hits.map((h) => h.doc.kind)).toEqual([
      "chapter",
      "character",
      "clue",
      "foreshadow",
    ]);
  });

  it("档位排序：精确名先于名含，名含先于字段含", () => {
    const hits = searchIndex(docs, "陆远");
    expect(hits[0]?.doc.name).toBe("陆远");
    expect(hits[0]?.tier).toBe(0);
  });

  it("大小写不敏感（英文名场景）", () => {
    const mixed = [
      { kind: "character" as const, name: "Alice", haystack: "Alice 管家" },
      { kind: "clue" as const, name: "手枪", haystack: "手枪 弹痕" },
    ];
    expect(searchIndex(mixed, "alice")[0]?.doc.name).toBe("Alice");
  });

  it("每组封顶：perGroup 截断后剩余不溢出到展示", () => {
    const many = Array.from({ length: 8 }, (_, i) => ({
      kind: "character" as const,
      name: `角色${i}`,
      haystack: `角色${i} 雪夜`,
    }));
    const hits = searchIndex([...docs, ...many], "雪夜", 3);
    expect(hits.filter((h) => h.doc.kind === "character")).toHaveLength(3);
  });

  it("命中窗口摘要在首尾加省略号（超窗时）", () => {
    const long = { kind: "clue" as const, name: "遗嘱", haystack: "遗嘱 一页泛黄的遗嘱，落款处日期被水渍洇开" };
    const hits = searchIndex([long], "水渍");
    expect(hits[0]?.snippet).toContain("…");
  });

  it("无命中 → 空结果（合法空结果，下拉显示「无匹配」）", () => {
    expect(searchIndex(docs, "不存在的关键词")).toEqual([]);
  });
});

/**
 * 角色关系解析纯函数剧本（P6 T10，D8）：冒号拆分 / 注册名单校验 / 未连线泄压。
 */

import { describe, expect, it } from "vitest";
import { buildGraphData, parseRelation } from "./relations";

describe("parseRelation", () => {
  it("冒号（中英文）均拆分出目标名与描述", () => {
    expect(parseRelation("陆远: 委托人")).toEqual({ target: "陆远", description: "委托人" });
    expect(parseRelation("林岚：竞争对手")).toEqual({
      target: "林岚",
      description: "竞争对手",
    });
  });

  it("容忍前后空白，描述可空", () => {
    expect(parseRelation("  陆远 :  委托人  ")).toEqual({
      target: "陆远",
      description: "委托人",
    });
    expect(parseRelation("陆远:")).toEqual({ target: "陆远", description: "" });
  });

  it("无冒号 / 空目标不解析", () => {
    expect(parseRelation("与陆远关系密切")).toBeNull();
    expect(parseRelation(": 描述")).toBeNull();
    expect(parseRelation("")).toBeNull();
  });
});

describe("buildGraphData", () => {
  const inputs = [
    { name: "陆远", relationships: ["林岚: 对手", "冰室: 风雪夜遇", "未登记的人: 旧识"] },
    { name: "林岚", relationships: ["陆远：旧日同窗"] },
    { name: "冰室", relationships: [] },
  ];

  it("指向已注册角色的条目成边", () => {
    const data = buildGraphData(inputs);
    expect(data.edges).toEqual([
      { source: "陆远", target: "林岚", label: "对手" },
      { source: "陆远", target: "冰室", label: "风雪夜遇" },
      { source: "林岚", target: "陆远", label: "旧日同窗" },
    ]);
    expect(data.nodes.map((n) => n.id)).toEqual(["陆远", "林岚", "冰室"]);
  });

  it("未注册目标 / 不可解析条目进 unconnected 原样保留（泄压阀）", () => {
    const data = buildGraphData(inputs);
    expect(data.unconnected).toEqual([
      { source: "陆远", entry: "未登记的人: 旧识" },
    ]);
  });

  it("空账本 → 无节点无边无未连线", () => {
    const data = buildGraphData([]);
    expect(data.nodes).toEqual([]);
    expect(data.edges).toEqual([]);
    expect(data.unconnected).toEqual([]);
  });

  it("全部指向未知角色 → 无任何边，全部未连线（空态引导数据源）", () => {
    const data = buildGraphData([{ name: "孤", relationships: ["不存在的人: 路人"] }]);
    expect(data.edges).toEqual([]);
    expect(data.unconnected).toEqual([{ source: "孤", entry: "不存在的人: 路人" }]);
  });
});

import { describe, expect, it } from "vitest";

import { groupNodes } from "./StorySurface";

function node(id: string, title: string, planned: number | null = null) {
  return {
    id,
    title,
    action: "行动",
    change: null,
    stage_id: null,
    thread_ids: [],
    clue_ids: [],
    time_note: "",
    planned_chapter_number: planned,
  };
}

describe("情节安排分组", () => {
  it("按时间轴的讲述位置分组，未安排节点保留在最后", () => {
    const nodes = [node("n1", "抵达"), node("n2", "对质"), node("n3", "旧信", 2), node("n4", "尾声")];
    const timeline = {
      variant_id: "main",
      variant_revision: "rev",
      ledger_revision: "",
      view_revision: "",
      time_origin_label: "",
      domain: null,
      groups: [
        {
          chapter_number: 1,
          chapter_kind: "actual" as const,
          title: "第 1 章",
          nodes: [
            {
              node_id: "n1",
              title: "抵达",
              time_note: "",
              start_minute: null,
              end_minute: null,
              approximate: false,
              status: "ready" as const,
              reasons: [],
              binding_ids: [],
              chapter_paths: ["正文/0001.md"],
            },
          ],
        },
        {
          chapter_number: 2,
          chapter_kind: "planned" as const,
          title: "第 2 章",
          nodes: [
            {
              node_id: "n3",
              title: "旧信",
              time_note: "",
              start_minute: null,
              end_minute: null,
              approximate: false,
              status: "draft" as const,
              reasons: [],
              binding_ids: [],
              chapter_paths: [],
            },
          ],
        },
      ],
    };

    const groups = groupNodes(nodes, timeline);

    expect(groups.map(group => group.title)).toEqual(["第 1 章", "第 2 章", "尚未安排章节"]);
    expect(groups[0]?.nodes.map(row => row.id)).toEqual(["n1"]);
    expect(groups[1]?.nodes.map(row => row.id)).toEqual(["n3"]);
    expect(groups[2]?.nodes.map(row => row.id)).toEqual(["n2", "n4"]);
  });

  it("没有时间轴时按计划章节兜底，不把全部节点当成已出现", () => {
    const groups = groupNodes([node("n3", "旧信", 2), node("n4", "尾声")], null);

    expect(groups.map(group => group.title)).toEqual(["第 2 章", "尚未安排章节"]);
    expect(groups[0]?.nodes.map(row => row.id)).toEqual(["n3"]);
    expect(groups[1]?.nodes.map(row => row.id)).toEqual(["n4"]);
  });
});

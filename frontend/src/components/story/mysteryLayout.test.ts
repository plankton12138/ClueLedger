import { describe, expect, it } from "vitest";

import type { StoryGraph, StoryMystery } from "../../api/story";
import {
  diagnosticsForClue,
  knowledgeSubjects,
  lifecycleByRole,
  orderedMysteryNodes,
  readerReadings,
  resolutionLabel,
  roleSummary,
  useTone,
} from "./mysteryLayout";

const GRAPH: StoryGraph = {
  variant_id: "main",
  name: "测试",
  revision: "r1",
  stages: [{ id: "s1", title: "第一幕", order: 0, summary: null }],
  nodes: [
    { id: "n1", title: "发现", action: "看到手套", change: null, stage_id: "s1", thread_ids: [], clue_ids: ["c1"], time_note: "第一夜" },
    { id: "n2", title: "对质", action: "问管家", change: null, stage_id: "s1", thread_ids: [], clue_ids: ["c1"], time_note: "" },
    { id: "n3", title: "未登记", action: "雪停", change: null, stage_id: null, thread_ids: [], clue_ids: [], time_note: "" },
  ],
  edges: [],
  threads: [],
  event_times: [],
  constraints: [],
  collapsed: [],
};

const MYSTERY: StoryMystery = {
  variant_id: "main",
  revision: "r1",
  order: ["n2", "n1", "n3"],
  time_status: { state: "partial", registered: 2, unregistered: 1, total: 3 },
  nodes: [
    {
      id: "n2",
      time_status: "registered",
      before_ids: [],
      after_ids: ["n1"],
      clue_uses: [],
      knowledge_changes: [
        {
          subject_id: "detective",
          claim: "管家搬过尸体",
          stance: "knows",
          source_kind: "observation",
          source_node_ids: [],
          effective_event_node_id: "n2",
        },
      ],
    },
    {
      id: "n1",
      time_status: "registered",
      before_ids: ["n2"],
      after_ids: [],
      clue_uses: [
        {
          clue_id: "c1",
          title: "湿手套",
          roles: ["plant"],
          presentation: "手套还在滴水",
          mode: "narration",
          speaker_id: "",
          expected_interpretation: "读者以为有人刚回来",
          node_id: "n1",
          node_title: "发现",
        },
      ],
      knowledge_changes: [],
    },
    {
      id: "n3",
      time_status: "unregistered",
      before_ids: [],
      after_ids: [],
      clue_uses: [
        {
          clue_id: "c2",
          title: "干手套",
          roles: ["misdirect"],
          presentation: "另一双手套",
          mode: "action",
          speaker_id: "",
          expected_interpretation: "读者以为管家撒谎",
          node_id: "n3",
          node_title: "未登记",
        },
      ],
      knowledge_changes: [],
    },
  ],
  clues: [
    {
      clue_id: "c1",
      title: "湿手套",
      roles: ["plant", "resolve"],
      author_truth: "管家搬过尸体",
      intention: "让读者先怀疑管家",
      resolution_intent: "explain",
      has_identity: true,
      uses: [
        {
          clue_id: "c1",
          title: "湿手套",
          roles: ["plant"],
          presentation: "手套还在滴水",
          mode: "narration",
          speaker_id: "",
          expected_interpretation: "读者以为有人刚回来",
          node_id: "n1",
          node_title: "发现",
        },
        {
          clue_id: "c1",
          title: "湿手套",
          roles: ["resolve"],
          presentation: "管家承认搬过",
          mode: "testimony",
          speaker_id: "butler",
          expected_interpretation: "",
          node_id: "n2",
          node_title: "对质",
        },
      ],
    },
    {
      clue_id: "c2",
      title: "干手套",
      roles: ["misdirect"],
      author_truth: null,
      intention: "",
      resolution_intent: "leave_open",
      has_identity: false,
      uses: [
        {
          clue_id: "c2",
          title: "干手套",
          roles: ["misdirect"],
          presentation: "另一双手套",
          mode: "action",
          speaker_id: "",
          expected_interpretation: "读者以为管家撒谎",
          node_id: "n3",
          node_title: "未登记",
        },
      ],
    },
  ],
  subjects: [],
  diagnostics: [
    {
      code: "STORY_TIMELINE_PARTIAL",
      object_id: "main",
      related_ids: ["n3"],
      message: "实际时间只覆盖 2/3 个节点",
      suggestion: "补齐实际先后",
    },
    {
      code: "STORY_CLUE_READING_MISSING",
      object_id: "c2",
      related_ids: ["n3"],
      message: "线索缺少读者读法",
      suggestion: "补 expected_interpretation",
    },
  ],
  counts: { nodes: 3, clues: 2, event_times: 2, knowledge_changes: 1, subjects: 1 },
};

describe("悬疑视图排序与聚合", () => {
  it("按投影顺序排列，未登记节点在末尾", () => {
    const ordered = orderedMysteryNodes(GRAPH, MYSTERY);
    expect(ordered.map((item) => item.node.id)).toEqual(["n2", "n1", "n3"]);
    expect(ordered[2].extra?.time_status).toBe("unregistered");
    expect(ordered[0].stageTitle).toBe("第一幕");
  });

  it("没有投影时按节点 ID 回退", () => {
    const ordered = orderedMysteryNodes(GRAPH, null);
    expect(ordered.map((item) => item.node.id)).toEqual(["n1", "n2", "n3"]);
  });

  it("按主体聚合知情", () => {
    const subjects = knowledgeSubjects(MYSTERY.nodes[0]);
    expect(subjects).toEqual([
      { subject_id: "detective", claims: ["管家搬过尸体"], stances: ["knows"] },
    ]);
  });

  it("只保留写了读者理解的线索使用点", () => {
    const readings = readerReadings(MYSTERY.nodes[2]);
    expect(readings.map((use) => use.clue_id)).toEqual(["c2"]);
  });

  it("线索角色、回收方式和诊断可读", () => {
    expect(roleSummary(["plant", "resolve"])).toBe("埋设 · 回收");
    expect(roleSummary([])).toBe("角色未定");
    expect(resolutionLabel("leave_open")).toBe("保留开放");
    expect(useTone(["misdirect"])).toBe("reader");
    expect(useTone(["resolve"])).toBe("resolve");
    expect(lifecycleByRole(MYSTERY.clues[0]).map((item) => item.role)).toEqual([
      "plant",
      "resolve",
    ]);
    expect(diagnosticsForClue(MYSTERY.diagnostics, "c2")).toHaveLength(1);
  });
});

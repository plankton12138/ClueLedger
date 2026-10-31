import { describe, expect, it } from "vitest";
import type { StoryGraph, StoryNode } from "../../api/story";
import { collapseStages, layoutStory, cardWidth, columnWidth } from "./storyLayout";

function node(id: string, primary: string | null, stage = "s1", convergence = false): StoryNode {
  return { id, title: id, action: id, change: id, time_note: "", stage_id: stage,
    thread_ids: convergence ? ["a", "b", "c"] : primary ? [primary, "c"] : [],
    clue_ids: [], primary_thread_id: primary, is_convergence: convergence };
}
function fixture(): StoryGraph {
  return { variant_id: "main", name: "构思", revision: "r1", collapsed: [], constraints: [], event_times: [],
    stages: [{ id: "s1", title: "长摘要", order: 0, summary: "雪夜里的局面变化。".repeat(30) },
      { id: "s2", title: "第二阶段", order: 1, summary: null }, { id: "empty", title: "空阶段", order: 2, summary: "待安排" }],
    threads: ["a", "b", "c"].map((id, order) => ({ id, title: id, order, goal: "目标", obstacle: "阻力", intended_resolution: "收束" })),
    nodes: [node("a1", "a"), node("b1", "b"), node("c1", "c"), node("join", "a", "s1", true), node("a2", "a", "s2")],
    edges: [ ["a1", "join"], ["b1", "join"], ["c1", "join"], ["join", "a2"] ].map(([from_id, to_id], i) => ({ id: `e${i}`, from_id, to_id, kind: "next", reason: "真实影响" })),
  };
}
describe("按阶段与故事线布局", () => {
  for (const scale of [1, 1.5, 2]) it(`长摘要、空阶段、汇合与字号 ${scale} 不相交`, () => {
    const graph = fixture();
    const ids = [...graph.nodes.map(n => n.id), ...graph.stages.map(s => `stage:${s.id}`), ...graph.threads.map(t => `thread:${t.id}`)];
    const sizes = new Map(ids.map(id => [id, { width: id.startsWith("stage:") ? 88 : 280, height: (id.startsWith("stage:") ? 80 : 180) * scale }]));
    const p = layoutStory(graph, sizes);
    for (let i = 0; i < ids.length; i++) for (const other of ids.slice(i + 1)) {
      const a = { ...p.get(ids[i])!, ...sizes.get(ids[i])! };
      const b = { ...p.get(other)!, ...sizes.get(other)! };
      expect(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y, `${ids[i]} / ${other}`).toBe(true);
    }
    expect(p.get("a1")!.x).not.toBe(p.get("b1")!.x);
    expect(p.get("a1")!.x).toBe(p.get("a2")!.x);
    expect(p.get("join")!.y).toBeGreaterThan(p.get("c1")!.y + 180 * scale);
  });
  it("多归属不自动当作汇合，折叠保留既有边 ID 与阶段端点", () => {
    const graph = fixture();
    const folded = collapseStages(graph, "s2");
    expect(folded.nodes.map(n => n.id)).toEqual(["a2"]);
    expect(folded.edges).toEqual([{ ...graph.edges[3], from_id: "stage:s1" }]);
    expect(folded.stages).toHaveLength(3);
    expect(graph.nodes).toHaveLength(5);
  });
  it("未分线的真实分叉仍横向展开，单链不补造边", () => {
    const graph = fixture();
    graph.threads = [];
    graph.nodes = graph.nodes.map(n => ({ ...n, thread_ids: [], primary_thread_id: null, is_convergence: false }));
    const p = layoutStory(graph, new Map());
    expect(p.get("a1")!.x).not.toBe(p.get("b1")!.x);
    expect(graph.edges).toHaveLength(4);
  });
});


it("正常字号下列间紧凑、阶段不另占摘要行、汇合覆盖三列", () => {
  const graph = fixture();
  const width = columnWidth(graph, 1014);
  const sizes = new Map<string, { width: number; height: number }>([...graph.nodes.map(n => [n.id, { width: cardWidth(graph, n), height: 112 }] as const),
    ...graph.stages.map(s => [`stage:${s.id}`, { width: 88, height: 48 }] as const)]);
  const p = layoutStory(graph, sizes, 1014);
  expect(p.get("b1")!.x - p.get("a1")!.x - width).toBe(20);
  expect(p.get("a1")!.y).toBe(p.get("b1")!.y);
  expect(p.get("join")!.y - p.get("a1")!.y - 112).toBe(32);
  expect(cardWidth(graph, graph.nodes.find(n => n.id === "join")!, 1014)).toBe(3 * width + 40);
  expect(p.get("stage:s1")!.x + 88).toBeLessThan(p.get("a1")!.x);
});

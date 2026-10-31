import type { StoryGraph } from "../../api/story";

export type Size = { width: number; height: number };
export type Point = { x: number; y: number };
export const stageKey = (id: string) => `stage:${id}`;
export const threadKey = (id: string) => `thread:${id}`;

/** 只排列已有对象。阶段摘要与列头也占用矩形，不靠覆盖正文来显示。 */
export function columnWidth(graph: StoryGraph, available = 1014) {
  const count = graph.threads.length + (graph.nodes.some(n => !n.primary_thread_id && n.thread_ids.length !== 1 && !n.is_convergence) ? 1 : 0);
  return count ? Math.max(240, Math.min(380, (available - 128 - (count - 1) * 20) / count)) : 280;
}
export function cardWidth(graph: StoryGraph, node: StoryGraph["nodes"][number], available = 1014) {
  const width = columnWidth(graph, available);
  if (!node.is_convergence) return width;
  const threads = [...graph.threads].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  const indices = node.thread_ids.map(id => threads.findIndex(t => t.id === id)).filter(i => i >= 0);
  return (Math.max(...indices) - Math.min(...indices) + 1) * (width + 20) - 20;
}
export function layoutStory(graph: StoryGraph, sizes: Map<string, Size>, available = 1014) {
  const positions = new Map<string, Point>();
  const threads = [...graph.threads].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  const lanes = threads.map(t => t.id);
  if (!lanes.length || graph.nodes.some(n => !n.primary_thread_id && n.thread_ids.length !== 1 && !n.is_convergence)) lanes.push("");
  const width = columnWidth(graph, available), step = width + 20;
  const left = 112;
  const sorted = [...graph.nodes].sort((a, b) => (a.reveal_order ?? 0) - (b.reveal_order ?? 0) || a.id.localeCompare(b.id));
  const pending = new Set(sorted.map(n => n.id));
  const sequence: typeof sorted = [];
  while (pending.size) {
    const ready = sorted.filter(n => pending.has(n.id) && !graph.edges.some(e => e.kind === "next" && e.to_id === n.id && pending.has(e.from_id)));
    if (!ready.length) throw new Error("推进顺序存在循环");
    for (const n of ready) { pending.delete(n.id); sequence.push(n); }
  }
  threads.forEach((t, i) => positions.set(threadKey(t.id), { x: left + i * step, y: 20 }));
  let top = 20 + Math.max(0, ...threads.map(t => sizes.get(threadKey(t.id))?.height ?? 64)) + 24;
  const stages: (string | null)[] = [...graph.stages].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id)).map(s => s.id);
  if (sorted.some(n => !n.stage_id)) stages.push(null);
  const rank = new Map<string, number>();
  for (const stage of stages) {
    if (stage) positions.set(stageKey(stage), { x: 12, y: top });
    const members = sequence.filter(n => n.stage_id === stage);
    const laneRanks = new Map<string, number>();
    for (const n of members) {
      const lane = n.primary_thread_id ?? (n.thread_ids.length === 1 ? n.thread_ids[0] : "");
      const dependencies = graph.edges.filter(e => e.kind === "next" && e.to_id === n.id && members.some(m => m.id === e.from_id)).map(e => (rank.get(e.from_id) ?? -1) + 1);
      const r = Math.max(0, ...dependencies, ...(threads.length ? n.is_convergence ? [...laneRanks.values()] : [laneRanks.get(lane) ?? 0] : []));
      rank.set(n.id, r);
      if (n.is_convergence) lanes.forEach(id => laneRanks.set(id, r + 1));
      else laneRanks.set(lane, r + 1);
    }
    const rows = [...new Set(members.map(n => rank.get(n.id)!))].sort((a, b) => a - b);
    const start = top;
    for (const r of rows) {
      const row = members.filter(n => rank.get(n.id) === r);
      const height = Math.max(...row.map(n => sizes.get(n.id)?.height ?? 112));
      row.forEach((n, index) => {
        const lane = n.primary_thread_id ?? (n.thread_ids.length === 1 ? n.thread_ids[0] : "");
        const column = n.is_convergence ? Math.min(...n.thread_ids.map(id => lanes.indexOf(id)).filter(i => i >= 0)) : Math.max(0, lanes.indexOf(lane));
        positions.set(n.id, { x: left + (threads.length ? column : index) * step, y: top });
      });
      top += height + 32;
    }
    top = Math.max(top, start + (stage ? sizes.get(stageKey(stage))?.height ?? 48 : 0) + 32);
  }
  return positions;
}

/** 折叠只映射既有关系的端点；不生成新的故事关系。 */
export function collapseStages(graph: StoryGraph, expanded: string | null): StoryGraph {
  if (!expanded) return graph;
  const hidden = graph.nodes.filter(n => n.stage_id && n.stage_id !== expanded);
  const hiddenById = new Map(hidden.map(n => [n.id, stageKey(n.stage_id!)]));
  return {
    ...graph,
    nodes: graph.nodes.filter(n => !hiddenById.has(n.id)),
    collapsed: hidden.map(n => n.id),
    edges: graph.edges.map(e => ({ ...e, from_id: hiddenById.get(e.from_id) ?? e.from_id, to_id: hiddenById.get(e.to_id) ?? e.to_id })).filter(e => e.from_id !== e.to_id),
  };
}

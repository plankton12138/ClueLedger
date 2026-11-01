import type {
  StoryGraph,
  StoryMystery,
  StoryMysteryClue,
  StoryMysteryDiagnostic,
  StoryMysteryNode,
  StoryMysteryUse,
  StoryNode,
} from "../../api/story";

export const ROLE_ORDER = ["plant", "develop", "misdirect", "reinterpret", "resolve"];

const ROLE_LABELS: Record<string, string> = {
  plant: "埋设",
  develop: "推进",
  misdirect: "误导",
  reinterpret: "重释",
  resolve: "回收",
};

const RESOLUTION_LABELS: Record<string, string> = {
  explain: "计划解释",
  leave_open: "保留开放",
  undecided: "回收未定",
};

export interface MysteryNodeItem {
  node: StoryNode;
  extra: StoryMysteryNode | undefined;
  stageTitle: string;
  registered: boolean;
}

export function roleLabel(role: string): string {
  return ROLE_LABELS[role] ?? role;
}

export function roleSummary(roles: string[]): string {
  if (roles.length === 0) return "角色未定";
  return roles.map(roleLabel).join(" · ");
}

export function resolutionLabel(value: string): string {
  return RESOLUTION_LABELS[value] ?? value;
}

export function useTone(roles: string[]): "plant" | "reader" | "resolve" | "clue" {
  if (roles.includes("misdirect") || roles.includes("reinterpret")) return "reader";
  if (roles.includes("resolve")) return "resolve";
  if (roles.includes("plant")) return "plant";
  return "clue";
}

export function timeLabel(item: MysteryNodeItem): string {
  if (item.extra?.time_status === "unregistered") return "时间未登记";
  const note = item.node.time_note.trim();
  return note || "已登记实际时间";
}

export function orderedMysteryNodes(
  graph: StoryGraph,
  mystery: StoryMystery | null,
): MysteryNodeItem[] {
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const extras = new Map((mystery?.nodes ?? []).map((node) => [node.id, node]));
  const stages = new Map(graph.stages.map((stage) => [stage.id, stage.title]));
  const fallback = graph.nodes
    .map((node) => node.id)
    .sort((left, right) => left.localeCompare(right));
  const order = mystery?.order?.length ? mystery.order : fallback;
  const seen = new Set<string>();
  const ids = [...order, ...fallback].filter((id) => {
    if (seen.has(id) || !byId.has(id)) return false;
    seen.add(id);
    return true;
  });
  return ids
    .map((id) => {
      const node = byId.get(id)!;
      const extra = extras.get(id);
      return {
        node,
        extra,
        stageTitle: node.stage_id ? stages.get(node.stage_id) ?? node.stage_id : "未分阶段",
        registered: extra?.time_status === "registered",
      };
    })
}

export interface SubjectReading {
  subject_id: string;
  claims: string[];
  stances: string[];
}

export function knowledgeSubjects(node: StoryMysteryNode | undefined): SubjectReading[] {
  if (!node) return [];
  const grouped = new Map<string, { claims: string[]; stances: Set<string> }>();
  for (const change of node.knowledge_changes) {
    const entry = grouped.get(change.subject_id) ?? { claims: [], stances: new Set<string>() };
    entry.claims.push(change.claim);
    entry.stances.add(change.stance);
    grouped.set(change.subject_id, entry);
  }
  return [...grouped.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([subject_id, entry]) => ({
      subject_id,
      claims: entry.claims,
      stances: [...entry.stances].sort(),
    }));
}

export function readerReadings(node: StoryMysteryNode | undefined): StoryMysteryUse[] {
  if (!node) return [];
  return node.clue_uses
    .filter((use) => use.expected_interpretation.trim().length > 0)
    .sort((left, right) => {
      const leftMisdirect = left.roles.some((role) => role === "misdirect" || role === "reinterpret");
      const rightMisdirect = right.roles.some((role) => role === "misdirect" || role === "reinterpret");
      if (leftMisdirect !== rightMisdirect) return leftMisdirect ? -1 : 1;
      return left.title.localeCompare(right.title);
    });
}

export function clueUseNodes(clue: StoryMysteryClue): string[] {
  return [...new Set(clue.uses.map((use) => use.node_id))];
}

export function diagnosticsForClue(
  diagnostics: StoryMysteryDiagnostic[],
  clueId: string,
): StoryMysteryDiagnostic[] {
  return diagnostics.filter((item) => item.object_id === clueId);
}

export function lifecycleByRole(clue: StoryMysteryClue): {
  role: string;
  label: string;
  nodeIds: string[];
}[] {
  return ROLE_ORDER
    .map((role) => ({
      role,
      label: roleLabel(role),
      nodeIds: [...new Set(clue.uses.filter((use) => use.roles.includes(role)).map((use) => use.node_id))],
    }))
    .filter((entry) => entry.nodeIds.length > 0);
}

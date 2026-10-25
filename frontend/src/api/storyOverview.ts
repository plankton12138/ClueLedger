import { WorkspaceApiError } from "./workspace";
import type { StoryEvent } from "./storyMaterials";

export type Page<T> = { items: T[]; next_cursor: string | null; total: number };
export type Envelope = { generation: string; assembly_digest: string | null; as_of_chapter: number };
export type ModuleSource = { binding_id: string; library_id: string; module_id: string; revision_id: string; entry_id: string; digest: string };
export type Evidence = { start: number; end: number; quote: string };
export type FacetRef = { source: ModuleSource; facet_id: string };
export type Facet = { kind: "character" | "clue"; name: string; summary: string; evidence: Evidence; ref: FacetRef };
export type Binding = { binding_id: string; display_name: string; module: Pick<ModuleSource, "library_id" | "module_id" | "revision_id">; capabilities: ModuleView[]; availability: "available" | "unavailable"; diagnostic?: string };
export type ModuleView = "content" | "characters" | "clues";
export type Chapter = { path: string; chapter: number; title: string; digest: string };
export type Manifest = Envelope & { chapters: Page<Chapter>; bindings: Page<Binding>; update_summary: { changed_targets: number; incomplete_targets: number }; active_update: { turn_id: string; conversation_id: string } | null };
export type EventRow = { chapter: { path: string; title: string; chapter: number; summary: string; status: string } | null; event: (Omit<StoryEvent, "evidence"> & { evidence: (Evidence & { path: string; digest: string | null })[] }) | null; group: string; related_facets?: { name: string; kind: string; ref: FacetRef }[]; related_more?: boolean };
export type EventPage = Envelope & Page<EventRow> & { conflict_count: number; warnings: string[] };
export type ContentRecord = { entry_id: string; digest: string; title: string };
export type BindingPage = Envelope & Page<Facet | ContentRecord> & { binding: Binding };
export type Observation = { ref: FacetRef; event_id: string; text: string; evidence: Evidence; path: string; digest: string; chapter: number; mode: string };
export type FacetDetail = Envelope & { facet: Facet; observations: Page<Observation> };
export type Relation = { left: FacetRef; right: FacetRef; label: string; evidence: Evidence; nature: "declared" | "written"; path?: string; digest?: string; chapter?: number; mode?: string };
export type RelationPage = Envelope & Page<Relation> & { nodes: Facet[] };
export type UpdateRequest = { request_key: string; observed_generation: string; as_of_chapter: number };
export type UpdateResponse = { state: "noop"; generation: string } | { state: "accepted"; turn_id: string; conversation_id: string; generation: string; target_count: number };
export type UpdateProgress = { turn_id: string; conversation_id: string; run_status: string; outcome: "pending" | "complete" | "partial"; completed: number; total: number; remaining_target_ids: string[]; errors: string[] };

export async function overviewRequest<T>(workspaceId: string, path = "", params: Record<string, string | number | null | undefined> = {}, init?: RequestInit): Promise<T> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value != null) query.set(key, String(value)); });
  const response = await fetch(`/api/file-workspaces/${workspaceId}/story-overview${path}?${query}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new WorkspaceApiError(body?.error?.code ?? `HTTP_${response.status}`, body?.error?.why ?? "总览读取失败", body?.error?.fix ?? "重新读取总览后重试", response.status, body?.error?.where ?? "故事总览");
  }
  return response.json() as Promise<T>;
}

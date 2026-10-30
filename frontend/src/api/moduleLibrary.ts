/**
 * 模块库与作品装配客户端（0.8 D）。
 *
 * 两个前缀各管一件事：`/api/module-library` 是应用级库，`/api/file-workspaces/{id}`
 * 是当前作品的绑定清单。字段保持后端的 snake_case，界面只读这些结构，不自行拼路径。
 *
 * 错误沿用统一信封，转成带 code 的 `WorkspaceApiError`；界面按 code 分支。
 */

import { WorkspaceApiError } from "./workspace";

export type ModuleKind =
  | "characters"
  | "world"
  | "clues"
  | "truth"
  | "timeline"
  | "plot"
  | "reference";

export interface ModuleSpec {
  kind: ModuleKind;
  label: string;
  source_kinds: string[];
  organize_domain: string | null;
  allowed_fields: string[];
}

export interface ModuleInfo {
  module_id: string;
  kind: ModuleKind;
  name: string;
  head: string | null;
  deleted: boolean;
  created_at: string;
  updated_at: string;
  forked_from: { module_id: string; revision_id: string } | null;
  revision_count?: number;
  preview?: string;
}

export interface ModulePage {
  items: ModuleInfo[];
  total: number;
  next_cursor: string | null;
}

export interface RevisionSummary {
  revision_id: string;
  parent: string | null;
  created_at: string;
  entry_ids: string[];
}

export interface RevisionPage {
  items: RevisionSummary[];
  total: number;
  next_cursor: string | null;
}

export interface CatalogEntry {
  entry_id: string;
  kind: string;
  title: string;
  aliases: string[];
  fields: Record<string, unknown>;
  chars: number;
  digest: string;
}

export interface RevisionDetail {
  library_id: string;
  module_id: string;
  revision_id: string;
  kind: ModuleKind;
  name: string;
  entries: CatalogEntry[];
}

export interface EntryRecord {
  entry_id: string;
  kind: string;
  title: string;
  aliases: string[];
  fields: Record<string, unknown>;
  text: string;
  start: number;
  end: number;
  length: number;
  digest: string;
  truncated: boolean;
  next_cursor: number | null;
}

export interface EntryInput {
  entry_id: string;
  kind?: string;
  title: string;
  body: string;
  aliases?: string[];
  fields?: Record<string, unknown>;
}

export interface AssemblyModuleRef {
  library_id: string;
  module_id: string;
  revision_id: string;
}

export interface AssemblyBinding {
  id: string;
  label: string;
  module: AssemblyModuleRef;
  kind?: ModuleKind | "";
  name?: string;
  deleted?: boolean;
  problems?: string[];
}

export interface AssemblyView {
  schema_version: number;
  library_id: string | null;
  digest: string | null;
  bindings: AssemblyBinding[];
  links: [string, string][];
}

export interface ImportLegacyItem {
  path: string;
  module_id: string;
  revision_id: string;
  kind: ModuleKind;
  name: string;
  action: "created" | "published" | "unchanged";
}

export interface ImportLegacyResult {
  items: ImportLegacyItem[];
  assembly: AssemblyView;
  digest: string | null;
}

export interface ImportInfo {
  digest: string | null;
  items: {
    path: string;
    digest: string;
    module_id: string;
    revision_id: string;
    kind: ModuleKind;
    name: string;
  }[];
}

export interface ModuleUpdateError {
  code: string;
  where: string;
  why: string;
  fix: string;
  details?: Record<string, unknown>;
}

export interface DeriveResult {
  workspace: { id: string; name: string; path: string; exists: boolean };
  assembly: AssemblyView;
  recovered: boolean;
}

export interface ModuleUpdateResult {
  published: boolean;
  module: AssemblyModuleRef | null;
  binding_updated: boolean;
  assembly?: AssemblyView;
  error?: ModuleUpdateError;
}

const LIBRARY = "/api/module-library";
const WORKSPACES = "/api/file-workspaces";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as {
      error?: { code?: string; where?: string; why?: string; fix?: string };
    } | null;
    const error = body?.error;
    throw new WorkspaceApiError(
      error?.code ?? `HTTP_${response.status}`,
      error?.why ?? `请求没有成功（HTTP ${response.status}）`,
      error?.fix ?? "稍后重试；若持续失败，按状态码查后端日志",
      response.status,
      error?.where ?? "模块请求",
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function query(params: Record<string, string | number | boolean | null | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === null || value === undefined || value === "") continue;
    search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

// -- 库 ---------------------------------------------------------------------

export function fetchPresets(): Promise<ModuleSpec[]> {
  return request<ModuleSpec[]>(`${LIBRARY}/presets`);
}

export function listModules(options: {
  kind?: ModuleKind | null;
  q?: string;
  cursor?: string | null;
  limit?: number;
  includeDeleted?: boolean;
} = {}): Promise<ModulePage> {
  return request<ModulePage>(
    `${LIBRARY}/modules${query({
      kind: options.kind ?? "",
      q: options.q ?? "",
      cursor: options.cursor ?? "",
      limit: options.limit ?? 20,
      include_deleted: options.includeDeleted ? "true" : "",
    })}`,
  );
}

export function createModule(input: {
  kind: ModuleKind;
  name: string;
  entries: EntryInput[];
  request_key: string;
}): Promise<{ module: ModuleInfo; revision: RevisionSummary }> {
  return request(`${LIBRARY}/modules`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 精确链接不假设目标位于当前筛选的第一页；复用现有有界分页。 */
export async function findModuleInfo(moduleId: string): Promise<ModuleInfo | null> {
  let cursor: string | null = null;
  const visited = new Set<string>();
  do {
    const page = await listModules({ includeDeleted: true, limit: 50, cursor });
    const found = page.items.find(item => item.module_id === moduleId);
    if (found) return found;
    cursor = page.next_cursor;
    if (cursor && visited.has(cursor)) throw new Error("[MODULE_CURSOR_INVALID] 模块目录：分页游标重复。建议：重新读取模块库。");
    if (cursor) visited.add(cursor);
  } while (cursor);
  return null;
}

export function fetchRevisions(
  moduleId: string,
  options: { cursor?: string | null; limit?: number } = {},
): Promise<RevisionPage> {
  return request<RevisionPage>(
    `${LIBRARY}/modules/${moduleId}/revisions${query({
      cursor: options.cursor ?? "",
      limit: options.limit ?? 20,
    })}`,
  );
}

export function fetchRevision(moduleId: string, revisionId: string): Promise<RevisionDetail> {
  return request<RevisionDetail>(`${LIBRARY}/modules/${moduleId}/revisions/${revisionId}`);
}

export function fetchRecord(
  moduleId: string,
  revisionId: string,
  entryId: string,
  range: { start?: number; end?: number } = {},
): Promise<EntryRecord> {
  return request<EntryRecord>(
    `${LIBRARY}/modules/${moduleId}/revisions/${revisionId}/records/${entryId}${query(range)}`,
  );
}

/** 只有全部分段连续、版本一致且码点数吻合时，才把原文交给编辑器。 */
export async function fetchCompleteRecord(
  moduleId: string,
  revisionId: string,
  entryId: string,
  expected?: { chars: number; digest: string },
): Promise<EntryRecord> {
  let start = 0;
  let first: EntryRecord | null = null;
  const chunks: string[] = [];
  for (;;) {
    const part = await fetchRecord(moduleId, revisionId, entryId, { start });
    first ??= part;
    const end = part.end;
    const length = part.length;
    const valid = part.entry_id === entryId && part.start === start
      && Number.isSafeInteger(end) && Number.isSafeInteger(length) && length >= 0
      && end >= start && end <= length && (end > start || length === 0)
      && Array.from(part.text).length === end - start
      && part.digest === first.digest && length === first.length
      && (!expected || (part.digest === expected.digest && length === expected.chars))
      && part.truncated === (end < length)
      && part.next_cursor === (end < length ? end : null);
    if (!valid) {
      throw new Error("[MODULE_RECORD_INCOMPLETE] 模块原文：分段内容不完整或版本不一致。建议：重新读取完整版本后再编辑。");
    }
    chunks.push(part.text);
    if (!part.truncated) return { ...first, text: chunks.join(""), start: 0, end: length, truncated: false, next_cursor: null };
    start = end;
  }
}

export function publishRevision(
  moduleId: string,
  input: {
    base_revision: string | null;
    expected_head: string | null;
    entries: EntryInput[];
    request_key: string;
  },
): Promise<RevisionSummary> {
  return request<RevisionSummary>(`${LIBRARY}/modules/${moduleId}/revisions`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function forkModule(
  moduleId: string,
  input: { base_revision: string; name: string; request_key: string },
): Promise<{ module: ModuleInfo; revision: RevisionSummary }> {
  return request(`${LIBRARY}/modules/${moduleId}/fork`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function trashModule(
  moduleId: string,
  input: { expected_head: string | null; request_key: string },
): Promise<ModuleInfo> {
  return request<ModuleInfo>(`${LIBRARY}/modules/${moduleId}/trash`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function restoreModule(
  moduleId: string,
  input: { expected_head: string | null; request_key: string },
): Promise<ModuleInfo> {
  return request<ModuleInfo>(`${LIBRARY}/modules/${moduleId}/restore`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

// -- 作品装配 ---------------------------------------------------------------

export function fetchAssembly(workspaceId: string): Promise<AssemblyView> {
  return request<AssemblyView>(`${WORKSPACES}/${workspaceId}/assembly`);
}

/** 作品内保存不推进模块库的最新版本。 */
export function saveWorkspaceModule(workspaceId: string, bindingId: string, input: {
  module_id: string;
  base_revision: string;
  name: string;
  entries: EntryInput[];
  expected_digest: string | null;
  request_key: string;
}): Promise<AssemblyView> {
  return request(`${WORKSPACES}/${workspaceId}/assembly/${bindingId}/content`, {
    method: "PUT", body: JSON.stringify(input),
  });
}

export interface ChangeEditInput {
  entry_id: string;
  base_digest: string;
  find?: string;
  replace?: string;
  body?: string;
}

export interface ChangeOperationInput {
  operation_id: string;
  action: "create" | "update";
  name?: string;
  body?: string;
  binding_id?: string;
  module_id?: string;
  base_revision?: string;
  edits?: ChangeEditInput[];
}

export interface ModuleChangeSaved {
  operation_id: string;
  module_id: string | null;
  revision_id: string | null;
  binding_id?: string | null;
  name?: string | null;
  created?: boolean;
}

export interface ModuleChangeOperation {
  operation_id: string;
  action: "create" | "update";
  status?: "pending" | "saved";
  name?: string;
  /** 只在读取变更集详情时返回；新建操作的内容正文。 */
  body?: string;
  chars?: number;
  binding_id?: string;
  module_id?: string;
  base_revision?: string;
  edits?: number | ChangeEditInput[];
  saved?: ModuleChangeSaved;
}

export interface ModuleChangePrepared {
  change_set_id: string;
  status: string;
  base_assembly_digest: string | null;
  operations: ModuleChangeOperation[];
  content_ref?: { change_set_id: string; operations: string[] };
  note?: string;
}

export interface ModuleChangeError {
  code: string;
  where: string;
  why: string;
  fix: string;
  details?: Record<string, unknown>;
}

export interface ModuleChangeResult {
  applied?: boolean;
  reverted?: boolean;
  change_set_id: string;
  stage?: string;
  assembly?: AssemblyView;
  operations?: ModuleChangeOperation[];
  saved?: ModuleChangeSaved[];
  error?: ModuleChangeError;
  undo?: { available: boolean; change_set_id: string };
  note?: string;
}

export interface ModuleChangeSetView extends ModuleChangePrepared {
  turn_id: string;
  saved: ModuleChangeSaved[];
  result: ModuleChangeResult | null;
}

export function fetchModuleChangeSet(
  workspaceId: string,
  changeSetId: string,
): Promise<ModuleChangeSetView> {
  return request(`${WORKSPACES}/${workspaceId}/module-change-sets/${changeSetId}`);
}

export function applyModuleChangeSet(
  workspaceId: string,
  changeSetId: string,
  input: { expected_digest?: string | null; request_key: string; conversation_id?: string },
): Promise<ModuleChangeResult> {
  return request(`${WORKSPACES}/${workspaceId}/module-change-sets/${changeSetId}/apply`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function undoModuleChangeSet(
  workspaceId: string,
  changeSetId: string,
  input: { expected_digest?: string | null; request_key: string; conversation_id?: string },
): Promise<ModuleChangeResult> {
  return request(`${WORKSPACES}/${workspaceId}/module-change-sets/${changeSetId}/undo`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function putAssembly(
  workspaceId: string,
  input: {
    bindings: { id?: string; label?: string; module: AssemblyModuleRef }[];
    links?: [string, string][];
    expected_digest: string | null;
    request_key: string;
  },
): Promise<AssemblyView> {
  return request<AssemblyView>(`${WORKSPACES}/${workspaceId}/assembly`, {
    method: "PUT",
    body: JSON.stringify(input),
  });
}

export function fetchImportInfo(workspaceId: string): Promise<ImportInfo> {
  return request<ImportInfo>(`${WORKSPACES}/${workspaceId}/library-imports`);
}

export function importLegacyMaterials(
  workspaceId: string,
  input: { expected_digest: string | null; request_key: string },
): Promise<ImportLegacyResult> {
  return request<ImportLegacyResult>(`${WORKSPACES}/${workspaceId}/library-imports`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function deriveWorkspace(
  workspaceId: string,
  input: {
    name: string;
    replacements?: Record<string, string>;
    expected_digest: string | null;
    request_key: string;
  },
): Promise<DeriveResult> {
  return request<DeriveResult>(`${WORKSPACES}/${workspaceId}/derive`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function postModuleUpdate(
  workspaceId: string,
  input: {
    module_id: string;
    base_revision: string | null;
    expected_head: string | null;
    entries: EntryInput[];
    expected_digest: string | null;
    request_key: string;
  },
): Promise<ModuleUpdateResult> {
  return request<ModuleUpdateResult>(`${WORKSPACES}/${workspaceId}/module-updates`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

/** 新操作的请求键：同一次点击只用一个键，重试不产生第二版。 */
export function requestKey(prefix: string): string {
  const random = Math.random().toString(36).slice(2, 10);
  return `${prefix}-${Date.now().toString(36)}-${random}`;
}

/**
 * 从条目原文取出正文：头部是受控字段，编辑时只改正文，标题与字段单独维护。
 * 头部缺失时把整段当正文，不在前端猜测格式。
 */
export function entryBody(text: string): string {
  if (!text.startsWith("---\n")) return text.trim();
  const closing = text.indexOf("\n---", 4);
  if (closing < 0) return text.trim();
  return text.slice(closing + 4).trim();
}

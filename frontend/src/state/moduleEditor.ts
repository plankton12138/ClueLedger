/** 原文草稿只保留在应用会话；库、模块和基础版本共同决定归属。 */
import { create } from "zustand";
import type { EntryInput, RevisionDetail, ModuleSpec } from "../api/moduleLibrary";

export interface EntryRow {
  entry_id: string;
  kind: string;
  title: string;
  body: string;
  aliasesText: string;
  fieldsText: string;
  fieldTypes?: Record<string, "string" | "number" | "boolean">;
}
export interface ModuleDraft {
  rows: EntryRow[];
  baseline: EntryRow[];
  sequence: number;
}
export function moduleDraftKey(detail: Pick<RevisionDetail, "library_id" | "module_id" | "revision_id">): string {
  return JSON.stringify([detail.library_id, detail.module_id, detail.revision_id]);
}
export function draftDirty(draft: ModuleDraft | undefined): boolean {
  return Boolean(draft && JSON.stringify(draft.rows) !== JSON.stringify(draft.baseline));
}
export function parseFields(text: string): Record<string, unknown> | null {
  if (!text.trim()) return {};
  try {
    const value: unknown = JSON.parse(text);
    return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : null;
  } catch { return null; }
}
export function draftEntries(rows: EntryRow[]): EntryInput[] {
  return rows.map(row => {
    const fields = draftFields(row);
    if (!fields) throw new Error("[ENTRY_FIELDS_INVALID] 条目字段：字段输入不合法。建议：修正输入后再保存。");
    return ({
    entry_id: row.entry_id, kind: row.kind, title: row.title, body: row.body,
    aliases: row.aliasesText.split(/[,，]/).map(s => s.trim()).filter(Boolean),
    fields,
  }); });
}
export function draftFields(row: EntryRow): Record<string, unknown> | null {
  const fields = parseFields(row.fieldsText);
  if (!fields) return null;
  for (const [key, type] of Object.entries(row.fieldTypes ?? {})) {
    if (!(key in fields)) continue;
    const raw = fields[key];
    if (type === "number") {
      if (raw === "") { delete fields[key]; continue; }
      if ((typeof raw !== "number" && typeof raw !== "string") || !Number.isFinite(Number(raw))) return null;
      fields[key] = Number(raw);
    } else if (type === "boolean") {
      if (typeof raw !== "boolean") return null;
    } else fields[key] = String(raw);
  }
  return fields;
}
export function fieldProblem(row: EntryRow, preset: ModuleSpec | undefined): string | null {
  if (!preset?.allowed_fields) return "[MODULE_RULES_UNAVAILABLE] 条目字段：字段规则尚未读取。建议：重新读取模块库后再编辑。";
  const fields = draftFields(row);
  if (!fields) return "[ENTRY_FIELDS_INVALID] 条目字段：字段值或 JSON 对象不合法。建议：修正数字或高级字段输入后再保存。";
  const scalar = (value: unknown) => typeof value === "string" || typeof value === "boolean" || (typeof value === "number" && Number.isFinite(value));
  for (const [key, value] of Object.entries(fields)) {
    if (!preset.allowed_fields.includes(key)) return `[RECORD_FIELD_UNKNOWN] 条目字段：不支持字段 ${key}。建议：在高级字段中删除该字段，或把内容写入正文。`;
    if (!scalar(value) && !(Array.isArray(value) && value.every(scalar))) return `[RECORD_FIELD_UNKNOWN] 条目字段：${key} 需要标量或标量列表。建议：将嵌套内容写入正文。`;
  }
  return null;
}
interface ModuleEditorStore {
  drafts: Record<string, ModuleDraft>;
  newDrafts: Record<string, { kind: import("../api/moduleLibrary").ModuleKind; name: string; title: string; body: string }>;
  ensure(detail: RevisionDetail, bodies: Record<string, string>): void;
  edit(key: string, update: (rows: EntryRow[]) => EntryRow[]): void;
  discard(key: string): void;
  published(key: string, sequence: number, revisionId: string, submitted: EntryRow[]): string;
}
export const useModuleEditorStore = create<ModuleEditorStore>()((set, get) => ({
  drafts: {}, newDrafts: {},
  ensure(detail, bodies) {
    const key = moduleDraftKey(detail);
    if (get().drafts[key]) return;
    const rows = detail.entries.map(entry => ({
      entry_id: entry.entry_id, kind: entry.kind, title: entry.title, body: bodies[entry.entry_id],
      aliasesText: entry.aliases.join(", "), fieldsText: JSON.stringify(entry.fields ?? {}, null, 2),
    }));
    set(state => ({ drafts: { ...state.drafts, [key]: { rows, baseline: rows, sequence: 0 } } }));
  },
  edit(key, update) {
    const draft = get().drafts[key];
    if (!draft) return;
    set(state => ({ drafts: { ...state.drafts, [key]: { ...draft, rows: update(draft.rows), sequence: draft.sequence + 1 } } }));
  },
  discard(key) {
    const draft = get().drafts[key];
    if (draft) set(state => ({ drafts: { ...state.drafts, [key]: { ...draft, rows: draft.baseline, sequence: draft.sequence + 1 } } }));
  },
  published(key, sequence, revisionId, submitted) {
    const [library, module] = JSON.parse(key) as string[];
    const nextKey = JSON.stringify([library, module, revisionId]);
    const draft = get().drafts[key];
    if (!draft) return nextKey;
    const drafts = { ...get().drafts };
    delete drafts[key];
    drafts[nextKey] = { rows: draft.sequence === sequence ? submitted : draft.rows, baseline: submitted, sequence: draft.sequence };
    set({ drafts });
    return nextKey;
  },
}));

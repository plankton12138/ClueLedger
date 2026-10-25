/** 内容目录来自正文小标题；旧模块的条目标题仍作为普通章节保留。 */
import type { EntryInput, RevisionDetail } from "../api/moduleLibrary";

export interface ModuleDocument { detail: RevisionDetail; bodies: Record<string, string> }
export interface ModuleHeading { id: string; title: string; level: number }

export function documentKey(ref: { library_id: string; module_id: string; revision_id: string }): string {
  return JSON.stringify([ref.library_id, ref.module_id, ref.revision_id]);
}
export function contentName(name: string, body: string): string {
  if (name.trim()) return name.trim();
  const firstLine=body.split("\n").find(line=>line.trim())?.replace(/^\s*#+\s*/, "").trim() ?? "";
  return Array.from(firstLine).slice(0,80).join("") || "未命名模块";
}
export function showEntryTitle(document: ModuleDocument, entry: { title: string }): boolean {
  return document.detail.entries.length > 1 || entry.title !== document.detail.name;
}
export function moduleOutline(document: ModuleDocument): ModuleHeading[] {
  const result: ModuleHeading[] = [];
  for (const entry of document.detail.entries) {
    if (showEntryTitle(document, entry)) result.push({ id: `module-entry-${entry.entry_id}`, title: entry.title, level: 1 });
    const lines = (document.bodies[entry.entry_id] ?? "").split("\n");
    let fence: { char: string; length: number } | null = null;
    lines.forEach((line, index) => {
      const marker = line.match(/^ {0,3}(`{3,}|~{3,})/);
      if (marker) {
        if (!fence) fence = { char: marker[1][0], length: marker[1].length };
        else if (marker[1][0] === fence.char && marker[1].length >= fence.length && /^ {0,3}(`+|~+)\s*$/.test(line)) fence = null;
        return;
      }
      if (fence) return;
      const heading = line.match(/^ {0,3}(#{1,6})\s+(.+?)\s*#*\s*$/);
      if (heading) {
        const title = heading[2].replace(/[*_`]/g, "");
        if (!result.length && document.detail.entries.length===1 && title===document.detail.name) return;
        result.push({ id: `module-heading-${entry.entry_id}-${index + 1}`, title, level: heading[1].length });
      }
    });
  }
  return result;
}
export function documentEntries(document: ModuleDocument): EntryInput[] {
  return document.detail.entries.map(entry => ({
    entry_id: entry.entry_id, kind: entry.kind, title: entry.title,
    aliases: [...entry.aliases], fields: { ...entry.fields }, body: document.bodies[entry.entry_id] ?? "",
  }));
}

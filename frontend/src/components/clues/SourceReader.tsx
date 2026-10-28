import { useLayoutEffect, useRef } from "react";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { RawSourceHighlight, sourceRange } from "./SourceHighlight";

const LABEL = {
  valid: "原文依据", stale: "来源已修改", source_missing: "来源已删除或移动",
  unavailable: "来源暂不可读", detached: "计划关联已变化", invalid_range: "原文位置需重新核对",
};

export function SourceNotice() {
  const state = useSourceLocationStore();
  const currentId = useWorkspaceStore(value => value.currentId);
  const file = useWorkspaceStore(value => value.file);
  if (!state.evidenceId || state.workspaceId !== currentId) return null;
  const location = state.location;
  const changed = location?.source.kind === "file" && file?.path === location.source.path && file.digest !== location.current_digest;
  return <aside className="source-notice" aria-label="原文定位" onKeyDown={event => { if (event.key === "Escape") { event.stopPropagation(); void state.back(); } }}>
    <div className="flex flex-wrap items-center gap-2"><strong>{state.loading ? "定位原文…" : location ? LABEL[changed ? "stale" : location.freshness] : "原文定位"}</strong>
      <Button size="compact" variant="quiet" onClick={() => void state.back()}>{state.returnPage?.kind === "materials" ? "返回资料" : "关闭定位"}</Button></div>
    {state.error && <p role="alert">{state.error}</p>}
    {location && (location.freshness !== "valid" || changed) && <><blockquote>{location.quote}</blockquote><p>这是保存的旧引文，当前内容不应用旧位置高亮。</p></>}
    {location && state.taskChapter !== null && <p className="text-xs text-sub">任务截止章仍为第 {state.taskChapter} 章。</p>}
  </aside>;
}

export function ModuleSourceReader() {
  const location = useSourceLocationStore(state => state.location);
  const host = useRef<HTMLDivElement>(null);
  const range = location?.freshness === "valid" ? sourceRange(location.excerpt, location.start! - location.excerpt_start, location.end! - location.excerpt_start, location.quote) : null;
  useLayoutEffect(() => { host.current?.querySelector<HTMLElement>("[data-source-target]")?.focus({ preventScroll: true }); }, [location?.evidence_id]);
  if (!location || location.source.kind !== "module") return null;
  return <article className="min-h-0 flex-1 overflow-auto p-4" ref={host} aria-label="模块原文依据">
    <p className="mb-2 text-xs text-sub">精确修订 {location.source.revision_id} · 原文片段</p>
    <RawSourceHighlight text={location.excerpt} range={range} />
  </article>;
}

import { useEffect, useRef, useState } from "react";
import { readFile } from "../../api/workspace";
import { fetchRecord } from "../../api/moduleLibrary";
import type { Evidence, ModuleSource } from "../../api/storyOverview";
import { errorMessage } from "../../api/errors";
import { Button } from "../ui/Button";

export type EvidenceSelection = { evidence: Evidence; source: ModuleSource } | { evidence: Evidence; path: string; digest: string | null };
export default function OverviewEvidence({ workspaceId, selection, onClose, onOpenSource }: { workspaceId: string; selection: EvidenceSelection; onClose(): void; onOpenSource(path: string): void }) {
  const [verified, setVerified] = useState(false);
  const close = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    close.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { event.stopPropagation(); onClose(); } };
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("keydown", escape); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  const [error, setError] = useState("");
  const key = JSON.stringify([workspaceId, selection]);
  useEffect(() => {
    let alive = true; setVerified(false); setError("");
    const verify = async () => {
      const evidence = selection.evidence;
      if ("source" in selection) {
        const source = selection.source;
        const record = await fetchRecord(source.module_id, source.revision_id, source.entry_id, { start: evidence.start, end: evidence.end });
        return record.digest === source.digest && record.text === evidence.quote;
      }
      const file = await readFile(workspaceId, selection.path);
      return file.digest === selection.digest && Array.from(file.text).slice(evidence.start, evidence.end).join("") === evidence.quote;
    };
    void verify().then(ok => { if (alive) { setVerified(ok); if (!ok) setError("原文版本已改变，请更新总览后重新定位。"); } }).catch(cause => { if (alive) setError(errorMessage(cause, "原文依据")); });
    return () => { alive = false; };
  }, [key]);
  return <aside className="overview-evidence" aria-label="原文依据">
    <div className="overview-section-head"><h3>原文依据</h3><Button ref={close} size="compact" variant="quiet" onClick={onClose}>收起</Button></div>
    <p className="overview-muted">{"source" in selection ? "作者设定" : selection.path}</p>
    <blockquote>{selection.evidence.quote}</blockquote>
    {error ? <p role="status">{error}</p> : <p className="overview-muted">{verified ? "已核对原文版本" : "正在核对来源…"}</p>}
    {verified && "path" in selection && <Button size="compact" onClick={() => onOpenSource(selection.path)}>打开正文</Button>}
  </aside>;
}

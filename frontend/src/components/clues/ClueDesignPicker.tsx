/** 从已装模块或作品设定中选一段实际原文，关联为一条线索的作者计划。 */
import { useEffect, useRef, useState } from "react";
import {
  fetchAssembly,
  fetchCompleteRecord,
  fetchRevision,
  type AssemblyBinding,
  type AssemblyView,
  type CatalogEntry,
} from "../../api/moduleLibrary";
import { listFiles, readFile, type Catalog, type FileEntry } from "../../api/workspace";
import {
  clueRequestKey,
  linkClueDesign,
  type ClueDetail,
  type SourceRef,
} from "../../api/clues";
import { errorMessage } from "../../api/errors";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import { selectedRange, type SelectedRange } from "./sourceSelection";

interface Loaded {
  text: string;
  title: string;
  source: SourceRef;
}

interface Props {
  workspaceId: string;
  clueId: string;
  ledgerRevision: string;
  asOf: number | null;
  onLinked: (detail: ClueDetail) => void;
  onClose: () => void;
}

export default function ClueDesignPicker({
  workspaceId,
  clueId,
  ledgerRevision,
  asOf,
  onLinked,
  onClose,
}: Props) {
  const [tab, setTab] = useState<"module" | "file">("module");
  const [assembly, setAssembly] = useState<AssemblyView | null>(null);
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [binding, setBinding] = useState<AssemblyBinding | null>(null);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [entry, setEntry] = useState<CatalogEntry | null>(null);
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [range, setRange] = useState<SelectedRange | null>(null);
  const [relation, setRelation] = useState("");
  const [loading, setLoading] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const host = useRef<HTMLPreElement>(null);

  useEffect(() => {
    let alive = true;
    void Promise.all([fetchAssembly(workspaceId), listFiles(workspaceId)])
      .then(([nextAssembly, nextCatalog]) => {
        if (!alive) return;
        setAssembly(nextAssembly);
        setCatalog(nextCatalog);
        if (nextAssembly.bindings.length === 0) setTab("file");
      })
      .catch(cause => {
        if (alive) setError(errorMessage(cause, "读取可关联来源"));
      });
    return () => {
      alive = false;
    };
  }, [workspaceId]);

  async function chooseBinding(next: AssemblyBinding) {
    setBinding(next);
    setEntry(null);
    setEntries([]);
    setLoaded(null);
    setRange(null);
    setError("");
    setLoading("正在读取模块版本…");
    try {
      const revision = await fetchRevision(next.module.module_id, next.module.revision_id);
      setEntries(revision.entries);
    } catch (cause) {
      setError(errorMessage(cause, "读取模块版本"));
    } finally {
      setLoading("");
    }
  }

  async function chooseEntry(next: CatalogEntry) {
    if (!binding) return;
    setEntry(next);
    setLoaded(null);
    setRange(null);
    setError("");
    setLoading("正在读取条目原文…");
    try {
      const record = await fetchCompleteRecord(
        binding.module.module_id,
        binding.module.revision_id,
        next.entry_id,
        { chars: next.chars, digest: next.digest },
      );
      setLoaded({
        text: record.text,
        title: `${binding.name || binding.label || binding.module.module_id} · ${next.title}`,
        source: {
          kind: "module",
          library_id: binding.module.library_id,
          module_id: binding.module.module_id,
          revision_id: binding.module.revision_id,
          entry_id: next.entry_id,
          content_digest: record.digest,
        },
      });
    } catch (cause) {
      setError(errorMessage(cause, "读取模块条目"));
    } finally {
      setLoading("");
    }
  }

  async function chooseFile(next: FileEntry) {
    setLoaded(null);
    setRange(null);
    setError("");
    setLoading("正在读取设定原文…");
    try {
      const file = await readFile(workspaceId, next.path);
      setLoaded({
        text: file.text,
        title: file.display_title || next.display_title,
        source: { kind: "file", path: next.path, digest: file.digest },
      });
    } catch (cause) {
      setError(errorMessage(cause, "读取设定文件"));
    } finally {
      setLoading("");
    }
  }

  function capture() {
    if (!host.current) return;
    const next = selectedRange(host.current);
    if (next) setRange(next);
  }

  function switchTab(next: "module" | "file") {
    setTab(next);
    setEntry(null);
    setLoaded(null);
    setRange(null);
    setError("");
  }

  async function submit() {
    if (!loaded || !range || busy) return;
    setBusy(true);
    setError("");
    try {
      const detail = await linkClueDesign(workspaceId, clueId, {
        requestKey: clueRequestKey("clue-link"),
        expectedRevision: ledgerRevision,
        source: loaded.source,
        start: range.start,
        end: range.end,
        quote: range.quote,
        relation,
        asOf,
      });
      onLinked(detail);
      onClose();
    } catch (cause) {
      setError(errorMessage(cause, "关联作者计划"));
    } finally {
      setBusy(false);
    }
  }

  const files = catalog
    ? [...catalog.settings, ...catalog.characters].filter(item => item.exists)
    : [];

  return (
    <AppDialog open onOpenChange={open => { if (!open) onClose(); }} title="关联作者计划">
      <div className="space-y-3 text-sm">
        <div role="tablist" aria-label="计划来源" className="flex gap-1">
          <Button size="compact" variant={tab === "module" ? "primary" : "secondary"} onClick={() => switchTab("module")}>
            已装模块
          </Button>
          <Button size="compact" variant={tab === "file" ? "primary" : "secondary"} onClick={() => switchTab("file")}>
            作品设定
          </Button>
        </div>
        {error && <StatusMessage tone="error">{error}</StatusMessage>}
        {loading && <p className="text-xs text-sub">{loading}</p>}
        {tab === "module" ? (
          <div className="space-y-2">
            {!assembly ? <p className="text-sub">正在读取装配…</p>
              : assembly.bindings.length === 0 ? <p className="text-sub">这个作品还没有装入模块。可以先装一个计划模块，或从作品设定选择。</p>
              : <div className="flex flex-wrap gap-1">{assembly.bindings.map(item => (
                <Button key={item.id} size="compact" variant={binding?.id === item.id ? "primary" : "secondary"}
                  onClick={() => void chooseBinding(item)}>
                  {item.name || item.label || item.module.module_id}
                </Button>
              ))}</div>}
            {binding && <div className="max-h-40 overflow-auto rounded border border-row-edge">
              {entries.length === 0 ? <p className="p-2 text-xs text-sub">这一版没有条目。</p>
                : entries.map(item => (
                  <button key={item.entry_id} type="button" className="block w-full border-b border-row-edge px-2 py-1 text-left last:border-b-0 hover:bg-ui-selected"
                    aria-pressed={entry?.entry_id === item.entry_id} onClick={() => void chooseEntry(item)}>
                    <span className="text-sm">{item.title}</span><span className="ml-2 text-xs text-sub">{item.chars} 字</span>
                  </button>
                ))}
            </div>}
          </div>
        ) : (
          <div className="max-h-40 overflow-auto rounded border border-row-edge">
            {!catalog ? <p className="p-2 text-xs text-sub">正在读取设定…</p>
              : files.length === 0 ? <p className="p-2 text-xs text-sub">作品里还没有可用的设定或角色小传。</p>
              : files.map(item => (
                <button key={item.path} type="button" className="block w-full border-b border-row-edge px-2 py-1 text-left last:border-b-0 hover:bg-ui-selected"
                  onClick={() => void chooseFile(item)}>
                  <span className="text-sm">{item.display_title || item.title}</span>
                  <span className="mt-0.5 block truncate text-xs text-sub" title={item.path}>{item.path}</span>
                </button>
              ))}
          </div>
        )}
        {loaded && (
          <section aria-label="计划原文" className="space-y-2">
            <p className="text-xs text-sub">{loaded.title} · 选中文字作为计划区间</p>
            <pre ref={host} tabIndex={0} onMouseUp={capture} onKeyUp={capture}
              className="max-h-56 overflow-auto whitespace-pre-wrap rounded border border-row-edge bg-panel p-2 font-serif leading-6">{loaded.text}</pre>
            <p className="text-xs text-sub">{range ? `已选：${range.quote}` : "在原文中拖选一段，或聚焦后用键盘选择。"}</p>
            <label className="block text-xs text-sub">关系说明
              <textarea className="ui-input mt-1 min-h-16 w-full" value={relation} maxLength={500}
                onChange={event => setRelation(event.target.value)} placeholder="例如：计划让照片在第 3 章出现" />
            </label>
          </section>
        )}
        <div className="flex flex-wrap justify-end gap-2">
          <Button size="compact" variant="quiet" onClick={onClose}>取消</Button>
          <Button size="compact" variant="primary" busy={busy} disabled={!range}
            disabledReason="先在原文中选择一段文字" onClick={() => void submit()}>
            关联到线索
          </Button>
        </div>
      </div>
    </AppDialog>
  );
}

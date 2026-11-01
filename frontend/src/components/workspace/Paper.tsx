/**
 * 稿纸（0.4 V04-4）：正文与设定共用的只读 Markdown 阅读器。
 *
 * 只读是有意的：修改走对话里的 Agent 或应用之外的编辑器，界面不再提供第二条
 * 写入路径。`skipHtml` 关掉内嵌 HTML，避免正文里的标签被当成页面结构执行。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Highlighter, Info } from "lucide-react";
import { useWorkspaceStore } from "../../state/workspace";
import { usePagePositions } from "../../state/pagePositions";
import { FONT_SCALE, useLayoutStore } from "../../state/layout";
import { navigate, parseHash } from "../../state/navigation";
import { Button } from "../ui/Button";

import { saveReadingPosition, type FileView } from "../../api/workspace";
import { fetchClueMarks, type ClueMarks } from "../../api/clueMarks";
import { readStoryProseMarks, type ProseMark, type ProseMarks } from "../../api/story";
import { errorMessage } from "../../api/errors";
import CharacterBadges from "./CharacterBadges";
import SelectionToChat from "./SelectionToChat";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { useStoryStore } from "../../state/story";
import { RawSourceHighlight, sourceHighlightPlugin, sourceRange, type SourceRange } from "../clues/SourceHighlight";
import { clueMarksPlugin } from "./ClueMarks";

interface Props {
  file: FileView | null;
  /** 正在读取的文章路径；null 表示没有读取请求在途。 */
  openingPath: string | null;
  /** 有正文正在读取；恢复上次阅读位置时路径可能还没确定。 */
  fileLoading: boolean;
  /** 作品级操作进行中：翻章按钮在此期间禁用。 */
  busy: boolean;
  onOpenClue?: (clueId: string) => void;
}

export default function Paper({ file, openingPath, fileLoading, busy, onOpenClue }: Props) {
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const catalog = useWorkspaceStore(state => state.catalog);
  const chapters = catalog.chapters;
  const openPath = useWorkspaceStore(state => state.openPath);
  const fontScale = useLayoutStore(state => state.fontScale);
  const setFontScale = useLayoutStore(state => state.setFontScale);
  const openingEntry = openingPath
    ? [...catalog.chapters, ...catalog.characters, ...catalog.settings].find(entry => entry.path === openingPath)
    : undefined;
  // 读取同一篇时保留现有正文（回读刷新）；换成另一篇或首次打开时才显示载入。
  const waiting = fileLoading && (!file || openingPath !== file.path);
  const activePath = waiting ? openingPath : file?.path;
  const chapterIndex = activePath ? chapters.findIndex(chapter => chapter.path === activePath) : -1;
  const previous = chapterIndex > 0 ? chapters[chapterIndex - 1] : undefined;
  const next = chapterIndex >= 0 ? chapters[chapterIndex + 1] : undefined;
  function readChapter(path: string) {
    const source = useSourceLocationStore.getState();
    if (source.restorePosition) source.consumeRestore(source.restorePosition.epoch);
    const page = parseHash(window.location.hash);
    if (page?.kind === "writing" && page.sourceId) {
      source.clear();
      navigate({ kind: "writing", workspaceId: page.workspaceId });
    }
    void openPath(path);
  }
  const positionKey = `${workspaceId}:paper:${file?.path ?? ""}`;
  const scroll = useRef<HTMLDivElement>(null);
  const [positionError, setPositionError] = useState<string | null>(null);
  useEffect(() => {
    if (!workspaceId || !file) return;
    const id = workspaceId, source = file;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let active = true;
    let lastOffset = -1;
    const save = () => {
      const offset = usePagePositions.getState().offsets[positionKey] ?? 0;
      if (offset === lastOffset) return;
      lastOffset = offset;
      void saveReadingPosition(id, { path: source.path, digest: source.digest, offset })
        .then(() => { if (active) setPositionError(null); })
        .catch(error => { if (active) setPositionError(errorMessage(error, "阅读位置")); });
    };
    const unsubscribe = usePagePositions.subscribe((state, before) => {
      if (state.offsets[positionKey] === before.offsets[positionKey]) return;
      clearTimeout(timer); timer = setTimeout(save, 200);
    });
    save();
    window.addEventListener("pagehide", save);
    return () => { active = false; clearTimeout(timer); save(); unsubscribe(); window.removeEventListener("pagehide", save); };
  }, [workspaceId, file?.path, file?.digest, positionKey]);
  const [paragraphLocation, setParagraphLocation] = useState(false);
  const [clueMarks, setClueMarks] = useState<ClueMarks | null>(null);
  const [marksError, setMarksError] = useState<string | null>(null);
  const [showMarks, setShowMarks] = useState(true);
  const [proseMarks, setProseMarks] = useState<ProseMarks | null>(null);
  const [originRange, setOriginRange] = useState<SourceRange | null>(null);
  const storyVariantId = useStoryStore(state => state.workspaceId === workspaceId ? state.variantId : null);
  useEffect(() => {
    if (!workspaceId || !file || file.kind !== "chapter" || waiting) {
      setClueMarks(null);
      setMarksError(null);
      return;
    }
    let cancelled = false;
    setMarksError(null);
    void fetchClueMarks(workspaceId, file.path, file.chapter_number)
      .then(data => { if (!cancelled) setClueMarks(data); })
      .catch(cause => {
        if (!cancelled) {
          setClueMarks(null);
          setMarksError(errorMessage(cause, "读取线索标注"));
        }
      });
    return () => { cancelled = true; };
  }, [workspaceId, file?.path, file?.digest, file?.kind, file?.chapter_number, waiting]);
  useEffect(() => {
    setOriginRange(null);
  }, [file?.path, file?.digest]);
  useEffect(() => {
    if (!workspaceId || !storyVariantId || !file || file.kind !== "chapter" || waiting) {
      setProseMarks(null);
      return;
    }
    let cancelled = false;
    void readStoryProseMarks(workspaceId, storyVariantId, file.path)
      .then(data => { if (!cancelled) setProseMarks(data); })
      .catch(() => { if (!cancelled) setProseMarks(null); });
    return () => { cancelled = true; };
  }, [workspaceId, storyVariantId, file?.path, file?.digest, file?.kind, waiting]);
  const restoreEpoch = useSourceLocationStore(state => state.restoreEpoch);
  const location = useSourceLocationStore(state => state.workspaceId === workspaceId ? state.location : null);
  const matched = location?.source.kind === "file" && location.source.path === file?.path && location.freshness === "valid" && file.digest === location.current_digest && file.digest === location.source.digest;
  const range = matched ? sourceRange(file.text, location.start, location.end, location.quote) : null;
  const currentMarks = clueMarks && clueMarks.path === file?.path && clueMarks.digest === file?.digest ? clueMarks : null;
  const currentProseMarks = proseMarks && proseMarks.path === file?.path && proseMarks.digest === file?.digest ? proseMarks : null;
  const proseMarkChips = useMemo(() => {
    if (!currentProseMarks) return [];
    const totals = new Map<string, number>();
    for (const mark of currentProseMarks.marks) {
      totals.set(mark.title, (totals.get(mark.title) ?? 0) + 1);
    }
    const seen = new Map<string, number>();
    return currentProseMarks.marks.map(mark => {
      const ordinal = (seen.get(mark.title) ?? 0) + 1;
      seen.set(mark.title, ordinal);
      const label = (totals.get(mark.title) ?? 0) > 1 ? `${mark.title} · ${ordinal}` : mark.title;
      return { mark, label };
    });
  }, [currentProseMarks]);
  const activeMarks = showMarks && !range && !originRange && currentMarks?.state === "ready" ? currentMarks.marks : [];
  useLayoutEffect(() => {
    const node = scroll.current;
    if (!node || range) return;
    const pending = useSourceLocationStore.getState().restorePosition;
    const offset = pending?.key === positionKey ? pending.offset : usePagePositions.getState().offsets[positionKey] ?? 0;
    // 页面常驻但可能隐藏；在重新可见后恢复，避免 hidden 时写入 scrollTop 失效。
    const observer = new ResizeObserver(() => restore());
    function restore() {
      if (node && node.clientHeight) {
        node.scrollTop = offset;
        if (pending?.key === positionKey) useSourceLocationStore.getState().consumeRestore(pending.epoch);
        observer.disconnect();
      }
    }
    observer.observe(node); restore();
    return () => observer.disconnect();
  }, [positionKey, restoreEpoch, range?.start, range?.end]);
  useLayoutEffect(() => {
    const target = scroll.current?.querySelector<HTMLElement>("[data-source-target]");
    setParagraphLocation(Boolean(target && range && target.dataset.sourceTarget === "paragraph"));
    if (!target || !scroll.current || !range) return;
    scroll.current.scrollTop += target.getBoundingClientRect().top - scroll.current.getBoundingClientRect().top - 36;
    target.focus({ preventScroll: true });
  }, [positionKey, location?.evidence_id, file?.digest, range?.start, range?.end]);
  useLayoutEffect(() => {
    const node = scroll.current;
    if (!node || !range) return;
    const observer = new ResizeObserver(() => {
      const target = node.querySelector<HTMLElement>("[data-source-target]");
      if (target && node.clientHeight) node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - 36;
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [positionKey, range?.start, range?.end]);
  useLayoutEffect(() => {
    const node = scroll.current;
    if (!node || !originRange) return;
    const target = node.querySelector<HTMLElement>("[data-source-target]");
    if (!target) return;
    node.scrollTop += target.getBoundingClientRect().top - node.getBoundingClientRect().top - 36;
    target.focus({ preventScroll: true });
  }, [originRange?.start, originRange?.end, file?.digest]);
  if (!file) {
    if (waiting) {
      return (
        <div className="grid h-full place-items-center px-8 text-center text-sm text-sub">
          <p role="status">{openingPath ? "正在读取文章…" : "正在载入作品…"}</p>
        </div>
      );
    }
    return (
      <div className="grid h-full place-items-center px-8 text-center text-sm text-sub">
        <div>
          <p className="font-serif text-lg font-bold text-fg-strong">还没有打开文章</p>
          <p className="mt-2 text-xs">
            从左侧选择一章或一份设定；也可以在对话里直接说「写第一章」。
          </p>
        </div>
      </div>
    );
  }

  const title = waiting && openingEntry ? openingEntry.display_title : file.display_title;
  const roles = waiting && openingEntry ? openingEntry.roles : file.roles;
  const highlightRange = originRange ?? range;
  const rawHighlight = (matched || originRange !== null) && file.text !== file.display_text;

  function openClueFromTarget(target: EventTarget | null) {
    if (!(target instanceof Element)) return;
    const clue = target.closest<HTMLElement>("[data-clue-id]");
    const clueId = clue?.dataset.clueId;
    if (clueId) onOpenClue?.(clueId);
  }

  function focusProseMark(mark: ProseMark) {
    if (!file) return;
    const direct = sourceRange(file.display_text, mark.start, mark.end, mark.quote);
    const fallbackStart = file.display_text.indexOf(mark.quote);
    const next = direct ?? (fallbackStart >= 0
      ? { start: fallbackStart, end: fallbackStart + mark.quote.length }
      : null);
    if (!next) return;
    if (originRange && originRange.start === next.start && originRange.end === next.end) {
      setOriginRange(null);
      return;
    }
    setOriginRange(next);
  }

  return (
    <article className="reader-document" aria-busy={waiting}>
      <header className="paper-toolbar">
        <div className="paper-navigation" aria-label="章节翻页">
          <Button size="compact" aria-label="上一章" title={previous?.display_title ?? "没有上一章"} disabled={!previous || busy || waiting} onClick={() => previous && readChapter(previous.path)}><ChevronLeft aria-hidden="true" size={18} /></Button>
          <Button size="compact" aria-label="下一章" title={next?.display_title ?? "没有下一章"} disabled={!next || busy || waiting} onClick={() => next && readChapter(next.path)}><ChevronRight aria-hidden="true" size={18} /></Button>
        </div>
        <h2 title={title}>{title}</h2>
        <CharacterBadges roles={roles} />
        <div className="paper-reading-tools" aria-label="阅读设置">
          <Button size="compact" aria-label="减小字号" title="减小字号" disabled={fontScale <= FONT_SCALE.min} onClick={() => setFontScale(fontScale - 10)}>A−</Button>
          <Button size="compact" aria-label="增大字号" title="增大字号" disabled={fontScale >= FONT_SCALE.max} onClick={() => setFontScale(fontScale + 10)}>A+</Button>
          {!waiting && currentMarks?.state === "ready" && currentMarks.marks.length > 0 && (
            <Button
              size="compact"
              aria-label="线索标注"
              title={showMarks ? "隐藏正文线索下划线" : "显示正文线索下划线"}
              aria-pressed={showMarks}
              onClick={() => setShowMarks(value => !value)}
            >
              <Highlighter aria-hidden="true" size={15} />{currentMarks.marks.length}
            </Button>
          )}
          {!waiting && <details className="paper-file-info"><summary aria-label="文件信息" title="文件信息"><Info aria-hidden="true" size={17} /></summary><div>{file.path}<br />{file.size} 字节</div></details>}
        </div>
        {busy && <span className="text-[11px]">读取中…</span>}
        {!waiting && file.metadata_warnings.length > 0 && (
          <p className="meta-warning basis-full">
            {file.metadata_warnings.join("；")}
          </p>
        )}
      </header>
      {!waiting && currentProseMarks && currentProseMarks.marks.length > 0 && (
        <div className="paper-prose-origin" aria-label="构思出处">
          <span className="paper-prose-origin-label">构思出处</span>
          {proseMarkChips.map(({ mark, label }) => (
            <button
              key={`${mark.node_id}:${mark.start}`}
              type="button"
              className="paper-prose-origin-chip"
              title={mark.quote}
              onClick={() => focusProseMark(mark)}
            >
              {label}
              <i className={`paper-prose-origin-dot ${mark.state}`} aria-hidden="true" />
            </button>
          ))}
        </div>
      )}
      {!waiting && currentMarks?.state === "stale" && (
        <p className="paper-clue-state" role="status">正文已修改，线索标注暂不可用。</p>
      )}
      {!waiting && marksError && <p className="paper-clue-state" role="status">{marksError}</p>}
      {!waiting && matched && paragraphLocation && <p className="mb-2 text-xs text-sub" role="status">已定位至所在段落。</p>}
      {!waiting && positionError && <p role="status" className="text-xs text-amber">{positionError}</p>}
      {waiting ? (
        <div className="paper-sheet grid min-h-0 flex-1 place-items-center rounded-sm" role="status">
          <p className="text-sm text-[#8a8371]">正在读取文章…</p>
        </div>
      ) : (
        <div ref={scroll} className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm" onScroll={event => { if (event.currentTarget.clientHeight) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
          <div className="paper-lined min-h-full px-8 py-6">
            {file.display_text.trim() || (matched && file.text.trim()) ? (
              <SelectionToChat source={{ title: file.display_title, source: file.path, version: file.digest }}>
                <div
                  className="prose-paper"
                  onClick={event => {
                    const selection = window.getSelection();
                    if (selection && !selection.isCollapsed) return;
                    openClueFromTarget(event.target);
                  }}
                  onKeyDown={event => {
                    if (event.key !== "Enter" && event.key !== " ") return;
                    if (!(event.target instanceof Element) || !event.target.closest("[data-clue-id]")) return;
                    event.preventDefault();
                    openClueFromTarget(event.target);
                  }}
                >
                  {rawHighlight ? <RawSourceHighlight text={file.text} range={highlightRange} /> : <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={originRange
                    ? [[sourceHighlightPlugin, { text: file.display_text, range: originRange }]]
                    : activeMarks.length > 0
                      ? [[clueMarksPlugin, { text: file.display_text, marks: activeMarks }]]
                      : [[sourceHighlightPlugin, { text: file.display_text, range }]]} skipHtml>
                    {file.display_text}
                  </ReactMarkdown>}
                </div>
              </SelectionToChat>
            ) : (
              <p className="text-sm italic text-[#8a8371]">这一页还是空的。</p>
            )}
          </div>
        </div>
      )}
    </article>
  );
}

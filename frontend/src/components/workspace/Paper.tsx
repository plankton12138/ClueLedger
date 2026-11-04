/**
 * 稿纸（1.0.9 P5；v1.2 增加修订模式）。
 *
 * 平时仍以阅读为主；章节正文可以进入「修订」亲手改稿，确认后入稿并留下修订记录。
 * 资料、设定与来源证据保持只读，内容修改仍可从对话发起。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, History, Info, PenLine } from "lucide-react";

import { saveReadingPosition, type FileView } from "../../api/workspace";
import { listRevisions } from "../../api/revisions";
import { errorMessage } from "../../api/errors";
import { FONT_SCALE, useLayoutStore } from "../../state/layout";
import { navigate } from "../../state/navigation";
import { usePagePositions } from "../../state/pagePositions";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { useDiscussionFocusStore } from "../../state/discussionContext";
import { useWorkspaceStore } from "../../state/workspace";
import { useWritingStore, writingDraftKey } from "../../state/writing";
import { useRevisionPanelStore } from "../../state/revisions";
import RevisionEditor from "../writing/RevisionEditor";
import { Button } from "../ui/Button";
import { RawSourceHighlight, sourceHighlightPlugin, sourceRange } from "../clues/SourceHighlight";
import CharacterBadges from "./CharacterBadges";
import SelectionToChat, { type ProseSelection } from "./SelectionToChat";
import { sourceAnchorsPlugin } from "./SourceAnchors";

interface Props {
  file: FileView | null;
  /** 正在读取的文章路径；null 表示没有读取请求在途。 */
  openingPath: string | null;
  /** 有正文正在读取；恢复上次阅读位置时路径可能还没确定。 */
  fileLoading: boolean;
  /** 作品级操作进行中：翻章按钮在此期间禁用。 */
  busy: boolean;
  /** 当前正文对应的章节；只有章节正文可以进入修订模式。 */
  chapterId?: string | null;
}

export default function Paper({ file, openingPath, fileLoading, busy, chapterId = null }: Props) {
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const chapters = useWorkspaceStore(state => state.catalog.chapters);
  const openPath = useWorkspaceStore(state => state.openPath);
  const fontScale = useLayoutStore(state => state.fontScale);
  const setFontScale = useLayoutStore(state => state.setFontScale);
  const openingEntry = openingPath
    ? [...chapters, ...useWorkspaceStore.getState().catalog.characters, ...useWorkspaceStore.getState().catalog.settings].find(entry => entry.path === openingPath)
    : undefined;
  // 读取同一篇时保留现有正文；换成另一篇或首次打开时才显示载入。
  const waiting = fileLoading && (!file || openingPath !== file.path);
  const activePath = waiting ? openingPath : file?.path;
  const chapterIndex = activePath ? chapters.findIndex(chapter => chapter.path === activePath) : -1;
  const previous = chapterIndex > 0 ? chapters[chapterIndex - 1] : undefined;
  const next = chapterIndex >= 0 ? chapters[chapterIndex + 1] : undefined;
  const positionKey = `${workspaceId}:paper:${file?.path ?? ""}`;
  const scroll = useRef<HTMLDivElement>(null);
  const [positionError, setPositionError] = useState<string | null>(null);
  const [paragraphLocation, setParagraphLocation] = useState(false);
  const restoreEpoch = useSourceLocationStore(state => state.restoreEpoch);
  const location = useSourceLocationStore(state => state.workspaceId === workspaceId ? state.location : null);
  const matched = location?.source.kind === "file" && location.source.path === file?.path && location.freshness === "valid" && file.digest === location.current_digest && file.digest === location.source.digest;
  const range = matched ? sourceRange(file.text, location.start, location.end, location.quote) : null;
  const draft = useWritingStore(state =>
    workspaceId && chapterId ? state.drafts[writingDraftKey(workspaceId, chapterId)] : undefined,
  );
  const openRevisionPanel = useRevisionPanelStore(state => state.open);
  const [editing, setEditing] = useState(false);
  const [latestRevision, setLatestRevision] = useState<string | null>(null);

  useEffect(() => {
    setEditing(false);
    setLatestRevision(null);
  }, [file?.path]);

  useEffect(() => {
    if (!workspaceId || !chapterId) return;
    let cancelled = false;
    void listRevisions(workspaceId, chapterId)
      .then(page => { if (!cancelled) setLatestRevision(page.items[0]?.id ?? null); })
      .catch(() => { if (!cancelled) setLatestRevision(null); });
    return () => { cancelled = true; };
  }, [workspaceId, chapterId, file?.digest]);

  function readChapter(path: string) {
    const source = useSourceLocationStore.getState();
    if (source.restorePosition) source.consumeRestore(source.restorePosition.epoch);
    if (workspaceId) navigate({ kind: "writing", workspaceId, prosePath: path });
    else void openPath(path);
  }

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

  if (!file) {
    if (waiting) {
      return <div className="grid h-full place-items-center px-8 text-center text-sm text-sub">
        <p role="status">{openingPath ? "正在读取文章…" : "正在载入作品…"}</p>
      </div>;
    }
    return <div className="grid h-full place-items-center px-8 text-center text-sm text-sub">
      <div>
        <p className="font-serif text-lg font-bold text-fg-strong">还没有打开文章</p>
        <p className="mt-2 text-xs">从左侧选择一篇正文或一份设定。</p>
      </div>
    </div>;
  }

  const title = waiting && openingEntry ? openingEntry.display_title : file.display_title;
  const roles = waiting && openingEntry ? openingEntry.roles : file.roles;
  const rawHighlight = matched && file.text !== file.display_text;

  function discussSelection(selection: ProseSelection) {
    if (!file || !selection.range) return;
    const quote = selection.range.quote;
    useDiscussionFocusStore.getState().prepare({
      focus: {
        kind: "selection",
        path: file.path,
        digest: file.digest,
        start: selection.range.start,
        end: selection.range.end,
        quote,
      },
      label: `选段：${quote.length > 24 ? `${quote.slice(0, 24)}…` : quote}`,
    });
  }

  return <article className="reader-document" aria-busy={waiting}>
    <header className="paper-toolbar">
      <div className="paper-navigation" aria-label="章节翻页">
        <Button size="compact" aria-label="上一章" title={previous?.display_title ?? "没有上一章"} disabled={!previous || busy || waiting} onClick={() => previous && readChapter(previous.path)}><ChevronLeft aria-hidden="true" size={18} /></Button>
        <Button size="compact" aria-label="下一章" title={next?.display_title ?? "没有下一章"} disabled={!next || busy || waiting} onClick={() => next && readChapter(next.path)}><ChevronRight aria-hidden="true" size={18} /></Button>
      </div>
      <h2 title={title}>{title}</h2>
      {chapterId && !waiting && (
        <div className="paper-revise-actions">
          {!editing && (
            <Button
              size="compact"
              onClick={() => setEditing(true)}
              disabled={!draft}
              disabledReason="正文还在读取"
            >
              <PenLine size={15} strokeWidth={2} aria-hidden="true" />修订
            </Button>
          )}
          {!editing && latestRevision && (
            <Button
              size="compact"
              onClick={() => workspaceId && openRevisionPanel({
                workspaceId,
                chapterId,
                revisionId: latestRevision,
                tab: "revisions",
              })}
            >
              <History size={15} strokeWidth={2} aria-hidden="true" />查看修订
            </Button>
          )}
          {!editing && draft?.dirty && draft.confirmRequired && (
            <span className="revision-pending" role="status">有未入稿的修订</span>
          )}
        </div>
      )}
      <CharacterBadges roles={roles} />
      <div className="paper-reading-tools" aria-label="阅读设置">
        <Button size="compact" aria-label="减小字号" title="减小字号" disabled={fontScale <= FONT_SCALE.min} onClick={() => setFontScale(fontScale - 10)}>A−</Button>
        <Button size="compact" aria-label="增大字号" title="增大字号" disabled={fontScale >= FONT_SCALE.max} onClick={() => setFontScale(fontScale + 10)}>A+</Button>
        {!waiting && <details className="paper-file-info"><summary aria-label="文件信息" title="文件信息"><Info aria-hidden="true" size={17} /></summary><div>{file.path}<br />{file.size} 字节</div></details>}
      </div>
      {busy && <span className="text-[11px]">读取中…</span>}
      {!waiting && file.metadata_warnings.length > 0 && <p className="meta-warning basis-full">{file.metadata_warnings.join("；")}</p>}
    </header>
    {!waiting && matched && paragraphLocation && <p className="mb-2 text-xs text-sub" role="status">已定位至所在段落。</p>}
    {!waiting && positionError && <p role="status" className="text-xs text-amber">{positionError}</p>}
    {waiting ? <div className="paper-sheet grid min-h-0 flex-1 place-items-center rounded-sm" role="status">
      <p className="text-sm text-[#8a8371]">正在读取文章…</p>
    </div> : editing && chapterId && workspaceId ? <RevisionEditor
      workspaceId={workspaceId}
      chapterId={chapterId}
      onExit={() => setEditing(false)}
      onSaved={revisionId => {
        if (revisionId) setLatestRevision(revisionId);
        setEditing(false);
        // 入稿后重新读取正文，阅读态立即显示确认后的文字。
        if (file) void useWorkspaceStore.getState().openPath(file.path);
      }}
    /> : <div ref={scroll} className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm" onScroll={event => { if (event.currentTarget.clientHeight) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
      <div className="paper-lined min-h-full px-8 py-6">
        {file.display_text.trim() || (matched && file.text.trim()) ? <SelectionToChat
          source={{ title: file.display_title, source: file.path, version: file.digest }}
          sourceText={file.display_text}
          onDiscuss={discussSelection}
        >
          <div className="prose-paper">
            {rawHighlight ? <RawSourceHighlight text={file.text} range={range} /> : <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[
              [sourceAnchorsPlugin, { text: file.display_text }],
              [sourceHighlightPlugin, { text: file.display_text, range }],
            ]} skipHtml>
              {file.display_text}
            </ReactMarkdown>}
          </div>
        </SelectionToChat> : <p className="text-sm italic text-[#8a8371]">这一页还是空的。</p>}
      </div>
    </div>}
  </article>;
}

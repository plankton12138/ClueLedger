/**
 * 稿纸（0.4 V04-4）：正文与设定共用的只读 Markdown 阅读器。
 *
 * 只读是有意的：修改走对话里的 Agent 或应用之外的编辑器，界面不再提供第二条
 * 写入路径。`skipHtml` 关掉内嵌 HTML，避免正文里的标签被当成页面结构执行。
 */

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Info } from "lucide-react";
import { useWorkspaceStore } from "../../state/workspace";
import { usePagePositions } from "../../state/pagePositions";
import { FONT_SCALE, useLayoutStore } from "../../state/layout";
import { navigate, parseHash } from "../../state/navigation";
import { Button } from "../ui/Button";

import { saveReadingPosition, type FileView } from "../../api/workspace";
import { errorMessage } from "../../api/errors";
import CharacterBadges from "./CharacterBadges";
import SelectionToChat from "./SelectionToChat";
import { useSourceLocationStore } from "../../state/sourceLocation";
import { RawSourceHighlight, sourceHighlightPlugin, sourceRange } from "../clues/SourceHighlight";

interface Props {
  file: FileView | null;
  loading: boolean;
}

export default function Paper({ file, loading }: Props) {
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const chapters = useWorkspaceStore(state => state.catalog.chapters);
  const openPath = useWorkspaceStore(state => state.openPath);
  const fontScale = useLayoutStore(state => state.fontScale);
  const setFontScale = useLayoutStore(state => state.setFontScale);
  const chapterIndex = file?.kind === "chapter" ? chapters.findIndex(chapter => chapter.path === file.path) : -1;
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
  const restoreEpoch = useSourceLocationStore(state => state.restoreEpoch);
  const location = useSourceLocationStore(state => state.workspaceId === workspaceId ? state.location : null);
  const matched = location?.source.kind === "file" && location.source.path === file?.path && location.freshness === "valid" && file.digest === location.current_digest && file.digest === location.source.digest;
  const range = matched ? sourceRange(file.text, location.start, location.end, location.quote) : null;
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

  return (
    <article className="reader-document">
      <header className="paper-toolbar">
        <div className="paper-navigation" aria-label="章节翻页">
          <Button size="compact" aria-label="上一章" title={previous?.display_title ?? "没有上一章"} disabled={!previous || loading} onClick={() => previous && readChapter(previous.path)}><ChevronLeft aria-hidden="true" size={18} /></Button>
          <Button size="compact" aria-label="下一章" title={next?.display_title ?? "没有下一章"} disabled={!next || loading} onClick={() => next && readChapter(next.path)}><ChevronRight aria-hidden="true" size={18} /></Button>
        </div>
        <h2 title={file.display_title}>
          {file.display_title}
        </h2>
        <CharacterBadges roles={file.roles} />
        <div className="paper-reading-tools" aria-label="阅读设置">
          <Button size="compact" aria-label="减小字号" title="减小字号" disabled={fontScale <= FONT_SCALE.min} onClick={() => setFontScale(fontScale - 10)}>A−</Button>
          <Button size="compact" aria-label="增大字号" title="增大字号" disabled={fontScale >= FONT_SCALE.max} onClick={() => setFontScale(fontScale + 10)}>A+</Button>
          <details className="paper-file-info"><summary aria-label="文件信息" title="文件信息"><Info aria-hidden="true" size={17} /></summary><div>{file.path}<br />{file.size} 字节</div></details>
        </div>
        {loading && <span className="text-[11px]">读取中…</span>}
        {file.metadata_warnings.length > 0 && (
          <p className="meta-warning basis-full">
            {file.metadata_warnings.join("；")}
          </p>
        )}
      </header>
      {matched && paragraphLocation && <p className="mb-2 text-xs text-sub" role="status">已定位至所在段落。</p>}
      {positionError && <p role="status" className="text-xs text-amber">{positionError}</p>}
      <div ref={scroll} className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm" onScroll={event => { if (event.currentTarget.clientHeight) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
        <div className="paper-lined min-h-full px-8 py-6">
          {file.display_text.trim() || (matched && file.text.trim()) ? (
            <SelectionToChat source={{ title: file.display_title, source: file.path, version: file.digest }}>
              <div className="prose-paper">
                {matched && file.text !== file.display_text ? <RawSourceHighlight text={file.text} range={range} /> : <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[[sourceHighlightPlugin, { text: file.display_text, range }]]} skipHtml>
                  {file.display_text}
                </ReactMarkdown>}
              </div>
            </SelectionToChat>
          ) : (
            <p className="text-sm italic text-[#8a8371]">这一页还是空的。</p>
          )}
        </div>
      </div>
    </article>
  );
}

/** 写作页只读目录（v1.2）。内容修改只从对话发起；模块只在此处装入和阅读。 */

import { useEffect, useRef, useState } from "react";
import { ListTree, Pencil } from "lucide-react";
import type { WritingBookView } from "../../api/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import { useWorkspaceStore } from "../../state/workspace";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { useWritingStore } from "../../state/writing";
import WorkspaceModules from "../workspace/WorkspaceModules";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";

export default function WritingNavigator({
  book,
  selectedChapterId,
  selectedProsePath,
  loading,
  onStructure,
  onChapter,
  onProse,
  onAskModules,
  onAskStructure,
  planRevision,
}: {
  book: WritingBookView | null;
  selectedChapterId: string | null;
  selectedProsePath: string | null;
  loading: boolean;
  onStructure: () => void;
  onChapter: (chapterId: string) => void;
  onProse: (path: string) => void;
  /** 把“让模型补充资料”的请求填进对话草稿，不直接发送。 */
  onAskModules: () => void;
  /** 结构尚未建立时，把作者带到对话建立第一版结构。 */
  onAskStructure: () => void;
  /** 当前作品修订；修改章节名时作为并发前提。 */
  planRevision: string | null;
}) {
  type Chapter = WritingBookView["chapters"][number];
  type Prose = WritingBookView["unplanned_prose"][number];
  type RenameTarget =
    | { kind: "chapter"; id: string; label: string; prosePath: string | null }
    | { kind: "prose"; path: string; label: string; digest: string };
  const catalog = useWorkspaceStore(state => state.catalog);
  const currentId = useWorkspaceStore(state => state.currentId);
  const moduleOpen = useWorkspaceModulesStore(state => state.selected?.workspaceId === currentId);
  const extras = useRef<HTMLDetailsElement>(null);
  const renameInput = useRef<HTMLInputElement>(null);
  const [renaming, setRenaming] = useState<RenameTarget | null>(null);
  const [renameTitle, setRenameTitle] = useState("");
  const [renameError, setRenameError] = useState<string | null>(null);
  const [renamingBusy, setRenamingBusy] = useState(false);
  // 正在读哪份模块内容，左栏就展开到哪一条，不把当前阅读位置藏起来。
  useEffect(() => { if (moduleOpen && extras.current) extras.current.open = true; }, [moduleOpen, currentId]);
  const structureActive = selectedProsePath === STRUCTURE_PATH;
  const structureExists = Boolean(book?.structure_exists);
  const structureLoading = loading && !book;
  const chapters = book?.chapters ?? [];
  const unplanned = book?.unplanned_prose ?? [];
  // 章节与正文文件共用一格目录：作者看到的是"有几章"，而不是内部是否登记过计划。
  const chapterRows = [
    ...chapters.map(chapter => ({
      kind: "chapter" as const,
      key: `chapter:${chapter.id}`,
      order: chapter.order,
      chapter,
    })),
    ...unplanned.map((entry, index) => ({
      kind: "prose" as const,
      key: `prose:${entry.path}`,
      order: Math.max(0, (entry.chapter_number ?? chapters.length + index + 1) - 1),
      entry,
    })),
  ].sort((left, right) => left.order - right.order);
  const documents = [...catalog.characters, ...catalog.settings].filter(entry => entry.exists);

  function beginRename(chapter: Chapter) {
    setRenaming({
      kind: "chapter",
      id: chapter.id,
      label: `第${chapter.order + 1}章`,
      prosePath: chapter.prose_path,
    });
    setRenameTitle(chapter.title ?? "");
    setRenameError(null);
  }
  function beginRenameProse(entry: Prose) {
    // 兼容还没重新加载的旧工作台投影：缺少 name 时退回显示名，避免输入框为空。
    const initial = entry.name || entry.title || "";
    setRenaming({ kind: "prose", path: entry.path, label: entry.title, digest: entry.digest });
    setRenameTitle(initial);
    setRenameError(null);
  }
  async function submitRename() {
    if (!currentId || !renaming || renamingBusy) return;
    if (renaming.kind === "chapter" && !planRevision) return;
    setRenamingBusy(true);
    setRenameError(null);
    const writing = useWritingStore.getState();
    const message = renaming.kind === "chapter"
      ? await writing.renameChapter(currentId, renaming.id, renameTitle, planRevision!)
      : await writing.renameProse(currentId, renaming.path, renameTitle, renaming.digest);
    setRenamingBusy(false);
    if (message) {
      setRenameError(message);
      return;
    }
    // 正文首行可能随标题一起更新；正在读这一章时重新取一次文件。
    if (renaming.kind === "chapter" && renaming.prosePath && selectedChapterId === renaming.id) {
      await useWorkspaceStore.getState().openPath(renaming.prosePath);
    }
    setRenaming(null);
  }

  return <nav className="toc-pane writing-navigator" aria-label="作品目录">
    {/* 全书结构固定在滚动区上方：展开模块或滚动章节时始终可见。
        结构是写正文的依据与前提，所以没有建立时也保留入口并说明原因。 */}
    <div className="writing-nav-top">
      <section className="writing-structure" aria-label="全书结构">
        <button
          className={`toc-row writing-structure-row ${structureActive ? "on" : ""}`}
          aria-label="全书结构"
          aria-describedby="writing-structure-hint"
          aria-current={structureActive ? "page" : undefined}
          title={structureExists ? "打开全书结构全文" : "打开全书结构说明"}
          onClick={onStructure}
        >
          <ListTree size={16} strokeWidth={1.8} aria-hidden="true" />
          <span className="writing-structure-name">全书结构</span>
          <span className={`writing-structure-state ${structureExists ? "ready" : ""}`}>
            {structureLoading ? "读取中" : structureExists ? "已就绪" : "尚未建立"}
          </span>
        </button>
        <p className="writing-structure-hint" id="writing-structure-hint">
          {structureLoading
            ? "正在读取全书结构…"
            : structureExists
              ? "写正文的依据，模型每轮都会读取。"
              : "写正文前，要先有这本书的结构。"}
        </p>
        {!structureLoading && !structureExists && currentId && (
          <button type="button" className="writing-structure-action" onClick={onAskStructure}>
            在对话里建立结构
          </button>
        )}
      </section>
    </div>
    <div className="writing-nav-scroll">
      <details className="writing-nav-section" open>
        <summary className="writing-nav-section-title">章节</summary>
        <ul className="toc-group">
          {chapterRows.map(row => row.kind === "chapter"
            ? <li className="toc-line" key={row.key}>
              <button
                className={`toc-row px-2 py-1 ${selectedChapterId === row.chapter.id ? "on" : ""}`}
                aria-current={selectedChapterId === row.chapter.id ? "page" : undefined}
                onClick={() => onChapter(row.chapter.id)}
              >
                <span className="writing-nav-title">
                  第{row.chapter.order + 1}章{row.chapter.title ? ` · ${row.chapter.title}` : ""}
                </span>
              </button>
              <button
                type="button"
                className="toc-add"
                aria-label={`修改章节名 第${row.chapter.order + 1}章${row.chapter.title ? ` · ${row.chapter.title}` : ""}`}
                title="修改章节名"
                onClick={() => beginRename(row.chapter)}
              >
                <Pencil size={12} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </li>
            : <li className="toc-line" key={row.key}>
              <button
                className={`toc-row px-2 py-1 ${selectedProsePath === row.entry.path ? "on" : ""}`}
                aria-current={selectedProsePath === row.entry.path ? "page" : undefined}
                onClick={() => onProse(row.entry.path)}
              >
                <span className="writing-nav-title">{row.entry.title}</span>
              </button>
              <button
                type="button"
                className="toc-add"
                aria-label={`修改章节名 ${row.entry.title}`}
                title="修改章节名"
                onClick={() => beginRenameProse(row.entry)}
              >
                <Pencil size={12} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </li>)}
          {chapterRows.length === 0 && <li className="px-2 py-1 text-sm text-mute">
            {loading ? "正在读取…" : "还没有章节。"}
          </li>}
        </ul>
      </details>


      {documents.length > 0 && <section>
        <h2 className="toc-head">资料</h2>
        <ul className="toc-group">
          {documents.map(entry => <li className="toc-line" key={entry.path}>
            <button
              className={`toc-row px-2 py-1 ${selectedProsePath === entry.path ? "on" : ""}`}
              onClick={() => onProse(entry.path)}
            >
              <span className="writing-nav-title">{entry.display_title}</span>
            </button>
          </li>)}
        </ul>
      </section>}

      <details className="writing-nav-extras" ref={extras}>
        <summary>作品模块</summary>
        <div className="writing-nav-module-hint">
          <p>人物小传、故事背景等长期资料，可以让模型判断并创建。</p>
          <button type="button" onClick={onAskModules}>让模型补充资料</button>
        </div>
        {currentId && <WorkspaceModules workspaceId={currentId} />}
      </details>
    </div>
    <AppDialog
      title="修改章节名"
      open={Boolean(renaming)}
      onOpenChange={open => { if (!open && !renamingBusy) { setRenaming(null); setRenameError(null); } }}
      initialFocus={() => renameInput.current}
    >
      <div className="ui-action-list">
        <Field label="章节名">
          <input
            ref={renameInput}
            aria-label="章节名"
            maxLength={200}
            placeholder={renaming?.label ?? "章节名"}
            value={renameTitle}
            disabled={renamingBusy}
            onChange={event => setRenameTitle(event.target.value)}
            onKeyDown={event => { if (event.key === "Enter") void submitRename(); }}
          />
        </Field>
        <p className="ui-field-hint">{renaming?.kind === "prose" ? "文件名保持不变，只更新正文首行标题。" : "留空时显示为「第N章」；正文首行仍是旧标题时会一并更新。"}</p>
        {renameError && <p role="alert" className="ui-field-error">{renameError}</p>}
        <Button
          variant="primary"
          busy={renamingBusy}
          disabled={!renaming || (renaming.kind === "chapter" ? !planRevision : !(renameTitle ?? "").trim())}
          disabledReason={renaming?.kind === "prose" ? "填写新的章节名" : "先建立作品结构"}
          onClick={() => void submitRename()}
        >保存</Button>
      </div>
    </AppDialog>
  </nav>;
}

/**
 * 写作页左侧导航（1.0.9 P4）。
 *
 * 只有一项是「全书构思」，其后是按顺序排列的章节。未挂章节的正文与设定文件仍可阅读，
 * 但不混进章节写作流程。新建章节由服务端在同一事务里建立正文文件；左栏不放缺少
 * 模型入口的标题输入框。
 */

import { useEffect, useRef, useState } from "react";
import { BookOpenText, ListTree, Plus } from "lucide-react";

import type { WritingBookView } from "../../api/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import { useWorkspaceStore } from "../../state/workspace";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
import { Button } from "../ui/Button";
import WorkspaceModules from "../workspace/WorkspaceModules";

export default function WritingNavigator({
  book,
  selectedChapterId,
  selectedProsePath,
  loading,
  onBook,
  onStructure,
  onChapter,
  onProse,
  onCreateChapter,
  onImported,
  onChoose,
}: {
  book: WritingBookView | null;
  selectedChapterId: string | null;
  selectedProsePath: string | null;
  loading: boolean;
  onBook: () => void;
  /** 全书结构只读页（STRUCTURE.md，模型自维护）；作品意图存在后展示。 */
  onStructure: () => void;
  onChapter: (chapterId: string) => void;
  onProse: (path: string) => void;
  onCreateChapter: (title: string | null) => Promise<void>;
  onImported: () => void;
  onChoose?: () => void;
}) {
  const catalog = useWorkspaceStore(state => state.catalog);
  const currentId = useWorkspaceStore(state => state.currentId);
  const upload = useWorkspaceStore(state => state.upload);
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const extras = useRef<HTMLDetailsElement>(null);
  const moduleOpen = useWorkspaceModulesStore(state => state.selected?.workspaceId === currentId);
  // 正在读哪份模块内容，左栏就展开到哪一条，不把当前阅读位置藏起来。
  useEffect(() => { if (moduleOpen && extras.current) extras.current.open = true; }, [moduleOpen, currentId]);
  // 阅读区显示模块内容时，「全书构思」不再标记为当前项，点了才看得出切换生效。
  const bookActive = selectedChapterId === null && selectedProsePath === null && !moduleOpen;
  const structureActive = selectedProsePath === STRUCTURE_PATH;
  // 结构只读页跟作品意图一起出现：起步对话还没收束时没有可看的结构。
  const structureAvailable = Boolean(book);
  const chapters = book?.chapters ?? [];
  const unplanned = book?.unplanned_prose ?? [];
  const canCreateChapter = Boolean(book?.intent?.trim());
  const started = canCreateChapter;
  const documents = [...catalog.characters, ...catalog.settings].filter(entry => entry.exists);

  async function create() {
    if (creating) return;
    setCreating(true);
    try {
      await onCreateChapter(null);
    } finally {
      setCreating(false);
    }
  }

  return <nav className="toc-pane writing-navigator" aria-label="作品目录">
    <div className="writing-nav-scroll">
      <ul className="toc-group">
        <li className="toc-line">
          <button
            className={`toc-row px-2 py-1 ${bookActive ? "on" : ""}`}
            aria-current={bookActive ? "page" : undefined}
            onClick={onBook}
          >
            <BookOpenText size={16} strokeWidth={1.8} aria-hidden="true" />
            <span>{started ? "全书构思" : "起步"}</span>
          </button>
        </li>
        {structureAvailable && <li className="toc-line">
          <button
            className={`toc-row px-2 py-1 ${structureActive ? "on" : ""}`}
            aria-current={structureActive ? "page" : undefined}
            onClick={onStructure}
          >
            <ListTree size={16} strokeWidth={1.8} aria-hidden="true" />
            <span>全书结构</span>
          </button>
        </li>}
      </ul>

      <section>
        <h2 className="toc-head">章节</h2>
        <ul className="toc-group">
          {chapters.map(chapter => (
            <li className="toc-line" key={chapter.id}>
              <button
                className={`toc-row px-2 py-1 ${selectedChapterId === chapter.id ? "on" : ""}`}
                aria-current={selectedChapterId === chapter.id ? "page" : undefined}
                onClick={() => onChapter(chapter.id)}
              >
                <span className="writing-nav-chapter-copy">
                  <span>第{chapter.order + 1}章{chapter.title ? ` · ${chapter.title}` : ""}</span>
                  <small>{chapter.summary || "还没有信息变化"}</small>
                </span>
                <i
                  className={`writing-nav-state ${chapter.prose_path ? "started" : "draft"}`}
                  title={chapter.prose_path ? "有正文文件" : "缺少正文文件"}
                  aria-label={chapter.prose_path ? "有正文文件" : "缺少正文文件"}
                />
              </button>
            </li>
          ))}
          {chapters.length === 0 && <li className="px-2 py-1 text-sm text-mute">{loading ? "正在读取…" : "还没有章节。"}</li>}
        </ul>
        <Button
          className="writing-nav-create"
          size="compact"
          onClick={() => void create()}
          busy={creating}
          disabled={!canCreateChapter}
          disabledReason="先在对话里完成起步"
        >
          <Plus size={15} strokeWidth={2} aria-hidden="true" />{canCreateChapter ? "新建章节" : "先在对话里起步"}
        </Button>
      </section>

      {unplanned.length > 0 && <section>
        <h2 className="toc-head">未挂章节的正文</h2>
        <ul className="toc-group">
          {unplanned.map(entry => <li className="toc-line" key={entry.path}>
            <button
              className={`toc-row px-2 py-1 ${selectedProsePath === entry.path ? "on" : ""}`}
              title={entry.path}
              onClick={() => onProse(entry.path)}
            >
              <span className="writing-nav-title">{entry.title}</span>
              <span className="writing-nav-order">只读</span>
            </button>
          </li>)}
        </ul>
      </section>}

      {documents.length > 0 && <details className="writing-nav-documents">
        <summary>设定与人物</summary>
        <ul className="toc-group">
          {documents.map(entry => <li className="toc-line" key={entry.path}>
            <button
              className={`toc-row px-2 py-1 ${selectedProsePath === entry.path ? "on" : ""}`}
              title={entry.path}
              onClick={() => onProse(entry.path)}
            >
              <span className="writing-nav-title">{entry.display_title}</span>
            </button>
          </li>)}
        </ul>
      </details>}

      <details className="writing-nav-extras" ref={extras}>
        <summary>模块与导入</summary>
        {currentId && <WorkspaceModules workspaceId={currentId} onChoose={onChoose} />}
        <div className="writing-nav-import">
          <Button size="compact" variant="quiet" busy={importing} onClick={() => fileInput.current?.click()}>导入正文</Button>
          <input
            ref={fileInput}
            type="file"
            accept=".md,.markdown,.txt"
            className="hidden"
            onChange={event => {
              const chosen = event.target.files?.[0];
              event.target.value = "";
              if (!chosen) return;
              setImporting(true);
              void upload(chosen).finally(() => { setImporting(false); onImported(); });
            }}
          />
        </div>
      </details>
    </div>
  </nav>;
}

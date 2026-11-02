/**
 * 写作页左侧导航（1.0.9 P4）。
 *
 * 只有一项是「全书构思」，其后是按顺序排列的章节。没有挂构思的正文与设定文件仍可阅读，
 * 但不混进章节写作流程。新建章节先建立无标题的构思壳，正文路径与确认状态由服务端
 * 在开始事务里生成；这样左栏不再放一个缺少模型入口的标题输入框。
 */

import { useRef, useState } from "react";
import { BookOpenText, Plus } from "lucide-react";

import type { WritingBookView } from "../../api/writing";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import WorkspaceModules from "../workspace/WorkspaceModules";

export default function WritingNavigator({
  book,
  selectedChapterId,
  selectedProsePath,
  loading,
  onBook,
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
  const chapters = book?.chapters ?? [];
  const unplanned = book?.unplanned_prose ?? [];
  const canCreateChapter = Boolean(book?.intent?.trim());
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
            className={`toc-row px-2 py-1 ${selectedChapterId === null && selectedProsePath === null ? "on" : ""}`}
            aria-current={selectedChapterId === null && selectedProsePath === null ? "page" : undefined}
            onClick={onBook}
          >
            <BookOpenText size={16} strokeWidth={1.8} aria-hidden="true" />
            <span>全书构思</span>
          </button>
        </li>
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
                  <small>{chapter.summary || chapter.intent || "尚未构思"}</small>
                </span>
                <i
                  className={`writing-nav-state ${chapter.prose_path ? "started" : chapter.confirmed ? "confirmed" : "draft"}`}
                  title={chapter.prose_path ? "已开始写作" : chapter.confirmed ? "构思已确认" : "构思待确认"}
                  aria-label={chapter.prose_path ? "已开始写作" : chapter.confirmed ? "构思已确认" : "构思待确认"}
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
          disabledReason="先完成并保存全书构思"
        >
          <Plus size={15} strokeWidth={2} aria-hidden="true" />{canCreateChapter ? "新建章节" : "先完成全书构思"}
        </Button>
      </section>

      {unplanned.length > 0 && <section>
        <h2 className="toc-head">未挂构思的正文</h2>
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

      <details className="writing-nav-extras">
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

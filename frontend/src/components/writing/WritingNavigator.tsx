/** 写作页只读目录（v1.2）。内容修改只从对话发起。 */

import { BookOpenText, ListTree } from "lucide-react";

import type { WritingBookView } from "../../api/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import { useWorkspaceStore } from "../../state/workspace";

export default function WritingNavigator({
  book,
  selectedChapterId,
  selectedProsePath,
  loading,
  onBook,
  onStructure,
  onChapter,
  onProse,
}: {
  book: WritingBookView | null;
  selectedChapterId: string | null;
  selectedProsePath: string | null;
  loading: boolean;
  onBook: () => void;
  onStructure: () => void;
  onChapter: (chapterId: string) => void;
  onProse: (path: string) => void;
}) {
  const catalog = useWorkspaceStore(state => state.catalog);
  const bookActive = selectedChapterId === null && selectedProsePath === null;
  const structureActive = selectedProsePath === STRUCTURE_PATH;
  const chapters = book?.chapters ?? [];
  const unplanned = book?.unplanned_prose ?? [];
  const documents = [...catalog.characters, ...catalog.settings].filter(entry => entry.exists);

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
            <span>稿纸</span>
          </button>
        </li>
        {book?.structure_exists && <li className="toc-line">
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
          {chapters.map(chapter => <li className="toc-line" key={chapter.id}>
            <button
              className={`toc-row px-2 py-1 ${selectedChapterId === chapter.id ? "on" : ""}`}
              aria-current={selectedChapterId === chapter.id ? "page" : undefined}
              onClick={() => onChapter(chapter.id)}
            >
              <span className="writing-nav-title">
                第{chapter.order + 1}章{chapter.title ? ` · ${chapter.title}` : ""}
              </span>
            </button>
          </li>)}
          {chapters.length === 0 && <li className="px-2 py-1 text-sm text-mute">
            {loading ? "正在读取…" : "还没有章节。"}
          </li>}
        </ul>
      </section>

      {unplanned.length > 0 && <section>
        <h2 className="toc-head">其他正文</h2>
        <ul className="toc-group">
          {unplanned.map(entry => <li className="toc-line" key={entry.path}>
            <button
              className={`toc-row px-2 py-1 ${selectedProsePath === entry.path ? "on" : ""}`}
              onClick={() => onProse(entry.path)}
            >
              <span className="writing-nav-title">{entry.title}</span>
            </button>
          </li>)}
        </ul>
      </section>}

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
    </div>
  </nav>;
}

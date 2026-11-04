/** 写作页只读目录（v1.2）。内容修改只从对话发起；模块只在此处装入和阅读。 */

import { useEffect, useRef } from "react";
import { BookOpenText, ListTree } from "lucide-react";

import type { WritingBookView } from "../../api/writing";
import { STRUCTURE_PATH } from "../../api/structure";
import { useWorkspaceStore } from "../../state/workspace";
import { useWorkspaceModulesStore } from "../../state/workspaceModules";
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
  onAskModules,
}: {
  book: WritingBookView | null;
  selectedChapterId: string | null;
  selectedProsePath: string | null;
  loading: boolean;
  onBook: () => void;
  onStructure: () => void;
  onChapter: (chapterId: string) => void;
  onProse: (path: string) => void;
  /** 把“让模型补充资料”的请求填进对话草稿，不直接发送。 */
  onAskModules: () => void;
}) {
  const catalog = useWorkspaceStore(state => state.catalog);
  const currentId = useWorkspaceStore(state => state.currentId);
  const moduleOpen = useWorkspaceModulesStore(state => state.selected?.workspaceId === currentId);
  const extras = useRef<HTMLDetailsElement>(null);
  // 正在读哪份模块内容，左栏就展开到哪一条，不把当前阅读位置藏起来。
  useEffect(() => { if (moduleOpen && extras.current) extras.current.open = true; }, [moduleOpen, currentId]);
  // 阅读区显示模块内容时，「稿纸」不再标记为当前项，点了才看得出切换生效。
  const bookActive = selectedChapterId === null && selectedProsePath === null && !moduleOpen;
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

      <details className="writing-nav-extras" ref={extras}>
        <summary>作品模块</summary>
        <div className="writing-nav-module-hint">
          <p>人物小传、故事背景等长期资料，可以让模型判断并创建。</p>
          <button type="button" onClick={onAskModules}>让模型补充资料</button>
        </div>
        {currentId && <WorkspaceModules workspaceId={currentId} />}
      </details>
    </div>
  </nav>;
}

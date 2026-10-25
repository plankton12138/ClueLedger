import { useLayoutEffect, useRef, type ReactNode } from "react";
import { useWorkspaceStore } from "../../state/workspace";
import { useConversationsStore } from "../../state/conversations";
import { usePagePositions } from "../../state/pagePositions";
import { pageHash } from "../../state/navigation";
import CharacterBadges from "./CharacterBadges";

function TocLine({
  path,
  active,
  onOpen,
  onReference,
  children,
}: {
  path: string;
  active: boolean;
  onOpen: () => void;
  onReference: () => void;
  children: ReactNode;
}) {
  return (
    <li className="toc-line">
      <button
        className={`toc-row px-2 py-1 text-[12px] ${active ? "on" : ""}`}
        title={path}
        onClick={onOpen}
      >
        {children}
      </button>
      <button
        type="button"
        className="toc-add"
        aria-label={`加入对话 ${path}`}
        title="加入这一轮的参考"
        onClick={onReference}
      >
        ＋
      </button>
    </li>
  );
}

export default function WorkspaceToc({ onChoose }: { onChoose?: () => void }) {
  const { bindings, currentId, catalog, selectedPath, search, openPath, runSearch, clearSearch, upload } = useWorkspaceStore();
  const current = bindings.find(item => item.id === currentId);
  const positionKey = `${currentId}:toc`;
  const query = usePagePositions(state => state.queries[positionKey] ?? "");
  const setQuery = (value: string) => usePagePositions.getState().query(positionKey, value);
  const addReference = useConversationsStore(state => state.addReference);
  const fileInput = useRef<HTMLInputElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = usePagePositions.getState().offsets[positionKey] ?? 0;
  }, [positionKey, catalog]);
  const choose = (path: string) => { void openPath(path); onChoose?.(); };
  return (
        <nav className="toc-pane flex h-full min-h-0 min-w-0 flex-col" aria-label="作品目录">
          <div className="flex flex-none items-center gap-2 px-3 py-2">
            <input
              aria-label="搜索正文与设定"
              className="well-input min-w-0 flex-1 px-2 py-1 text-sm"
              placeholder="搜索…"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") void runSearch(query);
                if (event.key === "Escape") {
                  setQuery("");
                  clearSearch();
                }
              }}
            />
            <button
              className="btn btn-gray px-2 py-1 text-sm"
              onClick={() => {
                if (query.trim()) void runSearch(query);
                else clearSearch();
              }}
            >
              查找
            </button>
          </div>

          <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto px-2 pb-3" onScroll={event => { if (event.currentTarget.clientHeight && (catalog.chapters.length || catalog.characters.length || catalog.settings.length)) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
            {search ? (
              <section>
                <div className="flex items-center gap-2 px-1 py-2 text-sm text-sub">
                  <span className="flex-1">搜索结果 {search.hits.length} 条</span>
                  <button
                    className="underline text-blue"
                    onClick={() => {
                      setQuery("");
                      clearSearch();
                    }}
                  >
                    返回目录
                  </button>
                </div>
                <ul className="space-y-1">
                  {search.hits.map((hit) => (
                    <li key={`${hit.path}:${hit.line}`}>
                      <button
                        className="toc-row px-2 py-1 text-sm leading-5"
                        onClick={() => choose(hit.path)}
                      >
                        <div className="truncate text-mute">
                          {hit.path} · 第 {hit.line} 行
                        </div>
                        <div className="truncate">{hit.text}</div>
                      </button>
                    </li>
                  ))}
                  {search.hits.length === 0 && (
                    <li className="px-2 py-1 text-sm text-mute">没有找到。</li>
                  )}
                </ul>
                {search.unreadable.length > 0 && (
                  <p className="mt-2 px-1 text-xs text-amber">
                    无法读取：{search.unreadable.join("、")}
                  </p>
                )}
              </section>
            ) : (
              <>
                <section>
                  <div className="flex items-center gap-2 px-1 py-2">
                    <h2 className="toc-head flex-1 text-sm font-bold tracking-wide">正文</h2>
                    <button
                      className="text-xs underline text-blue"
                      onClick={() => fileInput.current?.click()}
                    >
                      导入
                    </button>
                    <input
                      ref={fileInput}
                      type="file"
                      accept=".md,.markdown,.txt"
                      className="hidden"
                      onChange={(event) => {
                        const chosen = event.target.files?.[0];
                        if (chosen) void upload(chosen);
                        event.target.value = "";
                      }}
                    />
                  </div>
                  <ul className="space-y-0.5">
                    {catalog.chapters.map((entry) => (
                      <TocLine
                        key={entry.path}
                        path={entry.path}
                        active={selectedPath === entry.path}
                        onOpen={() => choose(entry.path)}
                        onReference={() => addReference(entry.path)}
                      >
                        {entry.display_title}
                      </TocLine>
                    ))}
                    {catalog.chapters.length === 0 && (
                      <li className="px-2 py-1 text-sm text-mute">
                        还没有章节。对右边说「写第一章」。
                      </li>
                    )}
                  </ul>
                </section>

                <section className="mt-3">
                  <h2 className="toc-head px-1 py-2 text-sm font-bold tracking-wide">设定集</h2>
                  <details open className="mb-1">
                    <summary className="px-2 py-1 text-sm font-bold">角色小传</summary>
                    <ul className="ml-2 space-y-0.5">
                      {catalog.characters.map((entry) => (
                        <TocLine
                          key={entry.path}
                          path={entry.path}
                          active={selectedPath === entry.path}
                          onOpen={() => choose(entry.path)}
                          onReference={() => addReference(entry.path)}
                        >
                          {entry.display_title}
                          <CharacterBadges roles={entry.roles} compact />
                        </TocLine>
                      ))}
                      {catalog.characters.length === 0 && (
                        <li className="px-2 py-1 text-sm text-mute">
                          还没有角色。可在对话中要求创建人物小传。
                        </li>
                      )}
                    </ul>
                  </details>
                  <ul className="space-y-0.5">
                    {catalog.settings.map((entry) => (
                      <TocLine
                        key={entry.path}
                        path={entry.path}
                        active={selectedPath === entry.path}
                        onOpen={() => choose(entry.path)}
                        onReference={() => addReference(entry.path)}
                      >
                        {entry.display_title}
                        {!entry.exists && (
                          <span className="ml-2 text-xs text-mute">（缺失）</span>
                        )}
                      </TocLine>
                    ))}
                  </ul>
                </section>
              </>
            )}
          </div>

          <a className="ui-library-entry" href={pageHash({ kind: "library", workspaceContextId: currentId ?? undefined })}>作品模块</a>
          <div className="toc-foot flex-none break-words px-3 py-2 text-xs">
            {current ? current.path : "未打开目录"}
          </div>
        </nav>
  );
}

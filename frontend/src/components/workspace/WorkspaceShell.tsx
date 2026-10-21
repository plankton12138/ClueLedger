/**
 * 工作区壳层（0.4 V04-4）：顶栏 + 章节目录 + 稿纸 + 对话栏。
 *
 * 数据全部来自 `/api/file-workspaces`：目录、文件、事件与 Git。外部编辑不会自动推送，
 * 切换回窗口或点刷新时重新扫描目录——首版不做文件系统监听。
 *
 * 模型设置是应用级状态（V04-9）：它不随作品打开/关闭，顶栏入口在没有作品时也能用；
 * 窗口重新获得焦点只重读本机配置，不自动调用供应商。
 */

import { useEffect, useRef, useState } from "react";

import { useModelsStore } from "../../state/models";
import { useWorkspaceStore } from "../../state/workspace";
import ModelSettingsDialog from "../models/ModelSettingsDialog";
import ChangesDrawer from "./ChangesDrawer";
import ConversationPanel from "./ConversationPanel";
import DirectoryDialog from "./DirectoryDialog";
import Paper from "./Paper";

export default function WorkspaceShell() {
  const bindings = useWorkspaceStore((state) => state.bindings);
  const currentId = useWorkspaceStore((state) => state.currentId);
  const catalog = useWorkspaceStore((state) => state.catalog);
  const file = useWorkspaceStore((state) => state.file);
  const selectedPath = useWorkspaceStore((state) => state.selectedPath);
  const search = useWorkspaceStore((state) => state.search);
  const loading = useWorkspaceStore((state) => state.loading);
  const error = useWorkspaceStore((state) => state.error);
  const notice = useWorkspaceStore((state) => state.notice);
  const init = useWorkspaceStore((state) => state.init);
  const selectWorkspace = useWorkspaceStore((state) => state.selectWorkspace);
  const refreshAll = useWorkspaceStore((state) => state.refreshAll);
  const openPath = useWorkspaceStore((state) => state.openPath);
  const runSearch = useWorkspaceStore((state) => state.runSearch);
  const clearSearch = useWorkspaceStore((state) => state.clearSearch);
  const upload = useWorkspaceStore((state) => state.upload);
  const openDrawer = useWorkspaceStore((state) => state.openDrawer);
  const dismissError = useWorkspaceStore((state) => state.dismissError);
  const dismissNotice = useWorkspaceStore((state) => state.dismissNotice);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const current = bindings.find((binding) => binding.id === currentId);

  const initModels = useModelsStore((state) => state.init);
  const openModelSettings = useModelsStore((state) => state.openSettings);

  useEffect(() => {
    void init();
    void initModels();
  }, [init, initModels]);

  // 外部编辑不推送：切回窗口时重新扫描目录与当前文件，并重读模型配置。
  useEffect(() => {
    const onFocus = () => {
      if (useWorkspaceStore.getState().currentId) void refreshAll();
      void useModelsStore.getState().refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshAll]);

  return (
    <div className="flex h-screen flex-col">
      <header className="topbar flex min-h-12 flex-none items-center gap-2 px-3 sm:gap-3 sm:px-5">
        <div className="mark grid h-10 w-10 flex-none place-items-center font-serif text-2xl font-bold">
          H
        </div>
        <div className="hidden leading-tight sm:block">
          <div className="font-serif text-xl font-bold tracking-wide">Hastings</div>
          <div className="text-[9px] tracking-[0.25em] text-white/85">MARKDOWN WORKSPACE</div>
        </div>
        <div className="hidden h-8 w-px bg-[#355f7f] shadow-[1px_0_#c4d9e8] sm:block" />
        <select
          aria-label="选择作品目录"
          className="project-picker h-[34px] min-w-0 max-w-56 rounded px-2 text-xs font-bold text-white"
          value={currentId ?? ""}
          onChange={(event) => {
            if (event.target.value) void selectWorkspace(event.target.value);
          }}
        >
          <option value="">（未打开目录）</option>
          {bindings.map((binding) => (
            <option key={binding.id} value={binding.id}>
              {binding.name}
              {binding.exists ? "" : "（目录已不在）"}
            </option>
          ))}
        </select>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          onClick={() => setDialogOpen(true)}
        >
          打开 / 新建
        </button>
        <div className="flex-1" />
        <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={openModelSettings}>
          模型设置
        </button>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={!currentId}
          onClick={() => void refreshAll()}
        >
          刷新
        </button>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={!currentId}
          onClick={() => void openDrawer()}
        >
          改动与历史
        </button>
      </header>

      {error && (
        <div
          role="alert"
          className="flex flex-none items-center gap-3 border-b border-red-edge bg-red-bg px-5 py-2 text-xs text-red"
        >
          <span className="min-w-0 flex-1">{error}</span>
          <button className="btn btn-gray shrink-0 px-3 py-1" onClick={dismissError}>
            知道了
          </button>
        </div>
      )}
      {notice && (
        <div className="flex flex-none items-center gap-3 border-b border-[#cfc7ae] bg-[#f3ecd8] px-5 py-2 text-xs text-[#4b4737]">
          <span className="min-w-0 flex-1">{notice}</span>
          <button className="underline" onClick={dismissNotice}>
            知道了
          </button>
        </div>
      )}

      <main className="grid min-h-0 flex-1 grid-cols-[260px_minmax(0,1fr)_380px] overflow-hidden">
        <nav className="flex min-h-0 flex-col border-r border-[#355f7f] bg-[#12354f] text-white">
          <div className="flex flex-none items-center gap-2 px-3 py-2">
            <input
              aria-label="搜索正文与设定"
              className="min-w-0 flex-1 rounded bg-[#1b4666] px-2 py-1 text-[11px] text-white placeholder:text-white/35"
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
              className="btn btn-gray px-2 py-1 text-[11px]"
              onClick={() => {
                if (query.trim()) void runSearch(query);
                else clearSearch();
              }}
            >
              查找
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-auto px-2 pb-3">
            {search ? (
              <section>
                <div className="flex items-center gap-2 px-1 py-2 text-[11px] text-white/60">
                  <span className="flex-1">搜索结果 {search.hits.length} 条</span>
                  <button
                    className="underline"
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
                        className="w-full rounded px-2 py-1 text-left text-[11px] leading-5 hover:bg-[#1b4666]"
                        onClick={() => void openPath(hit.path)}
                      >
                        <div className="truncate text-white/50">
                          {hit.path} · 第 {hit.line} 行
                        </div>
                        <div className="truncate">{hit.text}</div>
                      </button>
                    </li>
                  ))}
                  {search.hits.length === 0 && (
                    <li className="px-2 py-1 text-[11px] text-white/50">没有找到。</li>
                  )}
                </ul>
                {search.unreadable.length > 0 && (
                  <p className="mt-2 px-1 text-[10px] text-[#f0c48a]">
                    无法读取：{search.unreadable.join("、")}
                  </p>
                )}
              </section>
            ) : (
              <>
                <section>
                  <div className="flex items-center gap-2 px-1 py-2">
                    <h2 className="flex-1 text-[11px] font-bold tracking-wide text-white/70">
                      正文
                    </h2>
                    <button
                      className="text-[10px] underline text-white/60"
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
                      <li key={entry.path}>
                        <button
                          className={`w-full truncate rounded px-2 py-1 text-left text-[12px] ${
                            selectedPath === entry.path ? "bg-[#2b5f85]" : "hover:bg-[#1b4666]"
                          }`}
                          title={entry.path}
                          onClick={() => void openPath(entry.path)}
                        >
                          {entry.title}
                        </button>
                      </li>
                    ))}
                    {catalog.chapters.length === 0 && (
                      <li className="px-2 py-1 text-[11px] text-white/45">
                        还没有章节。对右边说「写第一章」。
                      </li>
                    )}
                  </ul>
                </section>

                <section className="mt-3">
                  <h2 className="px-1 py-2 text-[11px] font-bold tracking-wide text-white/70">
                    设定集
                  </h2>
                  <ul className="space-y-0.5">
                    {catalog.settings.map((entry) => (
                      <li key={entry.path}>
                        <button
                          className={`w-full truncate rounded px-2 py-1 text-left text-[12px] ${
                            selectedPath === entry.path ? "bg-[#2b5f85]" : "hover:bg-[#1b4666]"
                          }`}
                          onClick={() => void openPath(entry.path)}
                        >
                          {entry.title}
                          {!entry.exists && (
                            <span className="ml-2 text-[10px] text-white/40">（缺失）</span>
                          )}
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              </>
            )}
          </div>

          <div className="flex-none border-t border-[#355f7f] px-3 py-2 text-[10px] text-white/45">
            {current ? current.path : "未打开目录"}
          </div>
        </nav>

        <section className="min-h-0 overflow-hidden bg-[#12354f]">
          {currentId ? (
            <Paper file={file} loading={loading} />
          ) : (
            <div className="grid h-full place-items-center text-center text-sm text-white/70">
              <div>
                <p className="font-serif text-lg text-white/90">打开一个作品目录</p>
                <p className="mt-2 text-xs">作品就是一个文件夹：正文/ 与 设定集/，历史用 Git。</p>
                <button
                  className="btn btn-gold mt-4 px-4 py-1.5 text-xs"
                  onClick={() => setDialogOpen(true)}
                >
                  打开 / 新建
                </button>
              </div>
            </div>
          )}
        </section>

        {currentId ? (
          <ConversationPanel />
        ) : (
          <aside className="border-l border-[#355f7f] bg-[#12354f]" />
        )}
      </main>

      <DirectoryDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
      <ModelSettingsDialog />
      <ChangesDrawer />
    </div>
  );
}

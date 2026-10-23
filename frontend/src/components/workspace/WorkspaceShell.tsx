/**
 * 工作区壳层（0.4 V04-4 / V04-11）：顶栏 + 章节目录 + 稿纸 + 对话栏。
 *
 * 数据全部来自 `/api/file-workspaces`：目录、文件、事件与 Git。外部编辑不会自动推送，
 * 切换回窗口或点刷新时重新扫描目录——首版不做文件系统监听。
 *
 * 三栏宽度与对话栏显隐是界面偏好（`state/layout.ts`），存在浏览器本地：拖动栏间把手改宽度，
 * 双击复位；顶栏「对话」开关收起右栏，稿纸占满剩余空间。收起只是不渲染面板，
 * 会话状态在 `state/conversations.ts`，重新打开时草稿、事件与运行状态都还在。
 *
 * 模型设置是应用级状态（V04-9）：它不随作品打开/关闭，顶栏入口在没有作品时也能用；
 * 窗口重新获得焦点只重读本机配置，不自动调用供应商。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";

import { useConversationsStore } from "../../state/conversations";
import { CHAT, SIDEBAR, useLayoutStore } from "../../state/layout";
import { useModelsStore } from "../../state/models";
import { navigate, useWorkspacePage } from "../../state/navigation";
import { useWorkspaceStore } from "../../state/workspace";
import StoryMaterialsPage from "../materials/StoryMaterialsPage";
import ModelSettingsDialog from "../models/ModelSettingsDialog";
import ActivityPanel from "./ActivityPanel";
import FontSizeControl from "./FontSizeControl";
import CharacterBadges from "./CharacterBadges";
import ConversationPanel from "./ConversationPanel";
import DirectoryDialog from "./DirectoryDialog";
import Paper from "./Paper";
import ResizeHandle from "./ResizeHandle";

/**
 * 目录行：点标题打开文件，点右侧「＋」把它加入这一轮的参考。
 *
 * 打开与加入是两件事：只打开不会读原文、也不产生修改授权；加入参考只说明「这一轮
 * 可以参考它」，同样不授权写。按钮放在行内而不是右键菜单里，键盘也能到达。
 */
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
  const pruneMissing = useWorkspaceStore((state) => state.pruneMissing);
  const refreshAll = useWorkspaceStore((state) => state.refreshAll);
  const openPath = useWorkspaceStore((state) => state.openPath);
  const runSearch = useWorkspaceStore((state) => state.runSearch);
  const clearSearch = useWorkspaceStore((state) => state.clearSearch);
  const upload = useWorkspaceStore((state) => state.upload);
  const openDrawer = useWorkspaceStore((state) => state.openDrawer);
  const dismissError = useWorkspaceStore((state) => state.dismissError);
  const dismissNotice = useWorkspaceStore((state) => state.dismissNotice);
  // 目录里的「加入对话」写的是会话草稿的参考列表：与输入区的选择器同一份状态。
  const addReference = useConversationsStore((state) => state.addReference);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [query, setQuery] = useState("");
  const page = useWorkspacePage();
  const materials = page?.kind === "materials" ? page : null;
  const fileInput = useRef<HTMLInputElement>(null);
  const current = bindings.find((binding) => binding.id === currentId);
  const staleCount = bindings.filter((binding) => !binding.exists).length;

  const initModels = useModelsStore((state) => state.init);
  const openModelSettings = useModelsStore((state) => state.openSettings);

  const sidebarWidth = useLayoutStore((state) => state.sidebarWidth);
  const chatWidth = useLayoutStore((state) => state.chatWidth);
  const chatVisible = useLayoutStore((state) => state.chatVisible);
  const setSidebarWidth = useLayoutStore((state) => state.setSidebarWidth);
  const setChatWidth = useLayoutStore((state) => state.setChatWidth);
  const toggleChat = useLayoutStore((state) => state.toggleChat);

  useEffect(() => {
    void init();
    void initModels();
  }, [init, initModels]);

  // 页面位置与作品选择互相对齐：URL 里的作品未登记时回到当前作品，不把它当读取指令。
  useEffect(() => {
    if (!bindings.length) return;
    const target = page?.workspaceId;
    if (target && target !== currentId && bindings.some((item) => item.id === target)) {
      void selectWorkspace(target);
      return;
    }
    if (currentId && target !== currentId) {
      navigate({
        kind: "writing",
        workspaceId: currentId,
      });
    }
  }, [page, bindings, currentId, selectWorkspace]);

  // 外部编辑不推送：切回窗口时重新扫描目录与当前文件，并重读模型配置。
  useEffect(() => {
    const onFocus = () => {
      if (useWorkspaceStore.getState().currentId) {
        void refreshAll();
        // 运行状态与列表以服务端为准：别的窗口受理的轮次也能在这里看到。
        void useConversationsStore.getState().refreshList();
      }
      void useModelsStore.getState().refresh();
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refreshAll]);

  return (
    <div className="flex h-screen flex-col">
      <header className="topbar flex min-h-12 flex-none flex-wrap items-center gap-2 px-3 sm:gap-3 sm:px-5">
        <div className="mark grid h-10 w-10 flex-none place-items-center font-serif text-2xl font-bold">
          C
        </div>
        <div className="hidden leading-tight sm:block">
          <div className="font-serif text-xl font-bold tracking-wide">ClueLedger</div>
          <div className="text-[9px] tracking-[0.25em] text-white/85">MARKDOWN WORKSPACE</div>
        </div>
        <div className="hidden h-8 w-px bg-[#355f7f] shadow-[1px_0_#c4d9e8] sm:block" />
        <select
          aria-label="选择作品目录"
          className="project-picker h-[34px] min-w-0 max-w-56 rounded px-2 text-xs font-bold text-white"
          value={currentId ?? ""}
          onChange={(event) => {
            if (!event.target.value) return;
            const id = event.target.value;
            void selectWorkspace(id).then(() => navigate({ kind: "writing", workspaceId: id }));
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
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={!currentId}
          title="重新读取目录、版本与当前文件；不调用模型"
          onClick={() => void refreshAll()}
        >
          重新读取文件
        </button>
        {staleCount > 0 && (
          <button
            className="btn btn-gray whitespace-nowrap px-2 py-1 text-[11px]"
            title="只移除登记，不删除磁盘上的目录"
            onClick={() => void pruneMissing()}
          >
            清理失效（{staleCount}）
          </button>
        )}
        <div className="flex-1" />
        <div className="seg flex-none text-[11px]">
          <button
            className={!materials ? "on px-2 py-1" : "px-2 py-1"}
            aria-pressed={!materials}
            disabled={!currentId}
            onClick={() => currentId && navigate({ kind: "writing", workspaceId: currentId })}
          >
            写作
          </button>
          <button
            className={materials ? "on px-2 py-1" : "px-2 py-1"}
            aria-pressed={Boolean(materials)}
            disabled={!currentId}
            onClick={() =>
              currentId &&
              navigate({
                kind: "materials",
                workspaceId: currentId,
                section: materials?.section ?? "chapters",
                chapter: materials?.chapter ?? null,
              })
            }
          >
            故事资料
          </button>
        </div>
        <FontSizeControl />
        <div className={`seg flex-none text-[11px] ${chatVisible ? "" : "dim"}`}>
          <button
            className={chatVisible ? "on px-2 py-1" : "px-2 py-1"}
            aria-pressed={chatVisible}
            disabled={!currentId}
            onClick={toggleChat}
            title="显示或隐藏对话栏"
          >
            对话
          </button>
        </div>
        <button className="btn btn-gray px-2 py-1 text-[11px]" onClick={openModelSettings}>
          模型设置
        </button>
        <button
          className="btn btn-gray px-2 py-1 text-[11px]"
          disabled={!currentId}
          onClick={() => void openDrawer()}
        >
          活动记录
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
        <div className="flex flex-none items-center gap-3 border-b border-amber-edge bg-amber-bg px-5 py-2 text-xs text-amber">
          <span className="min-w-0 flex-1">{notice}</span>
          <button className="underline" onClick={dismissNotice}>
            知道了
          </button>
        </div>
      )}

      {materials && currentId ? (
        <main className="flex min-h-0 flex-1 overflow-hidden">
          <StoryMaterialsPage
            workspaceId={currentId}
            section={materials.section}
            chapter={materials.chapter}
            entryId={materials.entryId}
            onNavigate={navigate}
            onOpenSource={(path) => {
              void openPath(path).then(() =>
                navigate({ kind: "writing", workspaceId: currentId }),
              );
            }}
            onTakeToChat={(path) => {
              addReference(path);
              void openPath(path).then(() =>
                navigate({ kind: "writing", workspaceId: currentId }),
              );
            }}
            onOpenActivity={() => void openDrawer()}
          />
        </main>
      ) : (
      <main className="flex min-h-0 flex-1 overflow-hidden">
        <nav className="toc-pane flex min-h-0 flex-none flex-col" style={{ width: sidebarWidth }}>
          <div className="flex flex-none items-center gap-2 px-3 py-2">
            <input
              aria-label="搜索正文与设定"
              className="well-input min-w-0 flex-1 px-2 py-1 text-[11px]"
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
                <div className="flex items-center gap-2 px-1 py-2 text-[11px] text-sub">
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
                        className="toc-row px-2 py-1 text-[11px] leading-5"
                        onClick={() => void openPath(hit.path)}
                      >
                        <div className="truncate text-mute">
                          {hit.path} · 第 {hit.line} 行
                        </div>
                        <div className="truncate">{hit.text}</div>
                      </button>
                    </li>
                  ))}
                  {search.hits.length === 0 && (
                    <li className="px-2 py-1 text-[11px] text-mute">没有找到。</li>
                  )}
                </ul>
                {search.unreadable.length > 0 && (
                  <p className="mt-2 px-1 text-[10px] text-amber">
                    无法读取：{search.unreadable.join("、")}
                  </p>
                )}
              </section>
            ) : (
              <>
                <section>
                  <div className="flex items-center gap-2 px-1 py-2">
                    <h2 className="toc-head flex-1 text-[11px] font-bold tracking-wide">正文</h2>
                    <button
                      className="text-[10px] underline text-blue"
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
                        onOpen={() => void openPath(entry.path)}
                        onReference={() => addReference(entry.path)}
                      >
                        {entry.display_title}
                      </TocLine>
                    ))}
                    {catalog.chapters.length === 0 && (
                      <li className="px-2 py-1 text-[11px] text-mute">
                        还没有章节。对右边说「写第一章」。
                      </li>
                    )}
                  </ul>
                </section>

                <section className="mt-3">
                  <h2 className="toc-head px-1 py-2 text-[11px] font-bold tracking-wide">设定集</h2>
                  <details open className="mb-1">
                    <summary className="px-2 py-1 text-[12px] font-bold">角色小传</summary>
                    <ul className="ml-2 space-y-0.5">
                      {catalog.characters.map((entry) => (
                        <TocLine
                          key={entry.path}
                          path={entry.path}
                          active={selectedPath === entry.path}
                          onOpen={() => void openPath(entry.path)}
                          onReference={() => addReference(entry.path)}
                        >
                          {entry.display_title}
                          <CharacterBadges roles={entry.roles} compact />
                        </TocLine>
                      ))}
                      {catalog.characters.length === 0 && (
                        <li className="px-2 py-1 text-[11px] text-mute">
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
                        onOpen={() => void openPath(entry.path)}
                        onReference={() => addReference(entry.path)}
                      >
                        {entry.display_title}
                        {!entry.exists && (
                          <span className="ml-2 text-[10px] text-mute">（缺失）</span>
                        )}
                      </TocLine>
                    ))}
                  </ul>
                </section>
              </>
            )}
          </div>

          <div className="toc-foot flex-none px-3 py-2 text-[10px]">
            {current ? current.path : "未打开目录"}
          </div>
        </nav>

        <ResizeHandle
          value={sidebarWidth}
          min={SIDEBAR.min}
          max={SIDEBAR.max}
          label="目录栏宽度"
          onChange={setSidebarWidth}
          onReset={() => setSidebarWidth(SIDEBAR.default)}
        />

        <section className="workbench min-h-0 min-w-0 flex-1 overflow-hidden">
          {currentId ? (
            <Paper file={file} loading={loading} />
          ) : (
            <div className="grid h-full place-items-center text-center text-sm text-sub">
              <div>
                <p className="font-serif text-lg font-bold text-fg-strong">打开一个作品目录</p>
                <p className="mt-2 text-xs">
                  作品就是一个文件夹：正文/ 与 设定集/，历史用 Git。
                </p>
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

        {currentId && chatVisible && (
          <>
            <ResizeHandle
              value={chatWidth}
              min={CHAT.min}
              max={CHAT.max}
              invert
              label="对话栏宽度"
              onChange={setChatWidth}
              onReset={() => setChatWidth(CHAT.default)}
            />
            <div className="flex min-h-0 flex-none flex-col" style={{ width: chatWidth }}>
              <ConversationPanel />
            </div>
          </>
        )}
      </main>
      )}

      <DirectoryDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
      <ModelSettingsDialog />
      <ActivityPanel />
    </div>
  );
}

/**
 * 主页面（P3 T8/T9，P4 T6 多工作区，P5 T1 三栏反转，P5 T6 事件流一级入口，P6 T6 可调侧栏）：
 * 顶部导航切主视图——创作台（左主编对话可折叠可拖宽 + 中正文羊皮纸 + 右故事账本可拖宽，
 * 窄屏账本走抽屉覆盖层）/ 事件流（EventStreamView 占满主区）；语料库·审计中心置灰预留。
 * 工作区语义（计划 D5）：切换 = socket 重连 ws/{slug} + REST 前缀换 slug，不整页刷新；
 * 选中 slug 经 ?w= 记入地址（replaceState），刷新后不丢。
 */

import { useCallback, useEffect, useState } from "react";
import { createWorkspace, fetchWorkspaces } from "./api/rest";
import BiblePanel from "./components/BiblePanel";
import ChatStream from "./components/ChatStream";
import Composer from "./components/Composer";
import EventStreamView from "./components/EventStreamView";
import ErrorBoundary from "./components/ErrorBoundary";
import ManuscriptPanel from "./components/ManuscriptPanel";
import SearchBox from "./components/SearchBox";
import SettingsPanel from "./components/SettingsPanel";
import Splitter, { loadStoredWidth } from "./components/Splitter";
import type { CommandType } from "./api/types";
import type { ConnectionStatus } from "./api/socket";
import { useChatStore } from "./state/store";
import { useAgentSocket } from "./state/useAgentSocket";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  connecting: "连接中",
  open: "已连接",
  closed: "已断开",
};

const STATUS_DOT: Record<ConnectionStatus, string> = {
  connecting: "bg-planned",
  open: "bg-planted",
  closed: "bg-overdue",
};

/** 顶部导航项：创作台 / 事件流（EventStreamView 一级入口）/ 设置 / 置灰预留。 */
const NAV_ITEMS = [
  { key: "studio", label: "✒ 创作台", soon: false },
  { key: "events", label: "▤ 事件流", soon: false },
  { key: "settings", label: "⚙ 设置", soon: false },
  { key: "corpus", label: "册 语料库", soon: true },
  { key: "audit", label: "◎ 审计中心", soon: true },
] as const;

/** 选中工作区写进 ?w=（replaceState 不触发导航），刷新后恢复选择。 */
function switchTo(slug: string): void {
  useChatStore.getState().setSlug(slug);
  const url = new URL(location.href);
  url.searchParams.set("w", slug);
  history.replaceState(null, "", url);
}

/** 侧栏宽度常量（P6 T6，D6）：默认值 + localStorage key 按面板命名。 */
const CHAT_DEFAULT_WIDTH = 300;
const BIBLE_DEFAULT_WIDTH = 340;
const CHAT_WIDTH_KEY = "hastings.panel.chatWidth";
const BIBLE_WIDTH_KEY = "hastings.panel.bibleWidth";

/** 拉工作区列表并初始化选择：?w= 优先，其次第一个；空列表留给引导页。 */
async function bootWorkspaces(): Promise<void> {
  const slugs = await fetchWorkspaces();
  useChatStore.getState().setWorkspaces(slugs);
  const fromUrl = new URLSearchParams(location.search).get("w");
  if (fromUrl !== null && slugs.includes(fromUrl)) {
    switchTo(fromUrl);
  } else if (slugs.length > 0) {
    switchTo(slugs[0] as string);
  }
}

function CreateWorkspaceForm({ onCreated }: { onCreated: (slug: string) => void }) {
  const [slug, setSlug] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    const trimmed = slug.trim();
    if (trimmed === "" || busy) return;
    setBusy(true);
    setError(null);
    try {
      await createWorkspace(trimmed);
      onCreated(trimmed);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="w-80">
      <label htmlFor="workspace-slug" className="mb-2 block text-xs text-gold-soft">
        故事标识（slug，将成为目录名：字母、数字、中文或连字符）
      </label>
      <input
        id="workspace-slug"
        value={slug}
        onChange={(e) => setSlug(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") void submit();
        }}
        placeholder="orient-express"
        className="w-full border border-line bg-transparent px-2 py-1.5 text-sm text-paper placeholder:text-paper/30 focus:border-gold/60 focus:outline-none"
      />
      {error && <p className="mt-2 text-xs leading-relaxed text-overdue">{error}</p>}
      <button
        type="button"
        onClick={() => void submit()}
        disabled={busy || slug.trim() === ""}
        className="mt-3 w-full border border-gold/60 px-3 py-1.5 text-sm text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
      >
        {busy ? "创建中…" : "打开新案卷"}
      </button>
    </div>
  );
}

export default function App() {
  const { reconnect, sendCommand } = useAgentSocket();
  const status = useChatStore((s) => s.status);
  const connectionError = useChatStore((s) => s.connectionError);
  const running = useChatStore((s) => s.running);
  const snapshot = useChatStore((s) => s.snapshot);
  const slug = useChatStore((s) => s.slug);
  const workspaces = useChatStore((s) => s.workspaces);
  const nav = useChatStore((s) => s.nav);
  const [view, setView] = useState<"studio" | "events" | "settings">("studio");
  const [showCreate, setShowCreate] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);
  // 可调侧栏（P6 T6）：宽度惰性读档（损坏回落默认），窄屏账本走抽屉覆盖层。
  const [chatWidth, setChatWidth] = useState(() =>
    loadStoredWidth(CHAT_WIDTH_KEY, CHAT_DEFAULT_WIDTH),
  );
  const [bibleWidth, setBibleWidth] = useState(() =>
    loadStoredWidth(BIBLE_WIDTH_KEY, BIBLE_DEFAULT_WIDTH),
  );
  const [bibleDrawerOpen, setBibleDrawerOpen] = useState(false);

  // 启动：拉工作区列表并恢复/选择工作区（失败走顶部错误条，可重试）。
  useEffect(() => {
    bootWorkspaces().catch((exc: unknown) =>
      useChatStore
        .getState()
        .setConnectionError(
          `${exc instanceof Error ? exc.message : String(exc)} 怎样算对：确认服务已启动后刷新页面。`,
        ),
    );
  }, []);

  // 全局搜索跳转（P6 T11）：正文与账本都在创作台视图，命中一律切回；
  // 窄屏（<xl 账本走抽屉覆盖层）时顺带打开抽屉，xl 及以上账本面板常驻无需处理。
  useEffect(() => {
    if (nav === null) return;
    setView("studio");
    if (nav.kind === "bible" && !window.matchMedia("(min-width: 1280px)").matches) {
      setBibleDrawerOpen(true);
    }
  }, [nav]);

  async function handleCreated(created: string): Promise<void> {
    setShowCreate(false);
    try {
      useChatStore.getState().setWorkspaces(await fetchWorkspaces());
    } catch {
      // 列表刷新失败不阻塞切换——下拉内容稍后由下次启动/新建补齐
    }
    switchTo(created);
  }

  const slugInvalid = slug !== null && !workspaces.includes(slug);

  // 手动重跑对账（P5 T5）：WS sync 命令，复用该章最近一次保存的差异。
  // 服务端拒绝（尚无保存章节等）时 error 文案原样进连接错误条（三要素透传）。
  const retrySync = useCallback(
    (chapter: string) => {
      void sendCommand("sync", undefined, chapter).catch((exc: unknown) =>
        useChatStore
          .getState()
          .setConnectionError(exc instanceof Error ? exc.message : String(exc)),
      );
    },
    [sendCommand],
  );

  return (
    <div className="flex h-screen flex-col">
      <header className="flex flex-none items-center gap-5 border-b border-line bg-gradient-to-b from-ink-soft/90 to-ink/60 px-5 py-0" style={{ height: 52 }}>
        <div className="flex items-center gap-2.5">
          <div className="grid h-[30px] w-[30px] place-items-center rounded-full border border-gold font-serif text-sm font-bold text-gold">
            赫
          </div>
          <div className="leading-tight">
            <div className="font-serif text-base font-bold tracking-widest text-gold">黑斯廷斯备忘录</div>
            <div className="text-[10px] tracking-[0.2em] text-gold-soft/65">
              HASTINGS&rsquo; MEMO · 让好故事更接近真相
            </div>
          </div>
        </div>
        <nav className="ml-3 flex gap-1">
          {NAV_ITEMS.map((item) => {
            const active =
              (item.key === "studio" && view === "studio") ||
              (item.key === "events" && view === "events") ||
              (item.key === "settings" && view === "settings");
            if (item.soon) {
              return (
                <span
                  key={item.key}
                  className="cursor-not-allowed rounded px-3.5 py-1.5 text-[13px] text-paper opacity-30"
                  title="后续阶段开放"
                >
                  {item.label}
                  <small className="ml-1 text-[9px] text-gold-soft">
                    {item.key === "corpus" ? "P6" : "后续"}
                  </small>
                </span>
              );
            }
            return (
              <button
                key={item.key}
                type="button"
                onClick={() => {
                  if (item.key === "events") setView("events");
                  else if (item.key === "settings") setView("settings");
                  else setView("studio");
                }}
                className={`rounded border px-3.5 py-1.5 text-[13px] transition-colors ${
                  active
                    ? "border-line bg-gold/10 text-gold"
                    : "border-transparent text-paper/75 hover:text-gold"
                }`}
              >
                {item.label}
              </button>
            );
          })}
        </nav>
        <SearchBox />
        <div className="ml-auto flex items-center gap-3 text-xs text-paper/60">
          <button
            type="button"
            onClick={() => setBibleDrawerOpen(true)}
            className="border border-line px-2 py-0.5 transition-colors hover:border-gold/50 xl:hidden"
          >
            ▤ 账本
          </button>
          <select
            aria-label="工作区"
            value={slug ?? ""}
            onChange={(e) => switchTo(e.target.value)}
            className="max-w-40 border border-line bg-transparent px-1.5 py-0.5 text-xs text-paper focus:border-gold/60 focus:outline-none"
          >
            {slug === null && <option value="">选择案卷…</option>}
            {slug !== null && !workspaces.includes(slug) && (
              <option value={slug}>{slug}</option>
            )}
            {workspaces.map((w) => (
              <option key={w} value={w}>
                {w}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => setShowCreate((v) => !v)}
            className={`border px-2 py-0.5 transition-colors ${
              showCreate ? "border-gold bg-gold/15 text-gold" : "border-line hover:border-gold/50"
            }`}
          >
            新建案卷
          </button>
          {slug !== null && (
            <a
              href={`/api/${encodeURIComponent(slug)}/export`}
              title="导出工作区全量备份（zip：账本/正文/设置/会话/事件）"
              className="border border-line px-2 py-0.5 transition-colors hover:border-gold/50"
            >
              导出
            </a>
          )}
          {snapshot && (
            <span className="hidden font-mono md:inline">{snapshot.model}</span>
          )}
          {running && (
            <span className="border border-planned/50 px-2 py-0.5 text-planned">写作中</span>
          )}
          <span className="flex items-center gap-1.5">
            <span
              className={`h-2 w-2 rounded-full ${STATUS_DOT[status]} ${
                status !== "open" ? "animate-pulse" : ""
              }`}
            />
            {STATUS_LABEL[status]}
          </span>
          {status === "closed" && (
            <button
              type="button"
              onClick={reconnect}
              className="border border-line px-2 py-0.5 transition-colors hover:border-gold/50"
            >
              重连
            </button>
          )}
        </div>
      </header>

      {connectionError && (
        <div className="flex items-center gap-3 border-b border-overdue/40 bg-overdue/10 px-6 py-2 text-xs text-paper">
          <span className="min-w-0 flex-1">{connectionError}</span>
          <button
            type="button"
            onClick={() => {
              useChatStore.getState().setConnectionError(null);
              bootWorkspaces().catch((exc: unknown) =>
                useChatStore.getState().setConnectionError(String(exc)),
              );
            }}
            className="shrink-0 border border-overdue/60 px-2 py-0.5 text-overdue transition-colors hover:bg-overdue/10"
          >
            重试
          </button>
        </div>
      )}

      {showCreate && (
        <div className="flex items-start justify-end border-b border-line px-6 py-4">
          <div className="border border-line bg-ink p-4">
            <CreateWorkspaceForm
              onCreated={(created) => void handleCreated(created)}
            />
          </div>
        </div>
      )}

      {/* 顶层错误边界（D10）：一个面板崩不拖垮整个应用——顶部导航与其余视图不受影响。 */}
      <ErrorBoundary label="页面">
        {view === "events" ? (
        <main className="flex min-h-0 flex-1 flex-col border-t border-line">
          <ErrorBoundary label="事件流">
            <EventStreamView />
          </ErrorBoundary>
        </main>
      ) : view === "settings" ? (
        <main className="flex min-h-0 flex-1 flex-col border-t border-line">
          <SettingsPanel />
        </main>
      ) : slug === null || slugInvalid ? (
        <main className="flex flex-1 items-center justify-center">
          <div className="max-w-md text-center">
            <h2 className="font-serif text-2xl font-bold text-gold">
              {slugInvalid ? "案卷不在架上" : "案卷房空空如也"}
            </h2>
            <p className="mt-2 text-xs leading-relaxed text-paper/50">
              {slugInvalid
                ? "地址里的工作区不存在（可能已被移动）。从上方下拉选一本，或新建一本。"
                : "还没有任何故事工作区。打开第一本案卷，开始你的悬疑连载。"}
            </p>
            <div className="mt-6 flex justify-center">
              <CreateWorkspaceForm onCreated={(created) => void handleCreated(created)} />
            </div>
          </div>
        </main>
      ) : (
        <main className="relative flex min-h-0 flex-1">
          {chatCollapsed ? (
            <button
              type="button"
              onClick={() => setChatCollapsed(false)}
              aria-label="展开对话"
              className="flex w-7 flex-none flex-col items-center gap-2 border-r border-line bg-ink-soft/35 py-3 text-[10px] tracking-widest text-gold-soft transition-colors hover:text-gold"
              style={{ writingMode: "vertical-rl" }}
            >
              主编对话
            </button>
          ) : (
            <>
              <aside
                className="relative flex flex-none flex-col bg-ink-soft/35"
                style={{ width: chatWidth }}
              >
                <button
                  type="button"
                  onClick={() => setChatCollapsed(true)}
                  aria-label="折叠对话"
                  title="折叠对话"
                  className="absolute right-1.5 top-1.5 z-10 border border-line bg-ink px-1.5 py-0.5 text-[10px] text-paper/50 transition-colors hover:text-gold"
                >
                  ‹
                </button>
                <section className="flex min-h-0 flex-1 flex-col">
                  <ErrorBoundary label="对话">
                    <ChatStream onRetrySync={retrySync} />
                  </ErrorBoundary>
                  <Composer
                    sendCommand={(type: CommandType, message: string) =>
                      sendCommand(type, message)
                    }
                    abort={() => sendCommand("abort")}
                  />
                </section>
              </aside>
              <Splitter
                side="left"
                width={chatWidth}
                onWidthChange={setChatWidth}
                defaultWidth={CHAT_DEFAULT_WIDTH}
                storageKey={CHAT_WIDTH_KEY}
              />
            </>
          )}
          <ErrorBoundary label="正文">
            <ManuscriptPanel onSyncChapter={retrySync} />
          </ErrorBoundary>
          <Splitter
            side="right"
            width={bibleWidth}
            onWidthChange={setBibleWidth}
            defaultWidth={BIBLE_DEFAULT_WIDTH}
            storageKey={BIBLE_WIDTH_KEY}
            className="hidden xl:block"
          />
          <aside
            className="hidden flex-none flex-col overflow-hidden bg-ink-soft/35 xl:flex"
            style={{ width: bibleWidth }}
          >
            <ErrorBoundary label="账本">
              <BiblePanel />
            </ErrorBoundary>
          </aside>
          {/* 窄屏（<xl）账本抽屉覆盖层（D6）：信息密度高不整栏丢弃；定宽渲染，无 Splitter。 */}
          {bibleDrawerOpen && (
            <div className="fixed inset-0 z-40 xl:hidden">
              <div
                className="absolute inset-0 bg-ink/70"
                onClick={() => setBibleDrawerOpen(false)}
                aria-hidden="true"
              />
              <aside className="absolute inset-y-0 right-0 flex w-80 max-w-[85vw] flex-col border-l border-line bg-ink">
                <div className="flex flex-none items-center justify-between border-b border-line px-3 py-2">
                  <span className="text-xs tracking-widest text-gold-soft">故事账本</span>
                  <button
                    type="button"
                    onClick={() => setBibleDrawerOpen(false)}
                    aria-label="关闭账本"
                    className="border border-line px-1.5 text-xs text-paper/60 transition-colors hover:text-gold"
                  >
                    ×
                  </button>
                </div>
                <div className="min-h-0 flex-1">
                  <ErrorBoundary label="账本">
                    <BiblePanel />
                  </ErrorBoundary>
                </div>
              </aside>
            </div>
          )}
        </main>
      )}
      </ErrorBoundary>
    </div>
  );
}

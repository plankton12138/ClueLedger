/**
 * 主页面（P3 T8/T9，P4 T6 多工作区，P5 T1 三栏反转，P6 T6 可调侧栏，P7 T1/T14 顶栏三段式）：
 * 顶栏 = 品牌区｜主导航六项｜右段（全局搜索 + 当前文章徽标 + 状态徽章 + 重连）。
 * 主视图：创作台（左主编对话可折叠可拖宽 + 中正文羊皮纸 + 右故事账本可拖宽，
 * 窄屏账本走抽屉覆盖层，把手在创作台右缘）/ 文章管理 / 语料库 / 审计中心（占位，T7/T9 转正）/
 * 事件流 / 设置；账本全页（ledger）从创作台下钻进入（T5）。工作区语义（P4 计划 D5）：
 * 切换 = socket 重连 ws/{slug} + REST 前缀换 slug，不整页刷新；选中 slug 经 ?w=
 * 记入地址（replaceState），刷新后不丢。文章的增删切换在「文章」页管理（T14）。
 */

import { useCallback, useEffect, useState } from "react";
import { fetchStory, fetchWorkspaces } from "./api/rest";
import type { StoryWire } from "./api/rest";
import ArticlesPage from "./components/ArticlesPage";
import BiblePanel from "./components/BiblePanel";
import ChatStream from "./components/ChatStream";
import Composer from "./components/Composer";
import EventStreamView from "./components/EventStreamView";
import ErrorBoundary from "./components/ErrorBoundary";
import AuditPage from "./components/AuditPage";
import CorpusPage from "./components/CorpusPage";
import LedgerPage from "./components/LedgerPage";
import ManuscriptPanel from "./components/ManuscriptPanel";
import SearchBox from "./components/SearchBox";
import SettingsPanel from "./components/SettingsPanel";
import Splitter, { loadStoredWidth } from "./components/Splitter";
import type { CommandType } from "./api/types";
import type { ConnectionStatus } from "./api/socket";
import { useChatStore } from "./state/store";
import { chapterProgress, ledgerSummary, stageMeta } from "./state/story";
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

/**
 * 主视图类型（P7 D1，T14 扩容）：导航六项 + 账本全页详情（ledger 不进导航，
 * 从创作台账本面板「展开」下钻，T5 接线；先入骨架保证视图收口唯一）。
 */
type View = "studio" | "articles" | "corpus" | "audit" | "events" | "settings" | "ledger";

/** 顶部主导航（P7 D1/T14）：创作台 | 文章 | 语料库 | 审计中心 | 事件流 | 设置。 */
const NAV_ITEMS: Array<{ key: View; label: string }> = [
  { key: "studio", label: "✒ 创作台" },
  { key: "articles", label: "✎ 文章" },
  { key: "corpus", label: "册 语料库" },
  { key: "audit", label: "◎ 审计中心" },
  { key: "events", label: "▤ 事件流" },
  { key: "settings", label: "⚙ 设置" },
];

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

/**
 * 拉工作区列表并初始化选择：?w= 优先，其次第一个；无任何文章 → 返回 true，
 * 由 App 落到「文章」页引导新建（T14）。
 */
async function bootWorkspaces(): Promise<boolean> {
  const slugs = await fetchWorkspaces();
  useChatStore.getState().setWorkspaces(slugs);
  const fromUrl = new URLSearchParams(location.search).get("w");
  if (fromUrl !== null && slugs.includes(fromUrl)) {
    switchTo(fromUrl);
    return false;
  }
  if (slugs.length > 0) {
    switchTo(slugs[0] as string);
    return false;
  }
  return true;
}

export default function App() {
  const { reconnect, sendCommand } = useAgentSocket();
  const status = useChatStore((s) => s.status);
  const connectionError = useChatStore((s) => s.connectionError);
  const running = useChatStore((s) => s.running);
  const slug = useChatStore((s) => s.slug);
  const workspaces = useChatStore((s) => s.workspaces);
  const nav = useChatStore((s) => s.nav);
  const [view, setView] = useState<View>("studio");
  const [chatCollapsed, setChatCollapsed] = useState(false);
  // 可调侧栏（P6 T6）：宽度惰性读档（损坏回落默认），窄屏账本走抽屉覆盖层。
  const [chatWidth, setChatWidth] = useState(() =>
    loadStoredWidth(CHAT_WIDTH_KEY, CHAT_DEFAULT_WIDTH),
  );
  const [bibleWidth, setBibleWidth] = useState(() =>
    loadStoredWidth(BIBLE_WIDTH_KEY, BIBLE_DEFAULT_WIDTH),
  );
  const [bibleDrawerOpen, setBibleDrawerOpen] = useState(false);
  // 生命周期快照（P8 T7，D7）：顶栏阶段徽章 + 章节进度；连接时 + 每次 run 结束 refetch。
  const [story, setStory] = useState<StoryWire | null>(null);

  // 启动：拉工作区列表并恢复/选择工作区（失败走顶部错误条，可重试）；
  // 无任何文章 → 直接落到「文章」页引导新建（T14）。
  useEffect(() => {
    bootWorkspaces()
      .then((empty) => {
        if (empty) setView("articles");
      })
      .catch((exc: unknown) =>
        useChatStore
          .getState()
          .setConnectionError(
            `${exc instanceof Error ? exc.message : String(exc)} 怎样算对：确认服务已启动后刷新页面。`,
          ),
      );
  }, []);

  // 生命周期快照拉取（P8 T7）：slug 变化（切换文章）与 running 翻转（run 结束）时
  // refetch；读取失败降级隐藏徽章——顶栏状态不阻断创作（与状态注入同一泄压哲学）。
  useEffect(() => {
    if (slug === null) {
      setStory(null);
      return;
    }
    let alive = true;
    fetchStory(slug)
      .then((s) => {
        if (alive) setStory(s);
      })
      .catch(() => {
        if (alive) setStory(null);
      });
    return () => {
      alive = false;
    };
  }, [slug, running]);

  // 全局搜索跳转（P6 T11）：正文与账本都在创作台视图，命中一律切回；
  // 窄屏（<xl 账本走抽屉覆盖层）时顺带打开抽屉，xl 及以上账本面板常驻无需处理。
  useEffect(() => {
    if (nav === null) return;
    setView("studio");
    if (nav.kind === "bible" && !window.matchMedia("(min-width: 1280px)").matches) {
      setBibleDrawerOpen(true);
    }
  }, [nav]);

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
      {/* 顶栏三段式（P7 D2）：左品牌｜中主导航（窄屏折叠为下拉）｜右段操作区。 */}
      <header className="flex flex-none items-center gap-4 border-b border-line bg-gradient-to-b from-ink-soft/90 to-ink/60 px-5 py-0" style={{ height: 52 }}>
        <div className="flex flex-none items-center gap-2.5">
          <div className="grid h-[30px] w-[30px] place-items-center rounded-full border border-gold font-serif text-sm font-bold text-gold">
            赫
          </div>
          <div className="hidden leading-tight lg:block">
            <div className="font-serif text-base font-bold tracking-widest text-gold">黑斯廷斯备忘录</div>
            <div className="text-[10px] tracking-[0.2em] text-gold-soft/65">
              HASTINGS&rsquo; MEMO · 让好故事更接近真相
            </div>
          </div>
        </div>
        <nav className="hidden min-w-0 flex-1 justify-center gap-1 md:flex">
          {NAV_ITEMS.map((item) => (
            <button
              key={item.key}
              type="button"
              onClick={() => setView(item.key)}
              className={`rounded border px-3.5 py-1.5 text-[13px] transition-colors ${
                view === item.key
                  ? "border-line bg-gold/10 text-gold"
                  : "border-transparent text-paper/75 hover:text-gold"
              }`}
            >
              {item.label}
            </button>
          ))}
        </nav>
        {/* 窄屏（<md）导航折叠为下拉——同一 view 状态，只是呈现收窄（D2 布局不变量）。 */}
        <select
          aria-label="页面导航"
          value={view}
          onChange={(e) => setView(e.target.value as View)}
          className="mx-auto border border-line bg-transparent px-1.5 py-1 text-xs text-paper focus:border-gold/60 focus:outline-none md:hidden"
        >
          {NAV_ITEMS.map((item) => (
            <option key={item.key} value={item.key}>
              {item.label}
            </option>
          ))}
        </select>
        <div className="ml-auto flex flex-none items-center gap-2.5 text-xs text-paper/60">
          <div className="hidden min-w-0 sm:block">
            <SearchBox />
          </div>
          {/* 当前文章只读徽标（T14）：管理动作收进「文章」页，此处只作指向。 */}
          <button
            type="button"
            onClick={() => setView("articles")}
            title="文章管理"
            className={`max-w-44 border px-2 py-0.5 transition-colors ${
              view === "articles"
                ? "border-gold/60 text-gold"
                : "border-line text-paper/75 hover:border-gold/50"
            }`}
          >
            <span className="block truncate">{slug ?? "未选择文章"}</span>
          </button>
          {/* 阶段徽章 + 章节进度（P8 T7，D7）：数据来自 GET /api/{slug}/story，
              完备度速览收进 title——顶栏保持一行的信息密度约束。 */}
          {story && (
            <span
              title={ledgerSummary(story.counts)}
              className={`hidden items-center gap-2 border px-2 py-0.5 md:flex ${stageMeta(story.stage).className}`}
            >
              <span className="whitespace-nowrap">{stageMeta(story.stage).label}</span>
              <span className="whitespace-nowrap text-paper/45">
                {chapterProgress(story.chapters)}
              </span>
            </span>
          )}
          {/* 状态徽章（D2）：连接点 + 文案合并为一枚；写作中（连接正常且 run 进行）
              同一枚徽章换色脉动，替代原先两处并排。 */}
          {(() => {
            const writing = running && status === "open";
            return (
              <span
                className={`flex items-center gap-1.5 border px-2 py-0.5 ${
                  writing ? "border-planned/50 text-planned" : "border-line text-paper/60"
                }`}
              >
                <span
                  className={`h-2 w-2 rounded-full ${
                    writing
                      ? "bg-planned animate-pulse"
                      : `${STATUS_DOT[status]}${status !== "open" ? " animate-pulse" : ""}`
                  }`}
                />
                {writing ? "写作中" : STATUS_LABEL[status]}
              </span>
            );
          })()}
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
              bootWorkspaces()
                .then((empty) => {
                  if (empty) setView("articles");
                })
                .catch((exc: unknown) =>
                  useChatStore.getState().setConnectionError(String(exc)),
                );
            }}
            className="shrink-0 border border-overdue/60 px-2 py-0.5 text-overdue transition-colors hover:bg-overdue/10"
          >
            重试
          </button>
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
      ) : view === "corpus" ? (
        <ErrorBoundary label="语料库">
          <CorpusPage />
        </ErrorBoundary>
      ) : view === "audit" ? (
        <ErrorBoundary label="审计中心">
          <AuditPage onSteer={(message) => sendCommand(running ? "steer" : "prompt", message)} />
        </ErrorBoundary>
      ) : view === "ledger" ? (
        <ErrorBoundary label="账本全页">
          <LedgerPage onBack={() => setView("studio")} />
        </ErrorBoundary>
      ) : view === "articles" || slug === null || slugInvalid ? (
        <ErrorBoundary label="文章">
          <ArticlesPage onSwitch={switchTo} onEnter={() => setView("studio")} />
        </ErrorBoundary>
      ) : (
        <main className="relative flex min-h-0 flex-1">
          {/* 窄屏账本抽屉把手（P7 D2）：从顶栏移入创作台，右缘中部抽屉拉手形态。 */}
          <button
            type="button"
            onClick={() => setBibleDrawerOpen(true)}
            aria-label="打开账本"
            className="fixed right-0 top-1/2 z-30 -translate-y-1/2 border border-r-0 border-line bg-ink-soft/90 px-0.5 py-3 text-[10px] tracking-widest text-gold-soft transition-colors hover:text-gold xl:hidden"
            style={{ writingMode: "vertical-rl" }}
          >
            账本
          </button>
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
                className="relative flex min-w-0 flex-none flex-col overflow-hidden bg-ink-soft/35"
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
                <section className="flex min-h-0 min-w-0 flex-1 flex-col">
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
              <BiblePanel onExpand={() => setView("ledger")} />
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
                    <BiblePanel onExpand={() => setView("ledger")} />
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

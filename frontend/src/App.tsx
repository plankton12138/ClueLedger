import {
  BookOpenText,
  CheckCheck,
  Feather,
  GitBranch,
  LayoutDashboard,
  PenLine,
  Search,
  Settings2,
  Target,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useState } from "react";
import ErrorBoundary from "./components/ErrorBoundary";
import ModelButton from "./components/ModelButton";
import SearchResults from "./components/SearchResults";
import SettingsDrawer from "./components/SettingsDrawer";
import { ConfirmDiscard, type View } from "./components/Workbench";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import AuditPage from "./pages/AuditPage";
import ChangesPage from "./pages/ChangesPage";
import LedgerPage from "./pages/LedgerPage";
import OverviewPage from "./pages/OverviewPage";
import StudioPage from "./pages/StudioPage";
import {
  currentProjectName,
  useV2Store,
  isDirty,
} from "./state/v2";
import { useV2Events } from "./state/v2socket";
import ChatSidebar, { ChatToggle, OPEN_KEY } from "./components/chat/ChatSidebar";
import DeskShell from "./components/desk/DeskShell";
import { useChatStore } from "./state/chat";
import { layoutKind } from "./layout";
import ChatCandidateReview from "./components/chat/ChatCandidateReview";
import type { ChatMessage } from "./api/chat";
const nav: { key: View; label: string; Icon: LucideIcon }[] = [
  { key: "overview", label: "项目概览", Icon: LayoutDashboard },
  { key: "desk", label: "写作台", Icon: PenLine },
  { key: "studio", label: "创作台", Icon: Feather },
  { key: "ledger", label: "故事账本", Icon: BookOpenText },
  { key: "changes", label: "变更影响", Icon: GitBranch },
  { key: "review", label: "修订审阅", Icon: CheckCheck },
  { key: "audit", label: "悬疑审计", Icon: Target },
];
function readChatOpen(narrow: boolean): boolean {
  if (narrow) return false;
  try {
    const stored = localStorage.getItem(OPEN_KEY);
    if (stored === "0") return false;
    if (stored === "1") return true;
  } catch {
    /* 禁用存储时走默认策略 */
  }
  // 桌面真实服务与模拟器都默认展示；能力不匹配在侧栏内明确提示。
  return true;
}
export default function App() {
  const [view, setView] = useState<View>("overview"),
    [settingsOpen, setSettingsOpen] = useState(false),
    [modelsOpen, setModelsOpen] = useState(false),
    [query, setQuery] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [candidate, setCandidate] = useState<{ projectId: string; message: ChatMessage } | null>(null);
  // 对话栏：桌面收起/展开（持久化），窄屏以覆盖面板打开（不持久化）。
  const [chatOpen, setChatOpen] = useState(() =>
    readChatOpen(layoutKind(window.innerWidth) === "narrow"),
  ),
    [narrow, setNarrow] = useState(
      () => layoutKind(window.innerWidth) === "narrow",
    );
  const s = useV2Store(),
    conn = useV2Events(),
    unread = useChatStore((st) => st.unread),
    chatService = useChatStore((st) => st.service),
    setPinned = useChatStore((st) => st.setPinned);
  useEffect(() => {
    const onResize = () => setNarrow(layoutKind(window.innerWidth) === "narrow");
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  useEffect(() => {
    setPinned(chatOpen && !narrow);
    if (narrow) return;
    try {
      localStorage.setItem(OPEN_KEY, chatOpen ? "1" : "0");
    } catch {
      /* 禁用存储时仅本次会话生效 */
    }
  }, [chatOpen, narrow, setPinned]);
  useEffect(() => {
    setCandidate(null);
    if (!s.currentId) return;
    const state = useV2Store.getState();
    void state.refreshManuscript();
    void state.refreshStory();
    void state.refreshChanges();
    void state.refreshJobs();
    const timer = window.setInterval(() => { void state.refreshJobs(); }, 1800);
    return () => window.clearInterval(timer);
  }, [s.currentId]);
  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      const c = useV2Store.getState().chapter;
      if (isDirty(c) || c.saving) {
        e.preventDefault();
        e.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, []);
  const pendingCount = s.changes.filter(
    (c) => !["committed", "abandoned"].includes(c.status),
  ).length;
  return (
    <div className="flex h-screen flex-col">
      <header className="topbar flex min-h-16 flex-none items-center gap-2 px-3 sm:gap-4 sm:px-5">
        <div className="mark grid h-10 w-10 flex-none place-items-center font-serif text-2xl font-bold">
          H
        </div>
        <div className="hidden leading-tight sm:block">
          <div className="font-serif text-xl font-bold tracking-wide">
            Hastings
          </div>
          <div className="text-[9px] tracking-[0.25em] text-white/85">
            STORY WORKSPACE
          </div>
        </div>
        <div className="hidden h-8 w-px bg-[#355f7f] shadow-[1px_0_#c4d9e8] sm:block" />
        <Select
          value={s.currentId ?? ""}
          disabled={s.chapter.saving}
          onValueChange={(v) => {
            if (v === "__manage__") setSettingsOpen(true);
            else if (!s.selectProject(v)) setPending(v);
          }}
        >
          <SelectTrigger
            aria-label="选择项目"
            className="project-picker h-[34px] w-48 min-w-0 border-none px-3 text-xs font-bold text-white shadow-none focus:ring-0"
          >
            <SelectValue placeholder="选择项目…" />
          </SelectTrigger>
          <SelectContent>
            {s.projects.map((p) => (
              <SelectItem key={p.projectId} value={p.projectId}>
                {p.name}
              </SelectItem>
            ))}
            <SelectItem value="__manage__">管理 / 新建项目…</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <div className="relative hidden md:block">
          <div className="search-well flex h-[33px] w-56 items-center gap-2 px-3">
            <Search size={14} />
            <input
              aria-label="全局搜索"
              className="w-full bg-transparent text-xs text-fg outline-none placeholder:text-mute"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="搜索章节、角色与线索"
            />
          </div>
          {query.trim() && (
            <SearchResults
              query={query.trim()}
              onClose={() => setQuery("")}
              onNavigate={(target) => {
                if (target.kind === "chapter") {
                  if (isDirty(s.chapter)) {
                    s.setError("请先保存当前正文，再打开搜索结果中的章节。");
                    return;
                  }
                  void s.selectChapter(target.id);
                  setView("studio");
                } else {
                  useV2Store.setState({ focusArtifactId: target.id });
                  setView("ledger");
                }
                setQuery("");
              }}
            />
          )}
        </div>
        <span
          className="flex items-center gap-1.5 whitespace-nowrap text-[11px]"
          role="status"
        >
          <i
            className={`inline-block h-2 w-2 rounded-full ${conn === "open" ? "live-dot" : "bg-white/60"}`}
          />
          {!s.currentId
            ? "未选择项目"
            : conn === "open"
              ? "已连接"
              : conn === "connecting"
                ? "连接中"
                : "已断开"}
        </span>
        <div className="top-avatar hidden h-[34px] w-[34px] place-items-center font-serif text-lg lg:grid">
          作
        </div>
        <ModelButton onClick={() => setModelsOpen(true)} />
        <ChatToggle
          open={chatOpen}
          unread={unread}
          onToggle={() => setChatOpen((v) => !v)}
        />
        <button
          className="project-picker grid h-[34px] w-[34px] shrink-0 place-items-center"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings2 size={16} />
        </button>
      </header>
      <nav
        aria-label="主导航"
        className="navbar flex flex-none gap-1 overflow-x-auto px-3 pt-1.5"
      >
        {nav.map((item) => (
          <button
            type="button"
            aria-label={item.label}
            key={item.key}
            aria-current={view === item.key ? "page" : undefined}
            onClick={() => setView(item.key)}
            className={`navitem h-10 shrink-0 px-4 text-xs ${view === item.key ? "on" : ""}`}
          >
            <item.Icon size={15} />
            {item.label}
            {item.key === "changes" && pendingCount > 0 && (
              <span className="nav-badge px-1.5 py-0.5 text-[10px]">
                {pendingCount}
              </span>
            )}
          </button>
        ))}
      </nav>
      {s.error && (
        <div
          role="alert"
          className="flex flex-none items-center gap-3 border-b border-red-edge bg-red-bg px-5 py-2 text-xs text-red"
        >
          <span className="min-w-0 flex-1">{s.error}</span>
          <button
            className="btn btn-gray shrink-0 px-3 py-1"
            onClick={() => s.setError(null)}
          >
            知道了
          </button>
          <button
            className="underline"
            onClick={() => {
              s.setError(null);
              void s.refreshManuscript();
              void s.refreshStory();
              void s.refreshChanges();
            }}
          >
            重试读取
          </button>
        </div>
      )}
      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div
          className={`workbench min-h-0 flex-1 overflow-hidden ${chatOpen && !narrow ? "chat-on" : ""}`}
        >
          <ErrorBoundary key={`${s.currentId}:${view}`} label="页面">
            {view === "overview" && (
              <OverviewPage
                onNavigate={setView}
                onManage={() => setSettingsOpen(true)}
              />
            )}
            {view === "desk" && (
              <DeskShell onOpenModels={() => setModelsOpen(true)} />
            )}
            {view === "studio" && <StudioPage />}
            {view === "ledger" && <LedgerPage onNavigate={setView} />}
            {view === "changes" && <ChangesPage onNavigate={setView} />}
            {view === "review" && (candidate && candidate.projectId === s.currentId
              ? <ChatCandidateReview key={candidate.message.message_id}
                  candidate={candidate.message} projectId={candidate.projectId}
                  onClose={() => setCandidate(null)} />
              : <ChangesPage review onNavigate={setView} />)}
            {view === "audit" && <AuditPage onNavigate={setView} />}
          </ErrorBoundary>
        </div>
        <ChatSidebar
          open={chatOpen}
          narrow={narrow}
          onClose={() => setChatOpen(false)}
          onReviewCandidate={(message) => {
            if (s.currentId) setCandidate({ projectId: s.currentId, message });
            setView("review");
            if (narrow) setChatOpen(false);
          }}
          onOpenChange={(changeId) => {
            useV2Store.setState({ selectedChangeId: changeId });
            void s.refreshChanges();
            setView("changes");
            if (narrow) setChatOpen(false);
          }}
        />
      </div>
      <footer className="statusbar flex h-8 flex-none items-center justify-between gap-4 overflow-hidden whitespace-nowrap px-4 text-[10px]">
        <span className="flex min-w-0 gap-4">
          <span className="max-w-40 truncate">
            ● {currentProjectName(s)} · 主分支
          </span>
          <span>
            版本 <b>{s.head?.slice(0, 8) ?? "—"}</b>
          </span>
          <span className="hidden md:inline">
            章基础 <b>{s.chapter.chapterBase.slice(0, 8) || "—"}</b>
          </span>
          <span className="hidden lg:inline">
            上次保存 <b>{s.chapter.lastSavedAt ?? "—"}</b>
          </span>
        </span>
        <span>
          {s.chapter.saving
            ? "保存中…"
            : isDirty(s.chapter)
              ? "● 有未保存修改"
              : s.chapter.loading
                ? "正在读取…"
                : "✓ 无未保存修改"}
        </span>
        <span className="hidden lg:inline" data-testid="chat-status">
          对话：
          {chatOpen
            ? narrow
              ? "面板已打开"
              : chatService
                ? chatService.capabilities().simulated
                  ? "模拟模式"
                  : "已连接"
                : "服务未连接"
            : "已收起"}
        </span>
      </footer>
      <SettingsDrawer open={settingsOpen} onOpenChange={setSettingsOpen} />
      <SettingsDrawer open={modelsOpen} onOpenChange={setModelsOpen} modelsOnly />
      <ConfirmDiscard
        open={pending !== null}
        onCancel={() => setPending(null)}
        onDiscard={() => {
          if (pending) s.selectProject(pending, true);
          setPending(null);
        }}
      />
    </div>
  );
}

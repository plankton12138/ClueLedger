import { Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import ErrorBoundary from "./components/ErrorBoundary";
import ModelButton from "./components/ModelButton";
import SettingsDrawer from "./components/SettingsDrawer";
import { ConfirmDiscard } from "./components/Workbench";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "./components/ui/select";
import { isDirty, useV2Store } from "./state/v2";
import { useV2Events } from "./state/v2socket";
import DeskShell from "./components/desk/DeskShell";

/**
 * 应用外壳（V03-9 M1）：写作台是唯一主视图。
 *
 * 只保留顶栏（作品标记 + 作品选择器 + 模型 + 设置）与写作台；导航栏、搜索、
 * 连接状态点、状态栏、旧版本页面与对话侧栏全部撤下。错误条保留——它是真实
 * 状态而非开发痕迹；切换作品的未保存确认（ConfirmDiscard）保留。
 */
export default function App() {
  const s = useV2Store(),
    [settingsOpen, setSettingsOpen] = useState(false),
    [modelsOpen, setModelsOpen] = useState(false),
    [pending, setPending] = useState<string | null>(null);
  useV2Events(); // 保留实时订阅，不显示连接状态点

  useEffect(() => {
    if (!s.currentId) return;
    const state = useV2Store.getState();
    void state.refreshManuscript();
    void state.refreshStory();
    void state.refreshJobs();
    const timer = window.setInterval(() => {
      void state.refreshJobs();
    }, 1800);
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

  return (
    <div className="flex h-screen flex-col">
      <header className="topbar flex min-h-12 flex-none items-center gap-2 px-3 sm:gap-4 sm:px-5">
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
            aria-label="选择作品"
            className="project-picker h-[34px] w-48 min-w-0 border-none px-3 text-xs font-bold text-white shadow-none focus:ring-0"
          >
            <SelectValue placeholder="选择作品…" />
          </SelectTrigger>
          <SelectContent>
            {s.projects.map((p) => (
              <SelectItem key={p.projectId} value={p.projectId}>
                {p.name}
              </SelectItem>
            ))}
            <SelectItem value="__manage__">管理 / 新建作品…</SelectItem>
          </SelectContent>
        </Select>
        <div className="flex-1" />
        <ModelButton onClick={() => setModelsOpen(true)} />
        <button
          className="project-picker grid h-[34px] w-[34px] shrink-0 place-items-center"
          aria-label="设置"
          onClick={() => setSettingsOpen(true)}
        >
          <Settings2 size={16} />
        </button>
      </header>
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
            }}
          >
            重试读取
          </button>
        </div>
      )}
      <main className="min-h-0 flex-1 overflow-hidden">
        <ErrorBoundary key={s.currentId} label="写作台">
          <DeskShell onOpenModels={() => setModelsOpen(true)} />
        </ErrorBoundary>
      </main>
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

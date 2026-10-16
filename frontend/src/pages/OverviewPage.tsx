import {
  BookOpen,
  Feather,
  GitBranch,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { useV2Store, currentProjectName } from "../state/v2";
import { exportUrl } from "../api/v2";
import {
  Panel,
  Empty,
  Notice,
  Pill,
  CollapsiblePanel,
  changeLabels,
  jobLabels,
  roleLabels,
  type View,
} from "../components/Workbench";
import { useChatStore } from "../state/chat";
export default function OverviewPage({
  onNavigate,
  onManage,
}: {
  onNavigate(view: View): void;
  onManage(): void;
}) {
  const s = useV2Store();
  const chatPinned = useChatStore((st) => st.pinned);
  const words = s.chapters.reduce(
    (sum, c) =>
      sum + c.units.reduce((n, u) => n + u.text.replace(/\s/g, "").length, 0),
    0,
  );
  const pending = s.changes.filter(
    (c) => !["committed", "abandoned"].includes(c.status),
  );
  return (
    <main className="page-grid" aria-label="项目概览">
      <aside className="space-y-4">
        <Panel title="我的作品" meta={`${s.projects.length} 个项目`}>
          <div>
            {s.projects.map((p) => (
              <button
                key={p.projectId}
                className={`asset-row w-full p-4 text-left ${s.currentId === p.projectId ? "sel" : ""}`}
                onClick={() => {
                  if (!s.selectProject(p.projectId))
                    s.setError("请先保存创作台中的正文，再切换项目。");
                }}
              >
                <span className="book-spine">
                  <BookOpen size={17} />
                </span>
                <span className="min-w-0">
                  <b className="block truncate text-sm">{p.name}</b>
                  <span className="text-[11px] text-sub">
                    本地小说项目
                  </span>
                </span>
              </button>
            ))}
          </div>
          <button
            className="btn btn-gray m-3 w-[calc(100%-24px)] py-2 text-xs"
            onClick={onManage}
          >
            ＋ 新建 / 找回作品
          </button>
        </Panel>
        <Panel title="快速入口">
          <div className="p-2">
            {(
              [
                { view: "studio", label: "继续写作", Icon: Feather },
                { view: "changes", label: "修改设定", Icon: GitBranch },
                { view: "audit", label: "一致性检查", Icon: ShieldCheck },
              ] as const
            ).map((a) => (
              <button
                key={a.view}
                className="asset-row w-full px-2 py-3 text-left text-xs"
                onClick={() => onNavigate(a.view)}
              >
                <span className="mini-icon h-7 w-7">
                  <a.Icon size={14} />
                </span>
                {a.label}
              </button>
            ))}
          </div>
        </Panel>
        <Notice>
          <strong>作者拥有最终决定权</strong>
          <br />
          Agent 提出候选；接受之后，内容才进入正式稿。
        </Notice>
      </aside>
      <section className="min-w-0 space-y-4">
        <div className="hero px-7 py-8">
          <p className="text-[10px] tracking-[0.2em] text-white/70">
            PROJECT / STORY WORKSPACE
          </p>
          <h1 className="mb-3 mt-4 font-serif text-3xl font-bold">
            {currentProjectName(s)}
          </h1>
          <p className="mb-6 text-xs text-white/80">
            让每一个故事决定都有来处，让每一次修改都可以审阅。
          </p>
          <button
            className="btn px-5 py-2 text-xs"
            onClick={() => (s.currentId ? onNavigate("studio") : onManage())}
          >
            {s.currentId ? "继续这部作品" : "开始你的第一部作品"} →
          </button>
        </div>
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {[
            { n: s.chapters.length, label: "已保存章节" },
            { n: words.toLocaleString(), label: "正文总字数" },
            { n: pending.length, label: "进行中的变更", tone: "warn" },
            {
              n: s.storyArtifacts.length,
              label: "故事事实与计划",
              tone: "green",
            },
          ].map((m) => (
            <div key={m.label} className={`metric p-4 ${m.tone ?? ""}`}>
              <b className="text-3xl">{m.n}</b>
              <p className="mt-1 text-[11px] text-sub">{m.label}</p>
            </div>
          ))}
        </div>
        <Panel title="下一步" meta="从当前项目出发">
          <div className="divide-y divide-line">
            {!s.currentId ? (
              <Empty>新建项目，或从设置面板用项目编号找回作品。</Empty>
            ) : (
              <>
                <button
                  className="flex w-full items-center gap-4 p-5 text-left"
                  onClick={() => onNavigate("studio")}
                >
                  <span className="mini-icon h-9 w-9">
                    <Feather size={18} />
                  </span>
                  <span className="flex-1">
                    <b className="text-sm">
                      {s.chapters.length
                        ? `继续编辑 · ${s.chapter.title || s.chapters[0]?.title}`
                        : "先规划故事，再生成第一章"}
                    </b>
                    <p className="mt-1 text-xs text-sub">
                      正文保存立即生效，后台分析不会覆盖你的编辑。
                    </p>
                  </span>
                  <span className="text-blue">打开 →</span>
                </button>
                <button
                  className="flex w-full items-center gap-4 p-5 text-left"
                  onClick={() => onNavigate("review")}
                >
                  <span className="mini-icon gold h-9 w-9">
                    <Sparkles size={18} />
                  </span>
                  <span className="flex-1">
                    <b className="text-sm">审阅局部修改与候选</b>
                    <p className="mt-1 text-xs text-sub">
                      检查修改前后与依据，选择真正要进入作品的内容。
                    </p>
                  </span>
                  <span className="text-blue">去审阅 →</span>
                </button>
              </>
            )}
          </div>
        </Panel>
      </section>
      <aside className="space-y-4">
        <CollapsiblePanel
          title="待处理工作"
          meta={`${pending.length} 个变更`}
          defaultOpen={!chatPinned}
        >
          {pending.length ? (
            pending.slice(0, 5).map((c) => (
              <button
                key={c.change_set_id}
                className="asset-row block w-full p-4 text-left"
                onClick={() => {
                  useV2Store.setState({ selectedChangeId: c.change_set_id });
                  onNavigate(c.status === "review" ? "review" : "changes");
                }}
              >
                <Pill tone="amber">{changeLabels[c.status] ?? c.status}</Pill>
                <p className="mt-2 text-xs">{c.intent}</p>
              </button>
            ))
          ) : (
            <Empty>当前没有待处理变更。</Empty>
          )}
        </CollapsiblePanel>
        <CollapsiblePanel title="最近任务" defaultOpen={!chatPinned}>
          {s.jobs.length ? (
            s.jobs
              .slice(-5)
              .reverse()
              .map((j) => (
                <div className="asset-row block p-4 text-xs" key={j.job_id}>
                  <b>{roleLabels[j.job_type ?? ""] ?? "创作"}任务</b>
                  <p className="mt-1 text-sub">
                    {jobLabels[j.state ?? ""] ?? j.state}
                  </p>
                </div>
              ))
          ) : (
            <Empty>在创作台提交第一项规划任务。</Empty>
          )}
        </CollapsiblePanel>
        {s.currentId && (
          <a
            className="btn btn-gray flex py-2 text-xs"
            href={exportUrl(s.currentId)}
          >
            ⇩ 导出当前版本备份
          </a>
        )}
        <Notice>
          作品事实、计划和正文统一保存在项目版本中。对话和任务结果是过程记录。
        </Notice>
      </aside>
    </main>
  );
}

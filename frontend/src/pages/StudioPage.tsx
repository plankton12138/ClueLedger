import { useState } from "react";
import { importChapter, restoreV2Chapter } from "../api/v2";
import { useV2Store, isDirty, message } from "../state/v2";
import AgentTasks from "../components/AgentTasks";
import { useChatStore } from "../state/chat";
import { offerSelection } from "../components/chat/refs";
import {
  Panel,
  Empty,
  Notice,
  ErrorText,
  ConfirmDiscard,
  CollapsiblePanel,
  Pill,
  titleOf,
  kindLabels,
} from "../components/Workbench";

export default function StudioPage() {
  const s = useV2Store();
  const [pending, setPending] = useState<string | null>(null),
    [newOpen, setNewOpen] = useState(false);
  const [title, setTitle] = useState(""),
    [text, setText] = useState(""),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const [source, setSource] = useState("");
  const ch = s.chapter,
    dirty = isDirty(ch);
  // 对话栏常驻时，次要信息默认收拢，把宽度留给正文（Phase 19.1）。
  const chatPinned = useChatStore((st) => st.pinned);
  const [selection, setSelectionText] = useState("");
  async function perform(work: () => Promise<void>) {
    setBusy(true);
    setError("");
    try {
      await work();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  function select(id: string) {
    if (dirty) setPending(id);
    else void s.selectChapter(id);
  }
  // 次要信息片段：对话栏常驻时收拢进左列（目录下方），右列让位给对话栏，
  // 对话紧贴正文编辑区，不成四列拥挤布局（Phase 19.1 用户走查反馈）。
  const secondary = (
    <>
      <CollapsiblePanel
        title="场景事实与叙事计划"
        meta="当前项目"
        defaultOpen={!chatPinned}
      >
        {s.storyArtifacts
          .filter((a) => a.kind.startsWith("narrative."))
          .slice(0, 8)
          .map((a) => (
            <div className="asset-row block p-4 text-xs" key={a.artifact_id}>
              <p className="mb-1 font-bold text-blue">
                {kindLabels[a.kind]} · {titleOf(a)}
              </p>
              <p className="leading-6 text-sub">
                {String(
                  a.content.purpose || a.content.goal || "已接受的叙事计划",
                )}
              </p>
            </div>
          ))}
        {!s.storyArtifacts.some((a) => a.kind.startsWith("narrative.")) && (
          <Empty>接受策划候选后，场景与节拍会出现在这里。</Empty>
        )}
      </CollapsiblePanel>
      <CollapsiblePanel title="从历史版本恢复" defaultOpen={!chatPinned}>
        <form
          className="space-y-3 p-4"
          onSubmit={(e) => {
            e.preventDefault();
            void perform(async () => {
              if (dirty)
                throw new Error("先保存或处理当前草稿，再恢复历史正文。");
              const pid = s.currentId!,
                cid = ch.chapterId!;
              await restoreV2Chapter(pid, cid, source.trim(), ch.chapterBase);
              if (useV2Store.getState().currentId === pid) {
                await s.selectChapter(cid, true);
                await s.refreshManuscript();
              }
            });
          }}
        >
          <label className="field">
            来源提交编号
            <input
              aria-label="恢复来源提交"
              className="well-input"
              value={source}
              onChange={(e) => setSource(e.target.value)}
              required
            />
          </label>
          <p className="text-xs leading-6 text-sub">
            恢复会生成新版本，已有历史仍保留。
          </p>
          <button
            className="btn btn-gray px-3 py-2 text-xs"
            disabled={busy || !ch.chapterId || dirty || ch.saving}
          >
            恢复为当前正文
          </button>
          <ErrorText>{error}</ErrorText>
        </form>
      </CollapsiblePanel>
    </>
  );
  return (
    <main className="studio-grid" aria-label="创作台">
      <aside className="min-h-0 overflow-y-auto space-y-4">
        <AgentTasks compact />
        <Panel title="章节目录" meta={`${s.chapters.length} 章`}>
          {s.chapters.map((c) => (
            <button
              key={c.chapter_id}
              className={`asset-row block w-full px-4 py-3 text-left text-xs ${ch.chapterId === c.chapter_id ? "sel" : ""}`}
              disabled={ch.saving}
              onClick={() => select(c.chapter_id)}
            >
              {c.title}
            </button>
          ))}
          <button
            className="btn btn-gray m-3 w-[calc(100%-24px)] py-2 text-xs"
            disabled={!s.currentId}
            onClick={() => setNewOpen(!newOpen)}
          >
            ＋ 导入 / 新建章节
          </button>
          {newOpen && (
            <form
              className="space-y-3 border-t border-line p-3"
              onSubmit={(e) => {
                e.preventDefault();
                void perform(async () => {
                  if (dirty) throw new Error("请先保存当前正文，再导入新章。");
                  const pid = s.currentId!;
                  const result = await importChapter(
                    pid,
                    title,
                    text,
                    String(s.chapters.length + 1).padStart(4, "0"),
                  );
                  if (useV2Store.getState().currentId !== pid) return;
                  await s.refreshManuscript();
                  await s.selectChapter(result.chapter_id);
                  setNewOpen(false);
                  setTitle("");
                  setText("");
                });
              }}
            >
              <input
                className="well-input w-full p-2 text-xs"
                aria-label="导入章节标题"
                placeholder="章节标题"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                required
              />
              <textarea
                className="well-input min-h-32 w-full p-2 text-xs"
                aria-label="导入章节正文"
                placeholder="粘贴章节正文"
                value={text}
                onChange={(e) => setText(e.target.value)}
                required
              />
              <button
                className="btn px-3 py-2 text-xs"
                disabled={busy || !s.currentId}
              >
                保存为新章节
              </button>
              <ErrorText>{error}</ErrorText>
            </form>
          )}
        </Panel>
        {chatPinned && (
          <div className="space-y-4">
            {secondary}
            <Notice>
              人工保存立即生效。任务完成表示候选已返回，接受后的版本才是正式稿。
            </Notice>
          </div>
        )}
      </aside>
      <section className="panel flex min-h-0 min-w-0 flex-col">
        <header className="panel-head flex-wrap gap-2 px-4 py-3">
          <h1 className="text-sm">{ch.title || "正文工作台"}</h1>
          <div className="flex items-center gap-2">
            <Pill tone={dirty ? "amber" : "green"}>
              {ch.saving ? "保存中" : dirty ? "未保存" : "已保存"}
            </Pill>
            <button
              className="btn px-4 py-1.5 text-xs"
              disabled={!ch.chapterId || ch.loading || ch.saving || !dirty}
              onClick={() => void s.saveChapter()}
            >
              保存正文
            </button>
          </div>
        </header>
        {ch.stale && (
          <div className="notice m-3 p-3 text-xs">
            此章已有新版本。你的编辑仍保留；请先复制草稿，再重新读取合并。
          </div>
        )}
        <div className="px-3 pt-2">
          <ErrorText>{ch.error}</ErrorText>
        </div>
        {ch.loading ? (
          <Empty>正在读取正文…</Empty>
        ) : ch.chapterId && ch.draft !== null ? (
          <div className="relative min-h-0 flex-1 overflow-auto bg-[#d4dadd] p-4 lg:p-6">
            <textarea
              aria-label="人工正文"
              className="paper-sheet min-h-full w-full resize-none px-6 py-8 font-serif text-[15px] leading-8 outline-none lg:px-10"
              spellCheck={false}
              value={ch.draft}
              onChange={(e) => s.setDraft(e.target.value)}
              onSelect={(e) =>
                setSelectionText(
                  e.currentTarget.value.slice(
                    e.currentTarget.selectionStart,
                    e.currentTarget.selectionEnd,
                  ),
                )
              }
              onBlur={() => setSelectionText("")}
            />
            {selection.trim() !== "" && (
              <button
                className="btn absolute bottom-4 right-4 px-3 py-1.5 text-xs shadow-surface"
                onClick={() => {
                  offerSelection({
                    chapterId: ch.chapterId!,
                    chapterTitle: ch.title,
                    excerpt: selection.trim(),
                  });
                  setSelectionText("");
                }}
              >
                引用选中文本到对话（未保存摘录）
              </button>
            )}
          </div>
        ) : (
          <div className="flex flex-1 items-center justify-center">
            <Empty>
              作品还没有正文。先接受一个节拍，生成正文候选；也可以从左侧导入已有章节。
            </Empty>
          </div>
        )}
        <div className="flex items-center justify-between border-t border-line bg-white/60 px-4 py-2 text-[11px] text-sub">
          <span>
            {(ch.draft ?? "").replace(/\s/g, "").length.toLocaleString()} 字 ·
            正文优先
          </span>
          <button
            className="text-blue underline"
            disabled={!ch.chapterId || ch.saving || ch.loading}
            onClick={() => {
              if (dirty) setPending(ch.chapterId);
              else if (ch.chapterId) void s.selectChapter(ch.chapterId, true);
            }}
          >
            重新读取当前章
          </button>
        </div>
      </section>
      {!chatPinned && (
        <aside className="min-h-0 space-y-4 overflow-y-auto">
          {secondary}
          <Notice>
            人工保存立即生效。任务完成表示候选已返回，接受后的版本才是正式稿。
          </Notice>
        </aside>
      )}
      <ConfirmDiscard
        open={pending !== null}
        onCancel={() => setPending(null)}
        onDiscard={() => {
          if (pending) void s.selectChapter(pending, true);
          setPending(null);
        }}
      />
    </main>
  );
}

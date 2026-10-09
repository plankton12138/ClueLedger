/**
 * 正文面板（P5 T1 阅读态 + T5 三态创作台，计划 D1/D2/D7）。
 * 正文是页面中心——羊皮纸承载，三态可切换：
 * - 阅读（设计稿 A）：首行缩进 / 首字下沉 + 章节切换 / 字数 / 上一章·下一章；
 * - 编辑（设计稿 B）：纯 textarea（不做富文本），dirty 徽标 + 显式保存（⌘S）/ 放弃；
 *   agent 写作中置只读并横幅提示（互斥的前端半边，服务端半边见 D4）；
 * - 对账（设计稿 C）：段落级 diff 色块 + 段内字符高亮，可手动重跑对账。
 * 状态住在 store 的 manuscript 切片，本组件只做拉取调度、REST 编排与呈现
 * （与 BiblePanel 同一哲学：前端不跑业务逻辑）。
 */

import { useCallback, useEffect, useRef } from "react";
import type { DiffHunkWire } from "../api/rest";
import { acquireEditMark, fetchChapter, fetchChapters, releaseEditMark, saveChapter } from "../api/rest";
import { chapterNumber, countChars, parseChapterHeading, splitParagraphs } from "../state/chapter";
import { inlineDiff } from "../state/diff";
import { nextEditLock, type EditLockRef } from "../state/editLock";
import { useChatStore, type ManuscriptMode } from "../state/store";

const MODES: { key: ManuscriptMode; label: string }[] = [
  { key: "read", label: "阅读" },
  { key: "edit", label: "编辑" },
  { key: "diff", label: "对账" },
];

type ChipTone = "dirty" | "busy" | "ok" | "warn";

const CHIP_CLASS: Record<ChipTone, string> = {
  dirty: "text-overdue border-overdue/50 bg-overdue/10",
  busy: "text-planned border-planned/50 bg-planned/10",
  ok: "text-planted border-planted/50 bg-planted/10",
  warn: "text-overdue border-overdue/50 bg-overdue/10",
};

function Chip({ tone, children }: { tone: ChipTone; children: string }) {
  return (
    <span className={`rounded-full border px-2.5 py-0.5 text-[10.5px] ${CHIP_CLASS[tone]}`}>
      {children}
    </span>
  );
}

/** 单段落 diff 块：新增绿 / 删除红 / 修改金（段级色条 + 段内字符高亮）。 */
function DiffParagraph({ hunk }: { hunk: DiffHunkWire }) {
  if (hunk.kind === "added") {
    return (
      <p className="mb-[1.35em] border-l-2 border-planted bg-planted/10 py-1 pl-3">
        <span className="mr-2 align-middle text-[10px] text-planted">新增</span>
        {hunk.newText}
      </p>
    );
  }
  if (hunk.kind === "removed") {
    return (
      <p className="mb-[1.35em] border-l-2 border-overdue bg-overdue/10 py-1 pl-3 text-overdue line-through">
        <span className="mr-2 align-middle text-[10px] no-underline">删除</span>
        {hunk.oldText}
      </p>
    );
  }
  const d = inlineDiff(hunk.oldText, hunk.newText);
  return (
    <p className="mb-[1.35em] border-l-2 border-gold bg-gold/10 py-1 pl-3">
      <span className="mr-2 align-middle text-[10px] text-gold">修改</span>
      {d.prefix}
      {d.oldMiddle && <del className="bg-overdue/15 text-overdue line-through">{d.oldMiddle}</del>}
      {d.newMiddle && <ins className="bg-planted/20 text-planted no-underline">{d.newMiddle}</ins>}
      {d.suffix}
    </p>
  );
}

export default function ManuscriptPanel({
  onSyncChapter,
}: {
  /** 手动重跑对账（经 WS sync 命令）；缺省则隐藏「重新同步」按钮。 */
  onSyncChapter?: (chapter: string) => void;
} = {}) {
  const slug = useChatStore((s) => s.slug);
  const status = useChatStore((s) => s.status);
  const running = useChatStore((s) => s.running);
  const ms = useChatStore((s) => s.manuscript);
  const setChapters = useChatStore((s) => s.setChapters);
  const selectChapter = useChatStore((s) => s.selectChapter);
  const chapterLoaded = useChatStore((s) => s.chapterLoaded);
  const chapterFailed = useChatStore((s) => s.chapterFailed);
  const setMode = useChatStore((s) => s.setMode);
  const setDraft = useChatStore((s) => s.setDraft);
  const setManuscriptError = useChatStore((s) => s.setManuscriptError);
  const saveStart = useChatStore((s) => s.saveStart);
  const saveFailed = useChatStore((s) => s.saveFailed);
  const applySaved = useChatStore((s) => s.applySaved);
  const discardDraft = useChatStore((s) => s.discardDraft);

  // 作者草稿锁的持有者（纯逻辑在 state/editLock.ts，这里只负责发 REST）。
  const lockRef = useRef<EditLockRef | null>(null);

  // 加锁后若不归还，agent 对该章的 write_chapter 会被长期拒绝（R3-①）：
  // 切章节、切工作区、退出编辑态（保存/放弃）都必须释放——纯函数决定该释放谁。
  useEffect(() => {
    const { release, held } = nextEditLock(lockRef.current, {
      slug,
      chapterId: ms.chapterId,
      mode: ms.mode,
    });
    lockRef.current = held;
    if (release !== null) {
      // 释放是幂等的；失败无害（进程重启自然清空）——不打扰作者
      void releaseEditMark(release.slug, release.chapterId).catch(() => undefined);
    }
  }, [slug, ms.chapterId, ms.mode]);

  // 章节目录拉取：已选章不在新目录（空工作区/外部删章）时自动选第一章；
  // 已选章仍存在则保留选中（重连 refetch 不得打断阅读位置）。
  const refreshChapters = useCallback(
    async (workspaceSlug: string) => {
      try {
        const ids = await fetchChapters(workspaceSlug);
        setChapters(ids);
        const current = useChatStore.getState().manuscript;
        if (ids.length > 0 && (current.chapterId === null || !ids.includes(current.chapterId))) {
          selectChapter(ids[0] as string);
        }
      } catch (exc) {
        // 列表拉取失败与「当前章正文」无关，不经 chapterFailed 的 id 竞态守卫
        // （未选章时守卫会把错误整条吞掉，R3-③）
        setManuscriptError(String(exc));
      }
    },
    [setChapters, selectChapter, setManuscriptError],
  );

  useEffect(() => {
    if (slug === null) return;
    void refreshChapters(slug);
  }, [slug, refreshChapters]);

  useEffect(() => {
    if (slug === null || status !== "open") return;
    void refreshChapters(slug);
  }, [slug, status, refreshChapters]);

  // chapterId 变化 → 拉正文；晚到响应由 store 的 id 比对丢弃（竞态防护在状态层）。
  useEffect(() => {
    if (slug === null || ms.chapterId === null) return;
    const chapterId = ms.chapterId;
    let alive = true;
    void fetchChapter(slug, chapterId)
      .then((chapter) => {
        if (alive) chapterLoaded(chapterId, chapter.content, chapter.revision);
      })
      .catch((exc: unknown) => {
        if (alive) chapterFailed(chapterId, exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, [slug, ms.chapterId, chapterLoaded, chapterFailed]);

  // --- REST 编排：加锁 / 保存 / 放弃（store 只存状态，本组件只调度） ---

  const enterEdit = useCallback(() => {
    const current = useChatStore.getState().manuscript;
    if (slug === null || current.chapterId === null || running) return;
    setMode("edit");
    // 作者开始编辑 → 加草稿锁（D4）：失败则退回阅读（守门不变量优先，不让编辑态裸奔）
    void acquireEditMark(slug, current.chapterId).catch((exc: unknown) => {
      saveFailed(exc instanceof Error ? exc.message : String(exc));
      setMode("read");
    });
  }, [slug, running, setMode, saveFailed]);

  const doSave = useCallback(async () => {
    const current = useChatStore.getState().manuscript;
    if (slug === null || current.chapterId === null) return;
    if (current.draft === null || current.revision === null) return;
    if (current.saveState === "saving") return;
    if (current.draft === current.baseline) return; // 无变更：不发请求（空 diff 是合法 no-op）
    const submitted = current.draft; // 提交快照：保存期间的新输入不得被当作已保存
    saveStart();
    try {
      const saved = await saveChapter(slug, current.chapterId, submitted, current.revision);
      // 服务端保存成功即释放草稿锁 → 本地引用同步清空，避免随后的 effect 重复释放
      lockRef.current = null;
      applySaved(saved.revision, saved.diff, submitted);
    } catch (exc) {
      saveFailed(exc instanceof Error ? exc.message : String(exc));
    }
  }, [slug, saveStart, applySaved, saveFailed]);

  const doDiscard = useCallback(() => {
    const current = useChatStore.getState().manuscript;
    lockRef.current = null; // 下面已显式释放，effect 不必再来一次
    discardDraft();
    if (slug !== null && current.chapterId !== null) {
      // 释放锁是幂等的；失败无害（进程重启自然清空）——不打扰作者
      void releaseEditMark(slug, current.chapterId).catch(() => undefined);
    }
  }, [slug, discardDraft]);

  // ⌘S / Ctrl+S：编辑态且有未保存改动时保存（显式保存是唯一落盘入口）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.metaKey || e.ctrlKey) || e.key.toLowerCase() !== "s") return;
      const current = useChatStore.getState().manuscript;
      if (current.mode !== "edit" || current.saveState !== "dirty") return;
      e.preventDefault();
      void doSave();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [doSave]);

  const shownText = ms.mode === "edit" ? ms.draft : ms.baseline;
  const parsed = shownText !== null ? parseChapterHeading(shownText) : null;
  const paragraphs = parsed !== null ? splitParagraphs(parsed.body) : [];
  const chars = shownText !== null ? countChars(shownText) : 0;
  const index = ms.chapterId !== null ? ms.chapterIds.indexOf(ms.chapterId) : -1;
  const prev = index > 0 ? ms.chapterIds[index - 1] : null;
  const next = index >= 0 && index < ms.chapterIds.length - 1 ? ms.chapterIds[index + 1] : null;

  const syncing = ms.syncState === "queued" || ms.syncState === "syncing";
  const editLocked = running;

  return (
    <section className="flex min-w-0 flex-1 flex-col xl:min-w-[320px]">
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <select
          aria-label="章节"
          value={ms.chapterId ?? ""}
          onChange={(e) => selectChapter(e.target.value)}
          className="max-w-56 border border-line bg-transparent px-1.5 py-0.5 font-serif text-sm text-paper focus:border-gold/60 focus:outline-none"
        >
          {ms.chapterId === null && <option value="">选择章节…</option>}
          {ms.chapterIds.map((id) => (
            <option key={id} value={id}>
              第{chapterNumber(id)}章
            </option>
          ))}
        </select>
        {shownText !== null && (
          <span className="text-xs text-paper/50">
            字数 <b className="font-semibold text-gold-soft">{chars.toLocaleString()}</b>
          </span>
        )}
        <div className="ml-auto flex items-center gap-2">
          {ms.saveState === "dirty" && <Chip tone="dirty">● 未保存</Chip>}
          {ms.saveState === "saving" && <Chip tone="busy">保存中…</Chip>}
          {syncing && <Chip tone="busy">◌ 对账中…</Chip>}
          {ms.syncState === "done" && <Chip tone="ok">✓ 已同步</Chip>}
          {ms.syncState === "failed" && <Chip tone="warn">⚠ 对账失败</Chip>}
          {ms.mode === "edit" && (
            <>
              <button
                type="button"
                onClick={doDiscard}
                className="border border-line px-2.5 py-0.5 text-[11.5px] text-paper/60 transition-colors hover:text-paper"
              >
                放弃
              </button>
              <button
                type="button"
                onClick={() => void doSave()}
                disabled={ms.saveState !== "dirty"}
                className="border border-gold/60 px-2.5 py-0.5 text-[11.5px] text-gold transition-colors hover:bg-gold/15 disabled:opacity-40"
              >
                保存 ⌘S
              </button>
            </>
          )}
          {ms.diff !== null && onSyncChapter && (
            <button
              type="button"
              onClick={() => ms.chapterId !== null && onSyncChapter(ms.chapterId)}
              className="border border-line px-2.5 py-0.5 text-[11.5px] text-paper/60 transition-colors hover:text-paper"
            >
              重新同步
            </button>
          )}
          <div className="flex overflow-hidden rounded border border-line">
            {MODES.map((m) => {
              const active = ms.mode === m.key;
              const disabled = m.key === "diff" ? ms.diff === null : m.key === "edit" && editLocked;
              return (
                <button
                  key={m.key}
                  type="button"
                  disabled={disabled}
                  onClick={() => (m.key === "edit" ? enterEdit() : setMode(m.key))}
                  className={`px-3 py-0.5 text-[11.5px] transition-colors disabled:opacity-30 ${
                    active ? "bg-gold/15 text-gold" : "text-paper/55 hover:text-gold"
                  }`}
                >
                  {m.label}
                </button>
              );
            })}
          </div>
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {ms.error !== null && (
          <p className="mx-auto mb-4 max-w-[680px] border border-overdue/50 bg-overdue/10 px-3 py-2 text-xs leading-relaxed text-paper">
            {ms.error}
          </p>
        )}
        {ms.loading && <p className="mt-10 text-center text-xs text-paper/40">读取正文中…</p>}
        {!ms.loading && parsed === null && ms.error === null && (
          <p className="mt-10 text-center text-xs text-paper/40">
            {ms.chapterIds.length === 0
              ? "本案卷还没有章节（在左侧与 AI 主编讨论，或让它动笔写第一章）。"
              : "从上方选择一章开始阅读。"}
          </p>
        )}

        {parsed !== null && (
          <article className="relative mx-auto w-full max-w-[680px] rounded bg-parchment px-[52px] pt-10 pb-7 text-parchment-ink shadow-[0_12px_40px_rgba(0,0,0,0.5),0_2px_8px_rgba(0,0,0,0.4)]">
            <div
              aria-hidden
              className="pointer-events-none absolute left-[52px] right-[52px] top-[26px] h-px bg-gradient-to-r from-transparent via-parchment-line to-transparent"
            />
            {ms.mode === "edit" && (
              <div className="mb-4 rounded border border-overdue/40 bg-overdue/10 px-3 py-1.5 text-center text-xs text-overdue">
                {editLocked
                  ? "✎ Agent 写作中 · 本章正文暂不可编辑（写作完成后自动解锁）"
                  : "✎ 作者编辑中 · 本章对 Agent 写入已锁定（write_chapter 将被拒绝）"}
              </div>
            )}
            <h2 className="text-center font-serif text-2xl font-bold tracking-[0.25em]">
              {parsed.title !== null
                ? parsed.title
                : ms.chapterId !== null
                  ? `第${chapterNumber(ms.chapterId)}章`
                  : ""}
            </h2>
            <div aria-hidden className="mt-2.5 mb-6 text-center text-gold tracking-[0.6em]">
              ❖ ─── ✦ ─── ❖
            </div>

            {ms.mode === "edit" ? (
              <textarea
                aria-label="正文编辑"
                value={ms.draft ?? ""}
                // 保存往返期间冻结输入（R3-②）：否则这段新输入会被晚到的保存响应
                // 追认为已落盘（saveState 转 clean 而内容其实没提交过）
                readOnly={editLocked || ms.saveState === "saving"}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                className="min-h-[380px] w-full resize-y whitespace-pre-wrap border-0 bg-transparent font-serif text-[15px] leading-[2.0] text-parchment-ink outline-none"
              />
            ) : ms.mode === "diff" && ms.diff !== null ? (
              <div className="text-justify font-serif text-[15px] leading-[2.0]">
                {ms.diff.map((hunk, i) => (
                  <DiffParagraph key={i} hunk={hunk} />
                ))}
                <p className="mt-3 border-t border-dashed border-parchment-line pt-2.5 font-sans text-[11px] text-parchment-mute">
                  对账视图：{ms.diff.length} 处段落变更 · 段内高亮 = 字符级差异 · 右侧账本面板联动更新 →
                </p>
              </div>
            ) : (
              <div className="text-justify font-serif text-[15px] leading-[2.0]">
                {paragraphs.map((p, i) =>
                  i === 0 ? (
                    <p key={i} className="mb-[1.35em]">
                      <span className="float-left pt-2 pr-2.5 font-serif text-[58px] font-bold leading-none">
                        {p.slice(0, 1)}
                      </span>
                      {p.slice(1)}
                    </p>
                  ) : (
                    <p key={i} className="mb-[1.35em] indent-[2em]">
                      {p}
                    </p>
                  ),
                )}
              </div>
            )}

            <footer className="mt-auto flex items-center border-t border-parchment-line pt-4 text-xs text-parchment-mute">
              <button
                type="button"
                disabled={prev === null}
                onClick={() => prev !== null && selectChapter(prev)}
                className="transition-colors hover:text-parchment-ink disabled:opacity-30"
              >
                ← 上一章
              </button>
              <span className="ml-auto text-parchment-ink">
                <b className="font-semibold">{ms.chapterId !== null ? chapterNumber(ms.chapterId) : "—"}</b>{" "}
                / {ms.chapterIds.length}
              </span>
              <button
                type="button"
                disabled={next === null}
                onClick={() => next !== null && selectChapter(next)}
                className="ml-auto transition-colors hover:text-parchment-ink disabled:opacity-30"
              >
                下一章 →
              </button>
            </footer>
          </article>
        )}
      </div>
    </section>
  );
}
/**
 * 正文面板（P5 T1，计划 D1/D8）：正文成为页面中心——羊皮纸阅读面。
 * 阅读模式：章节列表（GET chapters）+ 正文渲染（首行缩进 / 首字下沉）+
 * 章节切换 / 字数 / 上一章·下一章。状态住在 store 的 manuscript 切片，
 * 本组件只做拉取调度与呈现（与 BiblePanel 同一哲学：前端不跑业务逻辑）。
 */

import { useCallback, useEffect } from "react";
import { fetchChapter, fetchChapters } from "../api/rest";
import { countChars, parseChapterHeading, splitParagraphs } from "../state/chapter";
import { useChatStore } from "../state/store";

/** ch03 → 3；非 chNN 命名的章节 id 原样展示。 */
function chapterNumber(id: string): string {
  const match = /^ch(\d+)$/.exec(id);
  return match !== null ? String(Number(match[1])) : id;
}

export default function ManuscriptPanel() {
  const slug = useChatStore((s) => s.slug);
  const status = useChatStore((s) => s.status);
  const chapterIds = useChatStore((s) => s.manuscript.chapterIds);
  const chapterId = useChatStore((s) => s.manuscript.chapterId);
  const content = useChatStore((s) => s.manuscript.content);
  const loading = useChatStore((s) => s.manuscript.loading);
  const error = useChatStore((s) => s.manuscript.error);
  const setChapters = useChatStore((s) => s.setChapters);
  const selectChapter = useChatStore((s) => s.selectChapter);
  const chapterLoaded = useChatStore((s) => s.chapterLoaded);
  const chapterFailed = useChatStore((s) => s.chapterFailed);

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
        chapterFailed(useChatStore.getState().manuscript.chapterId ?? "", String(exc));
      }
    },
    [setChapters, selectChapter, chapterFailed],
  );

  // slug 变化 → 重拉章节目录（工作区切换的重置在 store 的 setSlug 里已完成）。
  useEffect(() => {
    if (slug === null) return;
    void refreshChapters(slug);
  }, [slug, refreshChapters]);

  // 连接（重）建立 → 章节列表保鲜（断线期间 agent 可能写了新章）。
  useEffect(() => {
    if (slug === null || status !== "open") return;
    void refreshChapters(slug);
  }, [slug, status, refreshChapters]);

  // chapterId 变化 → 拉正文；晚到响应由 store 的 id 比对丢弃（竞态防护在状态层）。
  useEffect(() => {
    if (slug === null || chapterId === null) return;
    let alive = true;
    void fetchChapter(slug, chapterId)
      .then((text) => {
        if (alive) chapterLoaded(chapterId, text);
      })
      .catch((exc: unknown) => {
        if (alive) chapterFailed(chapterId, exc instanceof Error ? exc.message : String(exc));
      });
    return () => {
      alive = false;
    };
  }, [slug, chapterId, chapterLoaded, chapterFailed]);

  const parsed = content !== null ? parseChapterHeading(content) : null;
  const paragraphs = parsed !== null ? splitParagraphs(parsed.body) : [];
  const chars = content !== null ? countChars(content) : 0;
  const index = chapterId !== null ? chapterIds.indexOf(chapterId) : -1;
  const prev = index > 0 ? chapterIds[index - 1] : null;
  const next = index >= 0 && index < chapterIds.length - 1 ? chapterIds[index + 1] : null;

  return (
    <section className="flex min-w-0 flex-1 flex-col">
      <div className="flex flex-none items-center gap-3 border-b border-line bg-ink-soft/50 px-4 py-2">
        <select
          aria-label="章节"
          value={chapterId ?? ""}
          onChange={(e) => selectChapter(e.target.value)}
          className="max-w-56 border border-line bg-transparent px-1.5 py-0.5 font-serif text-sm text-paper focus:border-gold/60 focus:outline-none"
        >
          {chapterId === null && <option value="">选择章节…</option>}
          {chapterIds.map((id) => {
            const label = `第${chapterNumber(id)}章`;
            return (
              <option key={id} value={id}>
                {label}
              </option>
            );
          })}
        </select>
        {content !== null && (
          <span className="text-xs text-paper/50">
            字数 <b className="font-semibold text-gold-soft">{chars.toLocaleString()}</b>
          </span>
        )}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {error !== null && (
          <p className="mx-auto mb-4 max-w-[680px] border border-overdue/50 bg-overdue/10 px-3 py-2 text-xs leading-relaxed text-paper">
            {error}
          </p>
        )}
        {loading && (
          <p className="mt-10 text-center text-xs text-paper/40">读取正文中…</p>
        )}
        {!loading && parsed === null && error === null && (
          <p className="mt-10 text-center text-xs text-paper/40">
            {chapterIds.length === 0
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
            <h2 className="text-center font-serif text-2xl font-bold tracking-[0.25em]">
              {parsed.title !== null
                ? parsed.title
                : chapterId !== null
                  ? `第${chapterNumber(chapterId)}章`
                  : ""}
            </h2>
            <div aria-hidden className="mt-2.5 mb-6 text-center text-gold tracking-[0.6em]">
              ❖ ─── ✦ ─── ❖
            </div>
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
                <b className="font-semibold">
                  {chapterId !== null ? chapterNumber(chapterId) : "—"}
                </b>{" "}
                / {chapterIds.length}
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

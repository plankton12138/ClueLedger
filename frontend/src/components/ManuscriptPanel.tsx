/**
 * 正文面板（阅读态；V1 弃用后只读）。
 * 正文是页面中心——羊皮纸（ChapterPaper 共享组件）承载：章节切换 / 字数 /
 * 上一章·下一章。编辑与对账态已随 V1 写路径退役（正文改动走 V2 仓库项目）；
 * 状态住在 store 的 manuscript 切片，本组件只做拉取调度、REST 编排与呈现
 * （与 BiblePanel 同一哲学：前端不跑业务逻辑）。
 */

import { useCallback, useEffect } from "react";
import { fetchChapter, fetchChapters } from "../api/rest";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import ChapterPaper from "./ChapterPaper";
import { chapterNumber, countChars, parseChapterHeading, splitParagraphs } from "../state/chapter";
import { useChatStore } from "../state/store";

export default function ManuscriptPanel() {
  const slug = useChatStore((s) => s.slug);
  const status = useChatStore((s) => s.status);
  const ms = useChatStore((s) => s.manuscript);
  const setChapters = useChatStore((s) => s.setChapters);
  const selectChapter = useChatStore((s) => s.selectChapter);
  const nav = useChatStore((s) => s.nav);
  const clearNav = useChatStore((s) => s.clearNav);
  const chapterLoaded = useChatStore((s) => s.chapterLoaded);
  const chapterFailed = useChatStore((s) => s.chapterFailed);
  const setManuscriptError = useChatStore((s) => s.setManuscriptError);

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

  // 全局搜索跳转（P6 T11）：章节命中 → 定位该章；已是当前章则不重复拉正文。
  // 消费完即 clearNav（跳转是一次性的，不留残影）。选中章触发上方 effect 拉正文。
  useEffect(() => {
    if (nav === null || nav.kind !== "chapter") return;
    if (nav.chapterId !== ms.chapterId) selectChapter(nav.chapterId);
    clearNav();
  }, [nav, ms.chapterId, selectChapter, clearNav]);

  const shownText = ms.baseline;
  const parsed = shownText !== null ? parseChapterHeading(shownText) : null;
  const paragraphs = parsed !== null ? splitParagraphs(parsed.body) : [];
  const chars = shownText !== null ? countChars(shownText) : 0;
  const index = ms.chapterId !== null ? ms.chapterIds.indexOf(ms.chapterId) : -1;
  const prev = index > 0 ? ms.chapterIds[index - 1] : null;
  const next = index >= 0 && index < ms.chapterIds.length - 1 ? ms.chapterIds[index + 1] : null;

  return (
    <section className="flex min-w-0 flex-1 flex-col xl:min-w-[320px]">
      <div className="flex flex-none flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-ink-soft/50 px-3 py-2 sm:px-4">
        <Select
          value={ms.chapterId ?? undefined}
          onValueChange={(v) => selectChapter(v)}
        >
          <SelectTrigger
            aria-label="章节"
            className="max-w-40 min-w-0 px-1.5 py-0.5 font-serif text-sm sm:max-w-56"
          >
            <SelectValue placeholder="选择章节…" />
          </SelectTrigger>
          <SelectContent>
            {ms.chapterIds.map((id) => (
              <SelectItem key={id} value={id}>
                第{chapterNumber(id)}章
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {shownText !== null && (
          <span className="hidden text-xs text-paper/50 min-[430px]:inline">
            字数 <b className="font-semibold text-gold-soft">{chars.toLocaleString()}</b>
          </span>
        )}
        <span className="ml-auto text-[11px] text-paper/40">V1 只读 · 创作请使用 V2 工作台</span>
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
              ? "这篇文章还没有章节。"
              : "从上方选择一章开始阅读。"}
          </p>
        )}

        {parsed !== null && (
          <ChapterPaper
            title={
              parsed.title !== null
                ? parsed.title
                : ms.chapterId !== null
                  ? `第${chapterNumber(ms.chapterId)}章`
                  : ""
            }
            chars={chars}
            chapterNo={ms.chapterId !== null ? chapterNumber(ms.chapterId) : "—"}
            total={ms.chapterIds.length}
            onPrev={prev !== null ? () => selectChapter(prev) : undefined}
            onNext={next !== null ? () => selectChapter(next) : undefined}
            paragraphs={paragraphs}
          />
        )}
      </div>
    </section>
  );
}

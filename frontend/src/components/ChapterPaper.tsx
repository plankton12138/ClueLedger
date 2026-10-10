/**
 * 羊皮纸章节纸面（P7 T3，计划 D4）：从 ManuscriptPanel 提取的纯展示组件——
 * 纸面外壳（顶部分隔线 / 标题 / 菱形饰线 / 页脚章节导航 / 右上角字数徽注）
 * + 阅读态正文（首字下沉）。编辑态 textarea 与对账态 diff 仍由 ManuscriptPanel
 * 以 children 提供；审计中心（T9）复用阅读态。
 */

import type { ReactNode } from "react";

/** 菱形饰线（D4 统一组件）：章节标题下的分隔纹样，羊皮纸语汇。 */
export function OrnamentDivider() {
  return (
    <div aria-hidden className="mt-2.5 mb-6 text-center text-gold tracking-[0.6em]">
      ❖ ─── ✦ ─── ❖
    </div>
  );
}

/** 阅读态正文：首段首字下沉，其余段首行缩进（与提取前逐类一致）。 */
function ReadBody({ paragraphs }: { paragraphs: string[] }) {
  return (
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
  );
}

export interface ChapterPaperProps {
  title: string;
  /** 章内字数（右上角徽注，如「章节字数 2,348」）。 */
  chars: number;
  /** 页脚「N / M」的当前章号与总章数。 */
  chapterNo: string;
  total: number;
  /** 页脚章节导航；缺省按钮置灰（审计中心可接入自己的选章回调）。 */
  onPrev?: () => void;
  onNext?: () => void;
  /** 阅读态段落；children 提供正文主体时忽略（编辑/对账态传参仍可兜底）。 */
  paragraphs?: string[];
  /** 顶部横幅槽（如编辑态互斥提示），位于分隔线下、标题前。 */
  banner?: ReactNode;
  /** 正文主体覆盖（编辑 textarea / 对账 diff）；为空时回退 paragraphs 阅读态。 */
  children?: ReactNode;
}

export default function ChapterPaper({
  title,
  chars,
  chapterNo,
  total,
  onPrev,
  onNext,
  paragraphs,
  banner,
  children,
}: ChapterPaperProps) {
  const body =
    children !== null && children !== undefined && children !== false ? (
      children
    ) : paragraphs !== undefined ? (
      <ReadBody paragraphs={paragraphs} />
    ) : null;
  return (
    <article className="relative mx-auto w-full max-w-[680px] rounded bg-parchment px-6 pt-10 pb-7 text-parchment-ink shadow-[0_12px_40px_rgba(0,0,0,0.5),0_2px_8px_rgba(0,0,0,0.4)] lg:px-[52px]">
      <div
        aria-hidden
        className="pointer-events-none absolute left-6 right-6 top-[26px] h-px bg-gradient-to-r from-transparent via-parchment-line to-transparent lg:left-[52px] lg:right-[52px]"
      />
      {/* 右上角章内字数徽注（D4）：分隔线右端点之外，不与标题争位。 */}
      <span className="absolute right-4 top-[9px] text-[10px] text-parchment-mute">
        章节字数 {chars.toLocaleString("zh-CN")}
      </span>
      {banner}
      <h2 className="text-center font-serif text-2xl font-bold tracking-[0.25em]">{title}</h2>
      <OrnamentDivider />
      {body}
      <footer className="mt-auto flex items-center border-t border-parchment-line pt-4 text-xs text-parchment-mute">
        <button
          type="button"
          disabled={onPrev === undefined}
          onClick={onPrev}
          className="transition-colors hover:text-parchment-ink disabled:opacity-30"
        >
          ← 上一章
        </button>
        <span className="ml-auto text-parchment-ink">
          <b className="font-semibold">{chapterNo}</b> / {total}
        </span>
        <button
          type="button"
          disabled={onNext === undefined}
          onClick={onNext}
          className="ml-auto transition-colors hover:text-parchment-ink disabled:opacity-30"
        >
          下一章 →
        </button>
      </footer>
    </article>
  );
}

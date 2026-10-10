/**
 * 工作区生命周期视图映射（P8 T7，D7）：stage → 徽章文案/配色、账本完备度速览。
 * 纯函数无副作用——vitest 钉死六态映射与降级行为（未知 stage 不崩）。
 */

import type { StoryCountsWire, StoryWire } from "../api/rest";

/** 六阶段（镜像 story_state.py 的 StoryStage）。 */
export type StoryStage =
  | "intake"
  | "bible_drafting"
  | "bible_confirmed"
  | "writing"
  | "revising"
  | "complete";

export interface StageMeta {
  label: string;
  /** 徽章边框/文字配色（对齐工具卡色点体系：蓝=进行、绿=就绪、金=关键）。 */
  className: string;
}

/** 六态配色映射（D7）：终稿金色高亮，其余按进度语义取色。 */
export const STAGE_META: Record<StoryStage, StageMeta> = {
  intake: { label: "构想接收", className: "border-line text-paper/60" },
  bible_drafting: { label: "起草 Bible", className: "border-planned/50 text-planned" },
  bible_confirmed: { label: "Bible 已确认", className: "border-gold-soft/60 text-gold-soft" },
  writing: { label: "写作中", className: "border-planted/50 text-planted" },
  revising: { label: "修订维护", className: "border-gold/50 text-gold" },
  complete: { label: "终稿", className: "border-gold bg-gold/10 text-gold" },
};

/** 未知阶段（服务端前向扩展）降级为原样展示，不崩徽章。 */
export function stageMeta(stage: string): StageMeta {
  return STAGE_META[stage as StoryStage] ?? {
    label: stage,
    className: "border-line text-paper/60",
  };
}

/** 账本完备度速览（徽章 title）：五账本计数一行带过。 */
export function ledgerSummary(counts: StoryCountsWire): string {
  return (
    `角色 ${counts.characters} · 线索 ${counts.clues} · 伏笔 ${counts.foreshadows} · ` +
    `手法 ${counts.methods_locked}/${counts.methods} 锁定 · 章纲 ${counts.outlines}`
  );
}

/** 章节进度文案（n/m）：planned 为 0 时只报已写数（避免 0/0 误导）。 */
export function chapterProgress(chapters: StoryWire["chapters"]): string {
  return chapters.planned > 0
    ? `章 ${chapters.written}/${chapters.planned}`
    : `章 ${chapters.written}`;
}

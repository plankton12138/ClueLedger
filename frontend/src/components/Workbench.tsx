import type { ReactNode } from "react";
import * as Alert from "@radix-ui/react-alert-dialog";
import { useEffect, useState } from "react";

export function Panel({
  title,
  meta,
  children,
  className = "",
  dark = false,
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  className?: string;
  dark?: boolean;
}) {
  return (
    <section className={`panel ${className}`}>
      <header
        className={`panel-head px-4 py-3 text-sm ${dark ? "dark-head" : ""}`}
      >
        <h2>{title}</h2>
        {meta && <small className="text-[11px] font-normal">{meta}</small>}
      </header>
      {children}
    </section>
  );
}
/** 可折叠侧面板（Phase 19.1）：次要说明收拢，保留正文主要空间。 */
export function CollapsiblePanel({
  title,
  meta,
  children,
  defaultOpen = true,
  className = "",
}: {
  title: string;
  meta?: ReactNode;
  children: ReactNode;
  defaultOpen?: boolean;
  className?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  useEffect(() => setOpen(defaultOpen), [defaultOpen]);
  return (
    <section className={`panel panel-collapsible ${className}`}>
      <header className="panel-head px-4 py-3 text-sm">
        <button
          type="button"
          className="flex flex-1 items-center justify-between text-left"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <span className="flex items-center gap-2">
            <h2>{title}</h2>
            {meta && <small className="text-[11px] font-normal">{meta}</small>}
          </span>
          <span aria-hidden className="text-[10px]">
            {open ? "▴" : "▾"}
          </span>
        </button>
      </header>
      {open && children}
    </section>
  );
}
export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-6 text-sm leading-7 text-sub">{children}</div>;
}
export function Notice({ children }: { children: ReactNode }) {
  return <div className="notice p-3 text-xs leading-6">{children}</div>;
}
export function ErrorText({ children }: { children: ReactNode }) {
  return children ? (
    <p
      role="alert"
      className="rounded border border-red-edge bg-red-bg p-3 text-xs leading-6 text-red"
    >
      {children}
    </p>
  ) : null;
}
export function Pill({
  children,
  tone = "blue",
}: {
  children: ReactNode;
  tone?: "blue" | "green" | "amber" | "red";
}) {
  return (
    <span className={`pill pill-${tone} px-2 py-0.5 text-[10px]`}>
      {children}
    </span>
  );
}
export function ConfirmDiscard({
  open,
  onCancel,
  onDiscard,
}: {
  open: boolean;
  onCancel(): void;
  onDiscard(): void;
}) {
  return (
    <Alert.Root
      open={open}
      onOpenChange={(v) => {
        if (!v) onCancel();
      }}
    >
      <Alert.Portal>
        <Alert.Overlay className="fixed inset-0 z-50 bg-overlay" />
        <Alert.Content className="panel fixed left-1/2 top-1/2 z-50 w-[min(440px,90vw)] -translate-x-1/2 -translate-y-1/2 p-6 shadow-surface">
          <Alert.Title className="font-serif text-xl text-fgStrong">
            保留这份未保存的正文？
          </Alert.Title>
          <Alert.Description className="my-4 text-sm leading-7 text-sub">
            当前编辑尚未保存。继续切换会放弃这些修改；你也可以返回创作台，先保存再切换。
          </Alert.Description>
          <div className="flex justify-end gap-2">
            <Alert.Cancel className="btn btn-gray px-4 py-2" onClick={onCancel}>
              返回编辑
            </Alert.Cancel>
            <Alert.Action
              className="btn btn-gold px-4 py-2"
              onClick={onDiscard}
            >
              放弃修改并切换
            </Alert.Action>
          </div>
        </Alert.Content>
      </Alert.Portal>
    </Alert.Root>
  );
}
export type View =
  | "overview"
  | "desk"
  | "studio"
  | "ledger"
  | "changes"
  | "review"
  | "audit"
  | "model";
export const kindLabels: Record<string, string> = {
  "story.premise": "故事前提",
  "story.character": "人物",
  "story.relationship": "人物关系",
  "story.place": "地点",
  "story.world_rule": "世界规则",
  "story.mystery_truth": "案件真相",
  "story.clue": "线索",
  "story.red_herring": "误导",
  "story.foreshadow": "伏笔",
  "story.timeline_event": "时间线",
  "story.truth_fact": "真实事实",
  "story.knowledge": "角色认知",
  "story.disclosure": "读者披露",
  "narrative.series": "系列",
  "narrative.book": "单本",
  "narrative.arc": "故事弧",
  "narrative.chapter": "叙事章",
  "narrative.scene": "场景",
  "narrative.beat": "节拍",
};
export const fieldLabels: Record<string, string> = {
  text: "正文",
  name: "名称",
  title: "标题",
  statement: "事实表述",
  description: "说明",
  identity: "身份",
  role: "故事作用",
  goal: "目标",
  need: "内在需要",
  secret: "秘密",
  arc: "人物变化",
  traits: "特征",
  purpose: "叙事目的",
  parent_id: "所属结构",
  protagonist_id: "主角",
  pov_character_id: "视角人物",
  character_id: "角色",
  participants: "参与人物",
  preconditions: "前置条件",
  constraints: "约束",
  source: "来源",
  basis: "依据",
  resolution: "揭穿方式",
  false_explanation: "错误解释",
  culprit_id: "凶手",
  motive: "动机",
  method: "手法",
  cover_up: "掩饰",
  accomplice_ids: "共犯",
  true_timeline: "真实时间线",
  fact_id: "关联事实",
  event_id: "关联事件",
  chapter_id: "关联章节",
  order_key: "顺序",
  status: "状态",
  known_at: "获知时机",
  revealed_at: "披露时机",
  evidence: "证据",
  knowledge_state: "认知状态",
  reader_state: "读者状态",
  book_id: "所属单本",
  reveals: "披露",
  summary: "摘要",
  content: "内容",
};
export const titleOf = (a: {
  artifact_id: string;
  content: Record<string, unknown>;
}) =>
  String(
    a.content.name ||
      a.content.title ||
      a.content.statement ||
      a.content.purpose ||
      a.artifact_id,
  );
export function display(
  value: unknown,
  names: Record<string, string> = {},
): string {
  if (value === null || value === undefined || value === "") return "—";
  if (Array.isArray(value))
    return value.map((v) => display(v, names)).join("、") || "—";
  if (typeof value === "object")
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${fieldLabels[k] ?? k}：${display(v, names)}`)
      .join("；");
  return names[String(value)] ?? String(value);
}
export const changeLabels: Record<string, string> = {
  opened: "待分析",
  analyzing: "分析中",
  analyzed: "已分析",
  approved: "已批准",
  executing: "执行中",
  review: "待审阅",
  committed: "已提交",
  failed: "执行失败",
  abandoned: "已放弃",
};
export const roleLabels: Record<string, string> = {
  planner: "策划",
  writer: "写作",
  auditor: "审计",
  style_observer: "风格观察",
  coordinator: "协作主编",
  reviser: "修订",
  "manuscript.analysis": "正文分析",
};
export const jobLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  waiting: "等待审阅",
  completed: "任务完成",
  cancelled: "已取消",
  retryable_failed: "等待重试",
  terminal_failed: "执行失败",
};

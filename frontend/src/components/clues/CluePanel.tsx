/**
 * 线索只读面板（v1.3 P4）。
 *
 * 两个入口共用同一套渲染：本章信息面板里的「本章线索」，以及工具栏「线索」打开的
 * 全书清单。这里只有展示，没有任何写入入口——线索由章提交登记，重锚在修订回合做。
 * 文案刻意保留系统事实的口吻：状态、埋设/回收章、锚前缀，不做「可疑」「重要」这类评价。
 */

import type {
  BookCluesView,
  ChapterCluesView,
  ClueEntryView,
  ClueFindingView,
  CluePlanView,
  ClueStatus,
} from "../../api/writing";

const STATUS: Record<ClueStatus, { label: string; tone: string }> = {
  planted: { label: "未回收", tone: "text-amber" },
  advanced: { label: "已推进", tone: "text-blue" },
  resolved: { label: "已回收", tone: "text-green" },
  retired: { label: "不收", tone: "text-mute" },
};

function statusOf(status: string): { label: string; tone: string } {
  return STATUS[status as ClueStatus] ?? { label: status, tone: "text-mute" };
}

function chapterLabel(chapter: number | null): string {
  return chapter === null ? "—" : `第 ${chapter} 章`;
}

function metaOf(entry: ClueEntryView): string {
  const bits = [`${chapterLabel(entry.planted_in)}埋`];
  if (entry.resolved_in !== null) bits.push(`${chapterLabel(entry.resolved_in)}收`);
  if (entry.anchor_prefix) bits.push(`锚「${entry.anchor_prefix}…」`);
  else if (entry.status === "planted" || entry.status === "advanced") bits.push("未锚定");
  if (entry.origin === "migrated") bits.push("迁移待重锚");
  return bits.join(" · ");
}

export function ClueEntryList({ entries, empty }: { entries: ClueEntryView[]; empty: string }) {
  if (entries.length === 0) return <p className="chapter-margin-empty">{empty}</p>;
  return <ul>
    {entries.map(entry => <li key={`${entry.id}:${entry.status}:${entry.resolved_in ?? ""}`}>
      <span className="chapter-margin-label">
        <span className="text-mute">{entry.id}</span>
        {entry.note ? ` ${entry.note}` : "（未命名线索）"}
        <span className={`ml-1 text-xs ${statusOf(entry.status).tone}`}>{statusOf(entry.status).label}</span>
      </span>
      <div className="chapter-margin-occurrences">
        <small>{metaOf(entry)}</small>
      </div>
    </li>)}
  </ul>;
}

export function CluePlanList({ plans, empty }: { plans: CluePlanView[]; empty: string }) {
  if (plans.length === 0) return <p className="chapter-margin-empty">{empty}</p>;
  return <ul>
    {plans.map(plan => <li key={plan.id}>
      <span className="chapter-margin-label">
        <span className="text-mute">{plan.id}</span>
        {plan.note ? ` ${plan.note}` : ""}
      </span>
      <div className="chapter-margin-occurrences">
        <small>
          第 {plan.plant_chapter} 章埋
          {plan.resolve_chapter !== null ? `、第 ${plan.resolve_chapter} 章收` : ""}
        </small>
      </div>
    </li>)}
  </ul>;
}

export function ClueFindingList({ findings }: { findings: ClueFindingView[] }) {
  if (findings.length === 0) return null;
  return <section>
    <h3>需要注意</h3>
    <ul>
      {findings.map((finding, index) => <li key={`${finding.code}:${index}`}>
        <span className="chapter-margin-label">{finding.message}</span>
        <div className="chapter-margin-occurrences"><small>{finding.code}</small></div>
      </li>)}
    </ul>
  </section>;
}

/** 本章线索（嵌在「本章信息」面板与线索弹窗里）。 */
export function ChapterClues({ clues, heading = "本章线索" }: { clues: ChapterCluesView; heading?: string }) {
  return <>
    <section>
      <h3>{heading}</h3>
      <ClueEntryList entries={clues.entries} empty="这一章还没有登记线索。" />
    </section>
    {clues.plans.length > 0 && <section>
      <h3>本章计划</h3>
      <CluePlanList plans={clues.plans} empty="" />
    </section>}
    <ClueFindingList findings={clues.findings} />
  </>;
}

const COUNT_ORDER: { status: ClueStatus; label: string }[] = [
  { status: "planted", label: "未回收" },
  { status: "advanced", label: "已推进" },
  { status: "resolved", label: "已回收" },
  { status: "retired", label: "不收" },
];

/** 全书线索（工具栏「线索」弹窗）。 */
export function BookClues({ clues }: { clues: BookCluesView }) {
  const counts = COUNT_ORDER
    .map(({ status, label }) => `${label} ${clues.entries.filter(entry => entry.status === status).length}`)
    .join("、");
  const pending = clues.plans.filter(plan => !clues.entries.some(entry => entry.id === plan.id));
  if (clues.entries.length === 0 && clues.plans.length === 0) {
    return <p className="chapter-margin-empty">这本书还没有线索登记。写正文时登记的第一条线索会出现在这里。</p>;
  }
  return <div className="chapter-margin">
    <header className="chapter-margin-header">
      <h2>全书线索</h2>
      <p>共 {clues.entries.length} 条：{counts}。线索随章登记，这里只读。</p>
    </header>
    <section>
      <ClueEntryList entries={clues.entries} empty="还没有任何线索登记。" />
    </section>
    {pending.length > 0 && <section>
      <h3>计划未登记</h3>
      <CluePlanList plans={pending} empty="" />
    </section>}
    <ClueFindingList findings={clues.findings} />
  </div>;
}

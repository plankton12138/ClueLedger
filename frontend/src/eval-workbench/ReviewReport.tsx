import { useEffect, useId, useRef, useState } from "react";

import { fetchDiff, fetchInput } from "./api";
import type { AttemptRow, CaseView, CheckRow, CheckTarget, DiffView, Report, Review } from "./api";
import { caseName, checkLabels, modeShort, runDate } from "./labels";
import type { ReportFocus } from "./routing";
import "./review-report.css";

const CHECK_NAMES: Record<string, string> = {
  chapter_in_model_input: "读取原文检查",
  only_region_changed: "只改授权范围",
  region_actually_changed: "目标段落有改动",
  preserved_verbatim: "指定原文保留",
  committed_via_candidate: "入稿回执检查",
};

const ANSWERS = [
  ["pass", "是"],
  ["fail", "否"],
  ["pending_review", "证据不足"],
] as const;

type Verdict = typeof ANSWERS[number][0];
type OpenDetail = (caseId: string, attempt: number, focus: ReportFocus, checkId?: string) => void;
type SaveReview = (review: Review) => Promise<void>;

function reviewFor(reviews: Review[], row: AttemptRow, check: CheckRow) {
  return reviews.find(item => item.case_id === row.case_id && item.attempt === row.attempt && item.check_id === check.id);
}

function findings(rows: AttemptRow[], reviews: Review[]) {
  let required = 0, quality = 0, diagnostic = 0, pending = 0, insufficient = 0, abnormal = 0, rejected = 0;
  for (const row of rows) {
    if (row.status === "infrastructure_error" || row.status === "not_run") abnormal++;
    for (const check of row.checks || []) {
      const review = reviewFor(reviews, row, check);
      const verdict = check.level === "human" ? review?.verdict || check.verdict : check.verdict;
      if (verdict === "fail") {
        if (check.level === "human") rejected++;
        else if (check.role === "diagnostic") diagnostic++;
        else if (check.level === "must") required++;
        else if (check.level === "quality") quality++;
      }
      if (verdict === "pending_review" || verdict === "uncertain") {
        if (review?.verdict === "pending_review") insufficient++;
        else pending++;
      }
    }
  }
  const summary = checkLabels({ required, pending, insufficient, rejected, quality, diagnostic, abnormal })
    .map(item => item.text).join("，");
  const priority = required ? 0 : pending || insufficient ? 1 : quality || diagnostic || abnormal || rejected ? 2 : 3;
  return { summary, priority };
}

function excerpt(text: string, region: DiffView["region"]) {
  const { from, to } = region;
  if (!from) return text.trim();
  const start = text.indexOf(from);
  if (start < 0 || text.indexOf(from, start + from.length) >= 0) return "";
  if (!to || to === "END") return text.slice(start).trim();
  const end = text.indexOf(to, start + from.length);
  return end < 0 ? "" : text.slice(start, end).trim();
}

function changedParagraphs(before: string, after: string) {
  const oldParts = before.split(/\r?\n\s*\r?\n/);
  const newParts = after.split(/\r?\n\s*\r?\n/);
  let start = 0;
  while (start < Math.min(oldParts.length, newParts.length) && oldParts[start] === newParts[start]) start++;
  let tail = 0;
  while (tail < Math.min(oldParts.length, newParts.length) - start &&
    oldParts[oldParts.length - 1 - tail] === newParts[newParts.length - 1 - tail]) tail++;
  return {
    before: oldParts.slice(start, oldParts.length - tail).join("\n\n"),
    after: newParts.slice(start, newParts.length - tail).join("\n\n"),
  };
}

function ReviewQuestion({ row, check, review, diff, evidenceStatus, saving, onSave, focused }: {
  row: AttemptRow;
  check: CheckRow;
  review?: Review;
  diff: DiffView | null;
  evidenceStatus: string;
  saving: boolean;
  onSave: SaveReview;
  focused: boolean;
}) {
  const questionId = useId();
  const [draft, setDraft] = useState<Verdict>();
  const [editing, setEditing] = useState(!review && !["pass", "fail"].includes(check.verdict));
  const [saveError, setSaveError] = useState("");
  const fileRefs = (check.evidence || []).filter(ref => ref.type === "file" && ref.path);
  const fileRef = diff && fileRefs.find(ref => ref.path === diff.path && (!ref.anchor || ref.anchor === diff.region.from));
  const relevant = diff && (!fileRefs.length || fileRef) ? diff : null;
  const before = relevant ? excerpt(relevant.before, relevant.region) : "";
  const after = relevant && !relevant.diff.removed?.includes(relevant.path) ? excerpt(relevant.after, relevant.region) : "";
  const changed = changedParagraphs(before, after);
  const evidenceRef = relevant?.path && after
    ? `after:${relevant.path}${relevant.region.from ? ` ¶${relevant.region.from}` : ""}`
    : "";
  const stored = review?.verdict || check.verdict;
  const answer = ANSWERS.find(([value]) => value === stored)?.[1];

  async function save() {
    if (!draft || saving || (draft !== "pending_review" && !evidenceRef)) return;
    setSaveError("");
    try {
      await onSave({
        ...review,
        case_id: row.case_id,
        attempt: row.attempt,
        check_id: check.id,
        verdict: draft,
        evidence_ref: evidenceRef,
      });
      setEditing(false);
    } catch (error) {
      setSaveError(String(error instanceof Error ? error.message : error));
    }
  }

  return (
    <section className="rr-question" aria-labelledby={questionId} data-report-focus={focused || undefined} tabIndex={-1}>
      <div className="rr-category">作者复核</div>
      <h2 id={questionId}>{check.reason || "确认改稿结果"}</h2>
      {editing ? (
        <>
          {after ? (
            <div className="rr-excerpt">
              <div className="rr-meta">改后正文 / {relevant?.path.split("/").pop()?.replace(/\.md$/, "")}</div>
              <blockquote>{changed.after.trim() || after}</blockquote>
              {changed.after && changed.after.trim() !== after && (
                <details><summary>查看完整改后段落</summary><blockquote>{after}</blockquote></details>
              )}
              {before && <details><summary>对照改前</summary><blockquote>{changed.before.trim() || before}</blockquote></details>}
            </div>
          ) : <p className="rr-meta" role="status">{evidenceStatus || "本题缺少可定位的改后正文。"}</p>}
          <form className="rr-answer" onSubmit={event => { event.preventDefault(); void save(); }}>
            <fieldset aria-label="你的判断" disabled={saving}>
              {ANSWERS.map(([value, label]) => (
                <label key={value}>
                  <input name={questionId} type="radio" checked={draft === value}
                    disabled={value !== "pending_review" && !evidenceRef}
                    onChange={() => setDraft(value)} />
                  {label}
                </label>
              ))}
            </fieldset>
            <button type="submit" className="rr-primary"
              disabled={saving || !draft || (draft !== "pending_review" && !evidenceRef)}>
              {saving ? "正在保存…" : "保存判断"}
            </button>
          </form>
          {saveError && <p className="wb-error" role="alert">{saveError}</p>}
        </>
      ) : (
        <div className="rr-review-result">
          <span role="status">{stored === "pending_review" ? "已查看，待补证" : `已复核 · ${answer || stored}`}</span>
          <button type="button" className="rr-link" onClick={() => {
            setDraft(ANSWERS.find(([value]) => value === stored)?.[0]);
            setEditing(true);
          }}>重新查看</button>
        </div>
      )}
    </section>
  );
}

function InputSnapshot({ runId, row, operationId, index }: {
  runId: string; row: AttemptRow; operationId: string; index: number;
}) {
  const [content, setContent] = useState("");
  const [loading, setLoading] = useState(false);
  async function open() {
    setLoading(true);
    try {
      setContent(JSON.stringify(await fetchInput(runId, row.case_id, row.attempt, operationId), null, 2));
    } catch (error) {
      setContent(String(error instanceof Error ? error.message : error));
    } finally {
      setLoading(false);
    }
  }
  return (
    <details onToggle={event => { if (event.currentTarget.open && !content && !loading) void open(); }}>
      <summary>输入快照 {index + 1}</summary>
      {loading ? <p role="status">正在读取…</p> : <pre>{content}</pre>}
    </details>
  );
}

function AttemptReport({ runId, row, reviews, saving, onSave, onDetail, showDetails, target }: {
  runId: string; row: AttemptRow; reviews: Review[]; saving: boolean; onSave: SaveReview; onDetail: OpenDetail;
  showDetails: boolean;
  target?: CheckTarget;
}) {
  const [diff, setDiff] = useState<DiffView | null>(null);
  const [evidenceStatus, setEvidenceStatus] = useState("正在读取正文…");
  const checks = row.checks || [];
  const questions = checks.filter(check => check.level === "human");
  const needsDiff = questions.length > 0 || checks.some(check => check.evidence?.some(ref => ref.type === "file"));
  useEffect(() => {
    if (!needsDiff || row.status !== "completed") return;
    let active = true;
    void fetchDiff(runId, row.case_id, row.attempt).then(result => {
      if (active) { setDiff(result); setEvidenceStatus(""); }
    }, error => {
      if (active) setEvidenceStatus(error instanceof Error ? error.message : String(error));
    });
    return () => { active = false; };
  }, [runId, row.case_id, row.attempt, row.status, needsDiff]);

  function failedCheck(check: CheckRow) {
    const inputs = (check.evidence || []).flatMap(ref => ref.type === "input" && ref.operation_id ? [ref.operation_id] : []);
    const mandatory = check.level === "must" && check.role !== "diagnostic";
    return (
      <details className="rr-disclosure" key={check.id} open={target?.checkId === check.id || undefined}
        data-report-focus={target?.checkId === check.id || undefined} tabIndex={-1}>
        <summary>
          <span className={mandatory ? "rr-danger" : "rr-warning"}>
            {mandatory ? "必须项失败" : check.role === "diagnostic" ? "诊断异常" : "质量异常"}
          </span>
          <strong>{CHECK_NAMES[check.id] || check.reason}{CHECK_NAMES[check.id] ? "未通过" : ""}</strong>
        </summary>
        <div className="rr-detail">
          {CHECK_NAMES[check.id] && <p>{check.reason}</p>}
          {(check.evidence || []).filter(ref => ref.type !== "input").map((ref, index) => (
            <p className="rr-meta" key={index}>
              {[ref.path, ref.anchor, ref.fragment, ref.expected, ref.text, ref.span_id].filter(Boolean).join(" · ")}
            </p>
          ))}
          {inputs.map((id, index) => <InputSnapshot key={`${id}:${index}`} runId={runId} row={row} operationId={id} index={index} />)}
          <button type="button" className="rr-link" onClick={() => onDetail(row.case_id, row.attempt, "checks", check.id)}>查看运行证据 →</button>
        </div>
      </details>
    );
  }

  const passed = checks.filter(check => check.level !== "human" && check.verdict === "pass");
  const result = findings([row], reviews);
  const edited = checks.find(check => check.id === "region_actually_changed")?.verdict;
  return (
    <div className="rr-attempt" data-report-focus={target && !target.checkId ? true : undefined} tabIndex={-1}>
      <p className="rr-summary">
        {edited === "pass" && "目标段落已改动。"}
        {edited === "fail" && "目标段落未改动。"}
        {result.summary ? `${result.summary}。` : "没有待处理检查项。"}
      </p>
      {row.error && <p className="wb-error" role="alert">{row.error}</p>}
      {checks.filter(check => check.level === "must" && check.role !== "diagnostic" && check.verdict === "fail").map(failedCheck)}
      {row.status === "completed" && questions.map(check => (
        <ReviewQuestion key={check.id} row={row} check={check}
          review={reviewFor(reviews, row, check)} diff={diff} evidenceStatus={evidenceStatus}
          saving={saving} onSave={onSave} focused={target?.checkId === check.id} />
      ))}
      {checks.filter(check => check.level !== "human" && (check.level === "quality" || check.role === "diagnostic") &&
        check.verdict === "fail").map(failedCheck)}
      {passed.length > 0 && (
        <details className="rr-disclosure">
          <summary><span className="rr-meta">程序检查</span><strong>{passed.length} 项记录为通过</strong></summary>
          <ul className="rr-detail">{passed.map(check => <li key={check.id}>{CHECK_NAMES[check.id] || check.reason}</li>)}</ul>
        </details>
      )}
      {showDetails && <details className="rr-disclosure">
        <summary><span className="rr-meta">试验信息</span><strong>第 {row.attempt} 次试验</strong></summary>
        <div className="rr-detail">
          {row.model_requests != null && <p>模型请求 {row.model_requests} 次</p>}
          <button type="button" className="rr-link" onClick={() => onDetail(row.case_id, row.attempt, "checks")}>查看全部检查与运行轨迹 →</button>
        </div>
      </details>}
    </div>
  );
}

export function ReviewReport({ runId, report, reviews, saving, onSave, onDetail, target }: {
  runId: string; report: Report; reviews: Review[]; saving: boolean; onSave: SaveReview; onDetail: OpenDetail;
  target: CheckTarget | null;
}) {
  const cases: CaseView[] = report.cases?.length ? report.cases : Array.from(
    new Set((report.attempts || []).map(row => row.case_id)),
    id => ({ case_id: id, attempts: (report.attempts || []).filter(row => row.case_id === id) }),
  );
  const ordered = cases.map(item => ({ item, ...findings(item.attempts || [], reviews) }))
    .sort((a, b) => a.priority - b.priority);
  const [opened, setOpened] = useState<Record<string, boolean>>({});
  const root = useRef<HTMLElement>(null);
  const focusCase = target?.caseId;
  const focusAttempt = target?.attempt;
  const focusCheck = target?.checkId;
  useEffect(() => {
    if (!focusCase) return;
    const element = root.current?.querySelector<HTMLElement>("[data-report-focus]");
    element?.focus({ preventScroll: true });
    element?.scrollIntoView({ block: "nearest" });
  }, [focusCase, focusAttempt, focusCheck]);
  const singleRow = ordered.length === 1 && ordered[0].item.attempts.length === 1
    ? ordered[0].item.attempts[0] : null;
  const identity = report.identity || {};
  const date = runDate(runId, identity.started_at);

  function renderAttempt(row: AttemptRow) {
    return <AttemptReport runId={runId} row={row} reviews={reviews} saving={saving} onSave={onSave} onDetail={onDetail}
      showDetails={!singleRow} target={target?.caseId === row.case_id && target.attempt === row.attempt ? target : undefined} />;
  }

  function renderCase(item: CaseView) {
    if (!item.attempts?.length) return <p className="rr-meta">没有试验记录。</p>;
    if (item.attempts.length === 1) return renderAttempt(item.attempts[0]);
    return item.attempts.map(row => {
      const key = `${item.case_id}:${row.attempt}`;
      const focused = focusCase === row.case_id && focusAttempt === row.attempt;
      return <details className="rr-disclosure" key={key} open={focused || undefined} onToggle={event => {
        if (event.currentTarget.open) setOpened(previous => ({ ...previous, [key]: true }));
      }}>
        <summary>第 {row.attempt} 次试验 {findings([row], reviews).summary}</summary>
        {(opened[key] || focused) && renderAttempt(row)}
      </details>;
    });
  }

  return (
    <main className="rr-page" ref={root}>
      <header className="rr-title">
        <div className="rr-meta">
          {date && `${date.toLocaleString("zh-CN", { dateStyle: "short", timeStyle: "short" })} · `}{modeShort(report.mode)}
        </div>
        <h1>{singleRow ? caseName(singleRow.case_id) : "评测报告"}</h1>
      </header>
      {ordered.length === 0 && <p>这次运行没有案例记录。</p>}
      {singleRow ? renderAttempt(singleRow) : ordered.map(({ item, summary }) => (
        <details className="rr-disclosure rr-case" key={item.case_id} open={focusCase === item.case_id || undefined} onToggle={event => {
          if (event.currentTarget.open) setOpened(previous => ({ ...previous, [item.case_id]: true }));
        }}>
          <summary><strong>{caseName(item.case_id)}</strong>{summary && <span className="rr-meta">{summary}</span>}</summary>
          {(opened[item.case_id] || focusCase === item.case_id) && renderCase(item)}
        </details>
      ))}
      <details className="rr-disclosure rr-run-info">
        <summary><strong>运行信息</strong></summary>
        <dl className="rr-detail">
          <dt>记录</dt><dd>{runId}</dd>
          {identity.git_commit != null && <><dt>代码版本</dt><dd>{String(identity.git_commit)}</dd></>}
          {identity.model != null && <><dt>模型</dt><dd>{String(identity.model)}</dd></>}
          {identity.budget_used != null && <><dt>调用预算</dt><dd>{String(identity.budget_used)} / {String(identity.budget_max ?? "—")}</dd></>}
          {report.suite_id && <><dt>套件</dt><dd>{report.suite_id}</dd></>}
          {report.env_id && <><dt>环境</dt><dd>{report.env_id}</dd></>}
          {report.evaluator_id && <><dt>评测器</dt><dd>{report.evaluator_id}{report.evaluator_version != null ? ` v${report.evaluator_version}` : ""}</dd></>}
          {singleRow?.model_requests != null && <><dt>模型请求</dt><dd>{singleRow.model_requests} 次</dd></>}
          <dt>报告门禁</dt><dd>{report.gate?.passed == null ? "未判定" : report.gate.passed ? "通过" : "未通过"}</dd>
          <dt>试验统计</dt><dd>通过 {report.summary?.pass || 0} · 失败 {report.summary?.fail || 0} · 待复核 {report.summary?.pending_review || 0}</dd>
          {[...(report.gate?.blockers || []), ...(report.gate?.warnings || [])].map((item, index) =>
            <dd className="rr-gate-detail" key={index}>{caseName(item.case_id)} · {item.detail}</dd>)}
        </dl>
        {singleRow && <button type="button" className="rr-link"
          onClick={() => onDetail(singleRow.case_id, singleRow.attempt, "checks")}>查看全部检查与运行轨迹 →</button>}
      </details>
    </main>
  );
}

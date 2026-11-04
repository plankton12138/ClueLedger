/**
 * 修订与写作约定抽屉（v1.2）。
 *
 * 左边是这一章已入稿的正文改动，右边是所选改动的段落差异。分析、采纳与写作约定
 * 编辑都在同一个面板里完成：分析结论按「写作约定 / 全书结构 / 模块 / 本章」分组，
 * 作者可以只选其中几条写回 STYLE.md，也可以直接编辑整份约定。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { X } from "lucide-react";

import {
  applyModuleDecisions,
  applyRevisionAnalysis,
  applyStructureDecisions,
  listRevisions,
  readRevision,
  readRevisionAnalysis,
  readStyle,
  saveStyle,
  startRevisionAnalysis,
  type RevisionAnalysis,
  type RevisionDetail,
  type RevisionHunk,
  type RevisionSummary,
  type StyleDocument,
} from "../../api/revisions";
import { writingKey } from "../../api/writing";
import { useModuleLibraryStore } from "../../state/moduleLibrary";
import { errorMessage } from "../../api/errors";
import { Button } from "../ui/Button";

const SOURCE_LABEL: Record<string, string> = {
  author: "作者修订",
  model: "按作者要求改稿",
  candidate: "采用模型候选",
  author_after_model: "按作者要求改稿并由作者编辑",
};

const TARGET_LABEL: Record<string, string> = {
  style: "写作约定",
  structure: "全书结构",
  module_entry: "模块资料",
  chapter_only: "仅当前场景",
};

function timeLabel(value: string): string {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return `${date.getMonth() + 1}月${date.getDate()}日 ${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
}

function AnalysisLog({ analysis }: { analysis: RevisionAnalysis }) {
  const trace = analysis.trace;
  if (!trace || (!trace.reads?.length && !trace.steps?.length)) return null;
  return (
    <details className="mt-3 rounded-sm border border-[#e2dbc6] p-2 text-[11px] text-[#6b5d3e]">
      <summary className="cursor-pointer select-none">查看运行日志</summary>
      {trace.model && <p className="mt-1 text-[#8a8267]">模型：{trace.model}</p>}
      {trace.reads?.length > 0 && (
        <div className="mt-2">
          <h5 className="font-semibold">读取的资料</h5>
          <ul className="mt-0.5 space-y-0.5">
            {trace.reads.map(read => (
              <li key={read.label} className="flex gap-2">
                <span className="w-20 shrink-0 text-[#8a8267]">{read.label}</span>
                <span>{read.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {trace.steps?.length > 0 && (
        <div className="mt-2">
          <h5 className="font-semibold">执行步骤</h5>
          <ol className="mt-0.5 space-y-0.5">
            {trace.steps.map((step, index) => (
              <li key={`${step.label}-${index}`} className="flex gap-2">
                <span className="w-20 shrink-0 text-[#8a8267]">{timeLabel(step.at)}</span>
                <span className={step.status === "failed" ? "text-[#8d3a2a]" : ""}>
                  {step.label}
                  {step.detail ? ` · ${step.detail}` : ""}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </details>
  );
}

function Hunk({ hunk }: { hunk: RevisionHunk }) {
  return (
    <li className="rounded-sm border border-[#e2dbc6] bg-[#fffdf6] p-3">
      <div className="flex items-center gap-2 text-[11px] text-[#8a8267]">
        <span className="font-semibold text-[#6b5d3e]">
          {hunk.op === "replace" ? "改写" : hunk.op === "insert" ? "新增" : "删除"}
        </span>
        {!hunk.active && (
          <span className="rounded-sm bg-[#f1ead7] px-1.5 py-0.5 text-[#8a7451]">
            已在后续改稿中失效
          </span>
        )}
      </div>
      {hunk.before && (
        <p className="mt-2 whitespace-pre-wrap break-words text-[13px] leading-7 text-[#4b4737]">
          {hunk.inline.length > 0 || hunk.op !== "delete"
            ? hunk.inline
                .filter(span => span.kind !== "insert")
                .map((span, index) =>
                  span.kind === "delete" ? (
                    <del key={index} className="bg-[#f6dcd7] text-[#8d3a2a] decoration-[#b4553f]">
                      {span.text}
                    </del>
                  ) : (
                    <span key={index}>{span.text}</span>
                  ),
                )
            : (
              <del className="bg-[#f6dcd7] text-[#8d3a2a] decoration-[#b4553f]">{hunk.before}</del>
            )}
        </p>
      )}
      {hunk.after && (
        <p className="mt-1 whitespace-pre-wrap break-words text-[13px] leading-7 text-[#4b4737]">
          {hunk.inline.length > 0
            ? hunk.inline
                .filter(span => span.kind !== "delete")
                .map((span, index) =>
                  span.kind === "insert" ? (
                    <ins key={index} className="bg-[#e3eed6] text-[#3e6231] no-underline">
                      {span.text}
                    </ins>
                  ) : (
                    <span key={index}>{span.text}</span>
                  ),
                )
            : (
              <ins className="bg-[#e3eed6] text-[#3e6231] no-underline">{hunk.after}</ins>
            )}
        </p>
      )}
    </li>
  );
}

export default function RevisionDrawer({
  workspaceId,
  chapterId,
  chapterTitle,
  initialTab = "revisions",
  initialRevisionId = null,
  autoAnalyze = false,
  conversationId = null,
  onClose,
}: {
  workspaceId: string;
  chapterId: string | null;
  chapterTitle: string;
  initialTab?: "revisions" | "style";
  initialRevisionId?: string | null;
  autoAnalyze?: boolean;
  conversationId?: string | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<"revisions" | "style">(
    chapterId ? initialTab : "style",
  );
  const [items, setItems] = useState<RevisionSummary[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<RevisionDetail | null>(null);
  const [analysis, setAnalysis] = useState<RevisionAnalysis | null>(null);
  const [style, setStyle] = useState<StyleDocument | null>(null);
  const [styleDraft, setStyleDraft] = useState("");
  const [styleDirty, setStyleDirty] = useState(false);
  const [chosen, setChosen] = useState<Set<number>>(new Set());
  const [chosenStructure, setChosenStructure] = useState<Set<number>>(new Set());
  const [chosenModules, setChosenModules] = useState<Set<number>>(new Set());
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const autoStarted = useRef(false);
  const panel = useRef<HTMLElement>(null);

  useEffect(() => {
    panel.current?.scrollIntoView({ block: "nearest" });
  }, []);

  const loadStyle = useCallback(async () => {
    const document = await readStyle(workspaceId);
    setStyle(document);
    setStyleDraft(document.text);
    setStyleDirty(false);
  }, [workspaceId]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        if (chapterId) {
          setBusy("读取修订");
          const page = await listRevisions(workspaceId, chapterId);
          if (cancelled) return;
          setItems(page.items);
          const preferred = initialRevisionId
            ? page.items.find(item => item.id === initialRevisionId)
            : undefined;
          setSelected(preferred?.id ?? page.items[0]?.id ?? null);
        } else {
          setItems([]);
          setSelected(null);
        }
        await loadStyle();
      } catch (exc) {
        if (!cancelled) setError(errorMessage(exc, "修订"));
      } finally {
        if (!cancelled) setBusy(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, chapterId, initialRevisionId, loadStyle]);

  useEffect(() => {
    if (!selected) {
      setDetail(null);
      setAnalysis(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const next = await readRevision(workspaceId, selected, conversationId);
        if (!cancelled) {
          setDetail(next);
          setAnalysis(next.latest_analysis);
          setChosen(new Set(next.latest_analysis?.decisions.flatMap((decision, index) =>
            decision.target === "style" && !decision.applied ? [index] : [],
          )));
          setChosenStructure(new Set());
          setChosenModules(new Set());
          setNotice(null);
        }
      } catch (exc) {
        if (!cancelled) setError(errorMessage(exc, "修订"));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [workspaceId, selected, conversationId]);

  useEffect(() => {
    if (!analysis || (analysis.status !== "queued" && analysis.status !== "running")) return;
    let active = true;
    let timer: number;
    function poll() {
      if (!analysis) return;
      void readRevisionAnalysis(workspaceId, analysis.id)
        .then(next => {
          if (!active) return;
          setAnalysis(next);
          setError(null);
          if (next.status === "ready") {
            setChosen(new Set(next.decisions.map((_, index) => index).filter(
              index => next.decisions[index].target === "style",
            )));
          }
        })
        .catch(exc => {
          if (!active) return;
          setError(errorMessage(exc, "修订"));
          timer = window.setTimeout(poll, 3000);
        });
    }
    timer = window.setTimeout(poll, 900);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [analysis, workspaceId]);

  const activeHunks = useMemo(
    () => detail?.hunks.filter(hunk => hunk.active) ?? [],
    [detail],
  );

  useEffect(() => {
    if (!autoAnalyze || autoStarted.current || !detail || !detail.analyzable) return;
    if (analysis) return;
    autoStarted.current = true;
    void analyze();
    // analyze 只依赖当前详情；自动发起一次后由 autoStarted 挡住重复。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoAnalyze, detail, analysis]);

  async function analyze() {
    if (!detail || !detail.analyzable) return;
    setBusy("分析中");
    setError(null);
    setNotice(null);
    try {
      const result = await startRevisionAnalysis(workspaceId, detail.revision.id, {
        hunk_ids: activeHunks.map(hunk => hunk.id),
        conversation_id: conversationId ?? "",
        request_key: writingKey("analysis"),
      });
      setAnalysis(result);
    } catch (exc) {
      setError(errorMessage(exc, "修订"));
    } finally {
      setBusy(null);
    }
  }

  async function saveSelected() {
    if (!analysis) return;
    const indexes = [...chosen].sort((a, b) => a - b);
    setBusy("保存写作约定");
    setError(null);
    try {
      await applyRevisionAnalysis(workspaceId, analysis.id, {
        text: analysis.proposed_style_text,
        expected_digest: analysis.style_digest || null,
        decision_indexes: indexes,
        request_key: writingKey("apply"),
      });
      await loadStyle();
      setNotice("已保存到写作约定；下一轮写作会用新版本。");
      setAnalysis({ ...analysis, decisions: analysis.decisions.map((row, index) => (chosen.has(index) ? { ...row, applied: true } : row)) });
    } catch (exc) {
      setError(errorMessage(exc, "修订"));
    } finally {
      setBusy(null);
    }
  }

  async function applyStructure() {
    if (!analysis) return;
    const indexes = [...chosenStructure].sort((a, b) => a - b);
    if (indexes.length === 0) return;
    setBusy("写入结构");
    setError(null);
    setNotice(null);
    try {
      const result = await applyStructureDecisions(workspaceId, analysis.id, {
        decision_indexes: indexes,
        expected_digest: analysis.structure_digest || null,
      });
      setAnalysis(result.analysis);
      setChosenStructure(new Set());
      setNotice("已写入全书结构；下一次写作按新结构装配。");
    } catch (exc) {
      setError(errorMessage(exc, "修订"));
    } finally {
      setBusy(null);
    }
  }

  async function applyModules() {
    if (!analysis) return;
    const indexes = [...chosenModules].sort((a, b) => a - b);
    if (indexes.length === 0) return;
    setBusy("写入模块");
    setError(null);
    setNotice(null);
    try {
      const result = await applyModuleDecisions(workspaceId, analysis.id, {
        decision_indexes: indexes,
        expected_digest: analysis.assembly_digest || null,
      });
      setAnalysis(result.analysis);
      setChosenModules(new Set());
      setNotice("已写入作品模块；后续写作使用新版本。");
      void useModuleLibraryStore.getState().trackAssembly(workspaceId);
    } catch (exc) {
      setError(errorMessage(exc, "修订"));
    } finally {
      setBusy(null);
    }
  }

  async function saveStyleDraft() {
    setBusy("保存写作约定");
    setError(null);
    try {
      const saved = await saveStyle(workspaceId, {
        text: styleDraft,
        expected_digest: style?.digest ?? null,
        request_key: writingKey("style"),
      });
      setStyle({ path: saved.path, exists: true, text: saved.text, digest: saved.digest, size: saved.text.length });
      setStyleDraft(saved.text);
      setStyleDirty(false);
      setNotice("写作约定已保存。");
    } catch (exc) {
      setError(errorMessage(exc, "修订"));
    } finally {
      setBusy(null);
    }
  }

  function openProposal() {
    if (!analysis) return;
    setStyleDraft(analysis.proposed_style_text);
    setStyleDirty(true);
    setTab("style");
  }

  const styleDecisions = analysis?.decisions
    .map((decision, index) => ({ decision, index }))
    .filter(row => row.decision.target === "style") ?? [];
  const structureDecisions = analysis?.decisions
    .map((decision, index) => ({ decision, index }))
    .filter(row => row.decision.target === "structure") ?? [];
  const moduleDecisions = analysis?.decisions
    .map((decision, index) => ({ decision, index }))
    .filter(row => row.decision.target === "module_entry") ?? [];
  const otherDecisions = analysis?.decisions
    .map((decision, index) => ({ decision, index }))
    .filter(
      row =>
        row.decision.target !== "style"
        && row.decision.target !== "structure"
        && row.decision.target !== "module_entry",
    ) ?? [];

  return (
    <section ref={panel} className="rounded-sm border border-[#cfc7ae] bg-[#fbf8ef]" aria-label="修订与写作约定">
      <div className="flex min-w-0 flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b border-[#e2dbc6] px-3 py-2">
          <h2 className="font-serif text-base font-bold text-sheetInk">
            {chapterId ? `修订 · ${chapterTitle}` : "写作约定"}
          </h2>
          <div className="flex items-center gap-1 text-xs">
            {chapterId && (
              <button
                type="button"
                className={`rounded-sm px-2 py-1 ${tab === "revisions" ? "bg-[#e6ddc4] font-semibold" : "text-[#7a6f52]"}`}
                onClick={() => setTab("revisions")}
              >
                正文修订
              </button>
            )}
            <button
              type="button"
              className={`rounded-sm px-2 py-1 ${tab === "style" ? "bg-[#e6ddc4] font-semibold" : "text-[#7a6f52]"}`}
              onClick={() => setTab("style")}
            >
              写作约定
            </button>
          </div>
          <div className="flex-1" />
          <Button className="px-2 py-1 text-sm" onClick={onClose} aria-label="关闭">
            <X size={15} aria-hidden="true" />
          </Button>
        </div>

        {(error || notice) && (
          <div className={`flex-none px-4 py-2 text-xs ${error ? "bg-[#f8e4de] text-[#8d3a2a]" : "bg-[#edf3e2] text-[#3e6231]"}`}>
            {error ?? notice}
          </div>
        )}

        {tab === "revisions" ? (
          <div>
            <aside className="max-h-32 overflow-auto border-b border-[#e2dbc6] px-2 py-2">
              {items.length === 0 ? (
                <p className="px-2 text-xs leading-6 text-[#8a8267]">
                  这一章还没有可分析的改稿。亲手改一段正文，或用对话让模型改完并保存后，这里会出现记录。
                </p>
              ) : (
                <ul className="space-y-1">
                  {items.map(item => (
                    <li key={item.id}>
                      <button
                        type="button"
                        disabled={busy !== null}
                        onClick={() => {
                          if (item.id === selected) return;
                          setDetail(null);
                          setAnalysis(null);
                          setSelected(item.id);
                        }}
                        className={`w-full rounded-sm px-2 py-2 text-left text-xs ${
                          selected === item.id ? "bg-[#e6ddc4]" : "hover:bg-[#f1ead7]"
                        }`}
                      >
                        <span className="block font-semibold text-[#4b4737]">
                          {SOURCE_LABEL[item.source] ?? item.source}
                        </span>
                        <span className="mt-0.5 block text-[11px] text-[#8a8267]">
                          {timeLabel(item.confirmed_at)} · {item.hunk_count} 处
                          {item.analyzable ? "" : " · 已失效"}
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </aside>

            <div className="min-w-0 px-3 py-3 text-xs text-[#4b4737]">
              {!detail ? (
                <p className="text-[#8a8267]">{busy ?? "选择一次修订查看差异。"}</p>
              ) : (
                <>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-[#8a8267]">
                      {SOURCE_LABEL[detail.revision.source] ?? detail.revision.source} ·{" "}
                      {timeLabel(detail.revision.confirmed_at)}
                    </span>
                    <div className="flex-1" />
                    <Button
                      size="compact"
                      variant="primary"
                      onClick={() => void analyze()}
                      disabled={!detail.analyzable || busy !== null || analysis?.status === "queued" || analysis?.status === "running"}
                      disabledReason="这次改动已不在当前正文里"
                    >
                      让模型分析
                    </Button>
                  </div>
                  {detail.revision.author_message && (
                    <details className="mt-2 rounded-sm border border-[#e2dbc6] bg-[#fffdf6] p-2">
                      <summary className="cursor-pointer text-[11px] text-[#6b5d3e]">展开作者原请求</summary>
                      <p className="mt-1 whitespace-pre-wrap leading-6 text-[#4b4737]">
                        {detail.revision.author_message}
                      </p>
                    </details>
                  )}
                  <ul className="mt-3 space-y-2">
                    {detail.hunks.map(hunk => <Hunk key={hunk.id} hunk={hunk} />)}
                  </ul>

                  {analysis && (
                    <section className="mt-5 rounded-sm border border-[#d8cfb2] bg-[#fffdf6] p-3">
                      <div className="flex items-center gap-2">
                        <h3 className="font-semibold text-[#4b4737]">模型判断</h3>
                        <span role="status" className="text-[11px] text-[#8a8267]">
                          {analysis.status === "ready"
                            ? "已完成"
                            : analysis.status === "failed"
                              ? "失败"
                              : "分析中…"}
                        </span>
                      </div>
                      {analysis.status === "failed" && (
                        <p className="mt-2 text-[#8d3a2a]">{analysis.error}</p>
                      )}
                      <AnalysisLog analysis={analysis} />
                      {analysis.status === "ready" && (
                        <>
                          <p className="mt-2 leading-6">{analysis.summary}</p>
                          {styleDecisions.length > 0 && (
                            <div className="mt-3">
                              <h4 className="text-[11px] font-semibold text-[#6b5d3e]">写作约定建议</h4>
                              <ul className="mt-1 space-y-2">
                                {styleDecisions.map(({ decision, index }) => (
                                  <li key={index} className="rounded-sm border border-[#e2dbc6] p-2">
                                    <label className="flex items-start gap-2">
                                      <input
                                        type="checkbox"
                                        className="mt-1"
                                        checked={chosen.has(index)}
                                        disabled={decision.applied}
                                        onChange={event => {
                                          setChosen(current => {
                                            const next = new Set(current);
                                            if (event.target.checked) next.add(index);
                                            else next.delete(index);
                                            return next;
                                          });
                                        }}
                                      />
                                      <span>
                                        <span className="block whitespace-pre-wrap leading-6">{decision.proposal}</span>
                                        <span className="mt-1 block text-[11px] text-[#8a8267]">
                                          依据：{decision.reason}
                                        </span>
                                        {decision.applied && (
                                          <span className="mt-1 block text-[11px] text-[#3e6231]">已写入</span>
                                        )}
                                      </span>
                                    </label>
                                  </li>
                                ))}
                              </ul>
                              <div className="mt-2 flex gap-2">
                                <Button
                                  size="compact"
                                  onClick={() => void saveSelected()}
                                  disabled={chosen.size === 0 || busy !== null}
                                  disabledReason="先选一条建议"
                                >
                                  保存到写作约定
                                </Button>
                                <Button size="compact" onClick={openProposal}>
                                  编辑后再保存
                                </Button>
                              </div>
                            </div>
                          )}
                          {structureDecisions.length > 0 && (
                            <div className="mt-3">
                              <h4 className="text-[11px] font-semibold text-[#6b5d3e]">全书结构建议</h4>
                              <ul className="mt-1 space-y-2">
                                {structureDecisions.map(({ decision, index }) => {
                                  const appliable = Boolean(
                                    decision.patch_find
                                    && decision.patch_replace
                                    && !decision.patch_error,
                                  );
                                  return (
                                    <li key={index} className="rounded-sm border border-[#e2dbc6] p-2">
                                      <label className="flex items-start gap-2">
                                        <input
                                          type="checkbox"
                                          className="mt-1"
                                          checked={chosenStructure.has(index)}
                                          disabled={!appliable || decision.applied}
                                          onChange={event => {
                                            setChosenStructure(current => {
                                              const next = new Set(current);
                                              if (event.target.checked) next.add(index);
                                              else next.delete(index);
                                              return next;
                                            });
                                          }}
                                        />
                                        <span className="min-w-0">
                                          <span className="block text-[11px] font-semibold text-[#6b5d3e]">
                                            {decision.target_label || "全书结构"}
                                          </span>
                                          <span className="mt-1 block whitespace-pre-wrap leading-6">{decision.proposal}</span>
                                          <span className="mt-1 block text-[11px] text-[#8a8267]">
                                            依据：{decision.reason}
                                          </span>
                                          {appliable && (
                                            <span className="mt-2 block rounded-sm bg-[#fbf8ef] p-2 text-[12px] leading-6">
                                              <del className="bg-[#f6dcd7] text-[#8d3a2a]">{decision.patch_find}</del>
                                              <span className="mx-1 text-[#8a8267]">→</span>
                                              <ins className="bg-[#e3eed6] text-[#3e6231] no-underline">{decision.patch_replace}</ins>
                                            </span>
                                          )}
                                          {decision.patch_error ? (
                                            <span className="mt-1 block text-[11px] text-[#8d3a2a]">
                                              {decision.patch_error}；可在对话中继续说明。
                                            </span>
                                          ) : !appliable && !decision.applied ? (
                                            <span className="mt-1 block text-[11px] text-[#8a8267]">
                                              这条建议没有可直接应用的补丁；可在对话中继续说明。
                                            </span>
                                          ) : null}
                                          {decision.applied && (
                                            <span className="mt-1 block text-[11px] text-[#3e6231]">已写入</span>
                                          )}
                                        </span>
                                      </label>
                                    </li>
                                  );
                                })}
                              </ul>
                              <div className="mt-2 flex gap-2">
                                <Button
                                  size="compact"
                                  onClick={() => void applyStructure()}
                                  disabled={chosenStructure.size === 0 || busy !== null}
                                  disabledReason="先选一条结构建议"
                                >
                                  应用到全书结构
                                </Button>
                              </div>
                            </div>
                          )}
                          {moduleDecisions.length > 0 && (
                            <div className="mt-3">
                              <h4 className="text-[11px] font-semibold text-[#6b5d3e]">模块资料建议</h4>
                              <ul className="mt-1 space-y-2">
                                {moduleDecisions.map(({ decision, index }) => {
                                  const appliable = Boolean(
                                    decision.patch_find
                                    && decision.patch_replace
                                    && !decision.patch_error,
                                  );
                                  return (
                                    <li key={index} className="rounded-sm border border-[#e2dbc6] p-2">
                                      <label className="flex items-start gap-2">
                                        <input
                                          type="checkbox"
                                          className="mt-1"
                                          checked={chosenModules.has(index)}
                                          disabled={!appliable || decision.applied}
                                          onChange={event => {
                                            setChosenModules(current => {
                                              const next = new Set(current);
                                              if (event.target.checked) next.add(index);
                                              else next.delete(index);
                                              return next;
                                            });
                                          }}
                                        />
                                        <span className="min-w-0">
                                          <span className="block text-[11px] font-semibold text-[#6b5d3e]">
                                            {decision.target_label || "模块条目"}
                                          </span>
                                          <span className="mt-1 block whitespace-pre-wrap leading-6">{decision.proposal}</span>
                                          <span className="mt-1 block text-[11px] text-[#8a8267]">
                                            依据：{decision.reason}
                                          </span>
                                          {appliable && (
                                            <span className="mt-2 block rounded-sm bg-[#fbf8ef] p-2 text-[12px] leading-6">
                                              <del className="bg-[#f6dcd7] text-[#8d3a2a]">{decision.patch_find}</del>
                                              <span className="mx-1 text-[#8a8267]">→</span>
                                              <ins className="bg-[#e3eed6] text-[#3e6231] no-underline">{decision.patch_replace}</ins>
                                            </span>
                                          )}
                                          {decision.patch_error ? (
                                            <span className="mt-1 block text-[11px] text-[#8d3a2a]">
                                              {decision.patch_error}；可在对话中继续说明。
                                            </span>
                                          ) : !appliable && !decision.applied ? (
                                            <span className="mt-1 block text-[11px] text-[#8a8267]">
                                              这条建议没有可直接应用的补丁；可在对话中继续说明。
                                            </span>
                                          ) : null}
                                          {decision.applied && (
                                            <span className="mt-1 block text-[11px] text-[#3e6231]">已写入</span>
                                          )}
                                        </span>
                                      </label>
                                    </li>
                                  );
                                })}
                              </ul>
                              <div className="mt-2 flex gap-2">
                                <Button
                                  size="compact"
                                  onClick={() => void applyModules()}
                                  disabled={chosenModules.size === 0 || busy !== null}
                                  disabledReason="先选一条模块建议"
                                >
                                  写入作品模块
                                </Button>
                              </div>
                            </div>
                          )}
                          {otherDecisions.length > 0 && (
                            <div className="mt-3">
                              <h4 className="text-[11px] font-semibold text-[#6b5d3e]">其它去向</h4>
                              <ul className="mt-1 space-y-2">
                                {otherDecisions.map(({ decision, index }) => (
                                  <li key={index} className="rounded-sm border border-[#e2dbc6] p-2">
                                    <span className="block text-[11px] font-semibold text-[#6b5d3e]">
                                      {TARGET_LABEL[decision.target] ?? decision.target}
                                      {decision.target_label ? ` · ${decision.target_label}` : ""}
                                    </span>
                                    {decision.proposal && (
                                      <p className="mt-1 whitespace-pre-wrap leading-6">{decision.proposal}</p>
                                    )}
                                    <p className="mt-1 text-[11px] text-[#8a8267]">依据：{decision.reason}</p>
                                    {decision.evidence && (
                                      <p className="mt-1 text-[11px] text-[#8a8267]">现有内容：{decision.evidence}</p>
                                    )}
                                  </li>
                                ))}
                              </ul>
                              <p className="mt-2 text-[11px] leading-5 text-[#8a8267]">
                                其余改动需要连同具体场景一起确认；在对话里继续说明即可。
                              </p>
                            </div>
                          )}
                        </>
                      )}
                    </section>
                  )}
                </>
              )}
            </div>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col px-4 py-3">
            <p className="text-[11px] leading-5 text-[#8a8267]">
              整份写作约定都会进入每次正文生成。只写可执行的规则；一次换景或单处改句通常不足以成为全书规则。
            </p>
            <textarea
              className="mt-2 min-h-44 resize-y rounded-sm border border-[#d8cfb2] bg-[#fffdf6] p-3 font-mono text-[13px] leading-7 text-[#4b4737] outline-none"
              value={styleDraft}
              onChange={event => {
                setStyleDraft(event.target.value);
                setStyleDirty(true);
              }}
              placeholder="例如：&#10;- 写环境先写人物能感知的细节，不写全知视角。&#10;- 人物回避追问时，先用动作再开口。"
            />
            <div className="mt-2 flex items-center gap-2 text-[11px] text-[#8a8267]">
              <span>{styleDraft.length} 字符</span>
              {styleDirty && <span>有未保存的修改</span>}
              <div className="flex-1" />
              <Button
                size="compact"
                onClick={() => {
                  setStyleDraft(style?.text ?? "");
                  setStyleDirty(false);
                }}
                disabled={!styleDirty}
              >
                撤销修改
              </Button>
              <Button
                size="compact"
                variant="primary"
                onClick={() => void saveStyleDraft()}
                disabled={!styleDirty || busy !== null}
              >
                保存
              </Button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

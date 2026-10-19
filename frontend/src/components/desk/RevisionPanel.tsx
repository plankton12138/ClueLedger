/**
 * 整体调整（V03-7.3/7.4 执行循环的前端入口）。
 *
 * 产品约束（作家桌面 §3/§5）：
 * - 不新增常驻卡片：本视图按需打开，未开始时不占侧栏空间；
 * - 一个主要动作：执行一轮；停止、发布、新建一轮退为轻量按钮；
 * - 状态真实可见：预算「已用/上限」、覆盖「已下结论/总数」、暂存章数都来自服务端；
 * - 预算用完是**等待**不是错误：说清停在哪一章、下一步点「继续」；
 * - 发布是一次提交：成功后正文与账本进入同一个版本。
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  continueTask,
  fetchCurrentTask,
  fetchRevisionWork,
  fetchStaged,
  publishTask,
  runRound,
  startTask,
  stopTask,
  type RevisionTask,
  type RevisionWork,
  type StagedView,
} from "../../api/revision";
import { message } from "../../state/v2";
import { ErrorText, Notice, Pill } from "../Workbench";

const STATE_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "进行中",
  waiting: "等待中",
  completed: "已完成",
  cancelled: "已停止",
  retryable_failed: "失败（可重试）",
  terminal_failed: "失败",
};

const COVERAGE_LABEL: Record<string, string> = {
  unread: "未读",
  read: "已读",
  changed: "已改",
  checked_unchanged: "无需修改",
  blocked: "受阻",
};

const SETTLED = new Set(["changed", "checked_unchanged"]);
const CAN_CONTINUE = new Set([
  "waiting",
  "cancelled",
  "retryable_failed",
  "terminal_failed",
]);

function tone(state: string): "blue" | "green" | "amber" | "red" {
  if (state === "running" || state === "queued") return "blue";
  if (state === "completed") return "green";
  if (state === "waiting") return "amber";
  return "red";
}

export default function RevisionPanel({
  projectId,
  onPublished,
  onBack,
}: {
  projectId: string;
  onPublished(): void;
  onBack(): void;
}) {
  const [task, setTask] = useState<RevisionTask | null>(null);
  const [work, setWork] = useState<RevisionWork | null>(null);
  const [staged, setStaged] = useState<StagedView | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [instruction, setInstruction] = useState("");
  const [budget, setBudget] = useState("12");
  const [additional, setAdditional] = useState("1");
  const [selected, setSelected] = useState<string[]>([]);
  const [touched, setTouched] = useState(false);
  const [startingNew, setStartingNew] = useState(false);

  const refresh = useCallback(async () => {
    const current = await fetchCurrentTask(projectId);
    setTask(current);
    if (current === null) {
      setWork(null);
      setStaged(null);
      return;
    }
    const [nextWork, nextStaged] = await Promise.all([
      fetchRevisionWork(projectId),
      fetchStaged(projectId, current.task_id),
    ]);
    setWork(nextWork);
    setStaged(nextStaged);
  }, [projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    void refresh()
      .catch((exc: unknown) => {
        if (!cancelled) setError(message(exc));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [refresh]);

  const chapters = work?.context?.chapters ?? [];
  const statusOf = useMemo(() => {
    const rows = new Map<string, string>();
    for (const row of work?.coverage ?? []) rows.set(row.chapter_id, row.status);
    return rows;
  }, [work]);
  const openChapters = useMemo(
    () => chapters.filter((c) => !SETTLED.has(statusOf.get(c.chapter_id) ?? "unread")),
    [chapters, statusOf],
  );

  useEffect(() => {
    if (touched) return;
    setSelected(openChapters.map((c) => c.chapter_id));
  }, [openChapters, touched]);

  const run = async (action: () => Promise<void>) => {
    setBusy(true);
    setError("");
    setNote("");
    try {
      await action();
    } catch (exc) {
      setError(message(exc));
    } finally {
      setBusy(false);
    }
  };

  const start = () =>
    run(async () => {
      const created = await startTask(projectId, {
        request_id: `rq_ui_${Date.now().toString(36)}`,
        call_budget: Math.max(1, Number(budget) || 12),
      });
      setNote(`已开始：预算 ${created.budget.call_limit} 次调用。执行一轮后才会调用模型。`);
      setStartingNew(false);
      await refresh();
    });

  const execute = () =>
    run(async () => {
      if (!task) return;
      const result = await runRound(projectId, task.task_id, {
        instruction: instruction.trim(),
        chapter_ids: selected,
        request_id: `rn_ui_${Date.now().toString(36)}`,
      });
      if (result.status === "stopped" && result.stopped) {
        const title =
          chapters.find((c) => c.chapter_id === result.stopped?.chapter_id)?.title ??
          result.stopped.chapter_id;
        setNote(
          result.stopped.reason === "budget_exhausted"
            ? `预算用完，停在「${title}」之前；已完成 ${result.chapters.length} 章。继续追加次数后可接着跑。`
            : `执行停下（${result.stopped.reason}），已完成 ${result.chapters.length} 章。`,
        );
      } else if (result.status === "noop") {
        setNote("没有待处理的章：未下结论的章节为空。");
      } else {
        setNote(`本轮完成 ${result.chapters.length} 章，已暂存；发布后进入正式稿。`);
      }
      setTouched(false);
      await refresh();
    });

  const resume = () =>
    run(async () => {
      if (!task) return;
      await continueTask(projectId, task.task_id, Math.max(1, Number(additional) || 1));
      setNote("已追加预算，任务回到运行中；可以再执行一轮。");
      await refresh();
    });

  const halt = () =>
    run(async () => {
      if (!task) return;
      await stopTask(projectId, task.task_id);
      setNote("已停止：正式稿不变，已完成的覆盖与暂存保留；可继续或新建一轮。");
      await refresh();
    });

  const publish = () =>
    run(async () => {
      if (!task) return;
      const result = await publishTask(
        projectId,
        task.task_id,
        `op_ui_${Date.now().toString(36)}`,
      );
      setNote(`已发布 ${result.changed_chapters.length} 章，进入同一个版本。`);
      onPublished();
      await refresh();
    });

  const canRun =
    task !== null &&
    (task.state === "queued" || task.state === "running") &&
    !busy &&
    instruction.trim() !== "" &&
    selected.length > 0;
  const canPublish = task?.state === "running" && (staged?.chapter_count ?? 0) > 0;
  const blockers = task?.publication?.blockers ?? [];

  return (
    <section className="panel flex min-h-0 flex-1 flex-col" aria-label="整体调整">
      <header className="panel-head flex flex-wrap items-center justify-between gap-2 px-4 py-3">
        <p className="min-w-0 text-xs leading-6 text-sub">
          逐章读取并改写；改动先暂存，发布后进入正式稿。
        </p>
        <div className="flex items-center gap-2">
          {task && <Pill tone={tone(task.state)}>{STATE_LABEL[task.state] ?? task.state}</Pill>}
          <button type="button" className="btn btn-gray px-3 py-1.5 text-xs" onClick={onBack}>
            返回正文
          </button>
        </div>
      </header>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 lg:p-5">
        <ErrorText>{error}</ErrorText>
        {note && (
          <p role="status" className="text-xs leading-6 text-sub">
            {note}
          </p>
        )}

        {loading ? (
          <p className="text-xs text-sub">正在读取任务…</p>
        ) : task === null || startingNew ? (
          <section className="space-y-3" aria-label="开始整体调整">
            <p className="text-xs leading-6 text-sub">
              一次整体调整是一份预算明确的授权：开任务不调用模型，执行一轮时每章消耗一次调用。
            </p>
            <label className="field max-w-xl">
              <span className="text-xs text-sub">这次要改什么</span>
              <textarea
                className="well-input min-h-[96px] resize-y p-3 text-sm leading-6"
                aria-label="整体调整要求"
                placeholder="例如：把前三章的叙述压得更克制，保留所有事实与时间线"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
              />
            </label>
            <label className="field w-40">
              <span className="text-xs text-sub">调用预算（默认 12）</span>
              <input
                className="well-input px-3 py-2 text-sm"
                aria-label="调用预算"
                type="number"
                min={1}
                max={12}
                value={budget}
                onChange={(e) => setBudget(e.target.value)}
              />
            </label>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn px-4 py-2 text-xs"
                disabled={busy || instruction.trim() === ""}
                onClick={() => void start()}
              >
                开始整体调整
              </button>
              {startingNew && (
                <button
                  type="button"
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={busy}
                  onClick={() => setStartingNew(false)}
                >
                  取消
                </button>
              )}
            </div>
          </section>
        ) : (
          <>
            <section className="grid gap-3 sm:grid-cols-3" aria-label="任务状态">
              <div className="asset-row p-3">
                <p className="text-[11px] text-sub">预算</p>
                <p className="text-sm">
                  {task.budget.calls_used} / {task.budget.call_limit} 次
                </p>
              </div>
              <div className="asset-row p-3">
                <p className="text-[11px] text-sub">覆盖</p>
                <p className="text-sm">
                  {task.coverage?.covered_count ?? 0} / {task.coverage?.total_count ?? 0} 章
                </p>
              </div>
              <div className="asset-row p-3">
                <p className="text-[11px] text-sub">暂存</p>
                <p className="text-sm">{staged?.chapter_count ?? 0} 章待发布</p>
              </div>
            </section>

            {task.state === "waiting" && task.waiting_reason === "budget_exhausted" && (
              <Notice>
                <strong>预算用完，任务已暂停。</strong>
                <br />
                已完成的章、覆盖结论和暂存内容都保留；追加次数后点「继续」即可接着跑。
              </Notice>
            )}
            {task.state === "waiting" && task.waiting_reason !== "budget_exhausted" && (
              <Notice>
                <strong>任务在等待作者。</strong>
                <br />
                {task.waiting_detail || task.waiting_reason}
              </Notice>
            )}

            {task.state !== "completed" && (
            <label className="field">
              <span className="text-xs text-sub">
                本轮要求（执行一轮时交给模型；不会自动保存到本书约定）
              </span>
              <textarea
                className="well-input min-h-[72px] resize-y p-3 text-sm leading-6"
                aria-label="本轮要求"
                placeholder="写清这一轮要改成什么样；每章会带上该章现有正文"
                value={instruction}
                onChange={(e) => setInstruction(e.target.value)}
              />
            </label>
            )}

            <div className="flex flex-wrap items-end gap-2">
              {(task.state === "queued" || task.state === "running") && (
                <button
                  type="button"
                  className={`${openChapters.length > 0 ? "btn" : "btn btn-gray"} px-4 py-2 text-xs`}
                  disabled={!canRun}
                  onClick={() => void execute()}
                >
                  {busy
                    ? "正在执行…"
                    : `执行一轮${selected.length > 0 ? `（${selected.length} 章）` : ""}`}
                </button>
              )}
              {CAN_CONTINUE.has(task.state) && (
                <>
                  <label className="flex items-center gap-2 text-xs text-sub">
                    追加
                    <input
                      className="well-input w-16 px-2 py-1.5 text-sm"
                      aria-label="追加调用次数"
                      type="number"
                      min={1}
                      value={additional}
                      onChange={(e) => setAdditional(e.target.value)}
                    />
                    次
                  </label>
                  <button
                    type="button"
                    className="btn px-4 py-2 text-xs"
                    disabled={busy}
                    onClick={() => void resume()}
                  >
                    继续
                  </button>
                </>
              )}
              {(task.state === "running" || task.state === "queued") && (
                <button
                  type="button"
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={busy}
                  onClick={() => void halt()}
                >
                  停止
                </button>
              )}
              {canPublish && (
                <button
                  type="button"
                  className={`${
                    openChapters.length === 0 ? "btn" : "btn btn-gray"
                  } px-4 py-2 text-xs`}
                  disabled={busy || !(task.publication?.ready ?? true)}
                  onClick={() => void publish()}
                >
                  发布（{staged?.chapter_count ?? 0} 章）
                </button>
              )}
              {task.state === "completed" && (
                <button
                  type="button"
                  className="btn btn-gray px-3 py-1.5 text-xs"
                  disabled={busy}
                  onClick={() => {
                    setInstruction("");
                    setStartingNew(true);
                  }}
                >
                  新建一轮
                </button>
              )}
            </div>

            {blockers.length > 0 && canPublish && (
              <ul className="space-y-1 text-[11px] leading-5 text-sub" aria-label="发布前检查">
                {blockers.map((b) => (
                  <li key={b.code}>
                    {b.why}（{b.fix}）
                  </li>
                ))}
              </ul>
            )}

            <section aria-label="章节覆盖">
              <h2 className="mb-2 text-xs text-sub">
                章节（勾选本轮范围；已下结论的章默认不再重复消耗预算）
              </h2>
              <ul className="panel divide-y divide-line">
                {chapters.map((chapter) => {
                  const status = statusOf.get(chapter.chapter_id) ?? "unread";
                  const settled = SETTLED.has(status);
                  return (
                    <li key={chapter.chapter_id} className="asset-row flex items-center gap-3 p-3">
                      <input
                        type="checkbox"
                        aria-label={`选择 ${chapter.title}`}
                        disabled={settled || busy}
                        checked={selected.includes(chapter.chapter_id)}
                        onChange={(e) =>
                          setSelected((prev) =>
                            e.target.checked
                              ? [...prev, chapter.chapter_id]
                              : prev.filter((id) => id !== chapter.chapter_id),
                          )
                        }
                      />
                      <span className="min-w-0 flex-1 truncate text-sm">{chapter.title}</span>
                      <span className="shrink-0 text-[11px] text-sub">
                        {COVERAGE_LABEL[status] ?? status}
                      </span>
                    </li>
                  );
                })}
              </ul>
            </section>
          </>
        )}
      </div>
    </section>
  );
}

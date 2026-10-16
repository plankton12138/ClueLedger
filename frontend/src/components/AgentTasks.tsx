import { useState } from "react";
import {
  acceptAgentJob,
  cancelAgentJob,
  fetchAgentJobEvents,
  runAgentJob,
  submitAgentJob,
  type WorkerRole,
} from "../api/v2";
import { useV2Store, message, isDirty } from "../state/v2";
import AgentOutputView from "./AgentOutputView";
import {
  ErrorText,
  Panel,
  Empty,
  Pill,
  titleOf,
  roleLabels,
  jobLabels,
} from "./Workbench";
const eventLabels: Record<string, string> = {
  "job.queued": "任务已排队",
  "job.running": "任务开始执行",
  "agent.turn_start": "工作者开始推理",
  "agent.schema_correction": "正在纠正候选格式",
  "agent.tool_execution_start": "正在查询依据",
  "agent.tool_execution_end": "查询完成",
  "agent.turn_end": "本轮推理结束",
  "agent.output.proposed": "候选已返回",
  "agent.tool.denied": "已阻止范围外操作",
  "job.waiting": "等待作者审阅",
  "job.completed": "任务完成",
  "job.cancelled": "任务已取消",
  "job.retryable_failed": "环境故障，可重试",
  "job.terminal_failed": "执行失败，需要处理",
  "job.analysis_recorded": "分析结果已保存",
};

export default function AgentTasks({
  role = "planner",
  compact = false,
}: {
  role?: WorkerRole;
  compact?: boolean;
}) {
  const pid = useV2Store((s) => s.currentId),
    jobs = useV2Store((s) => s.jobs),
    story = useV2Store((s) => s.storyArtifacts);
  const [instruction, setInstruction] = useState(""),
    [selectedRole, setRole] = useState<WorkerRole>(role),
    [beat, setBeat] = useState("");
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [events, setEvents] = useState<Record<string, unknown>[]>([]);
  async function perform(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await fn();
    } catch (e) {
      setError(message(e));
    } finally {
      setBusy(false);
    }
  }
  const names = Object.fromEntries(
    story.map((a) => [a.artifact_id, titleOf(a)]),
  );
  const visible = jobs.filter(
    (j) =>
      j.job_type !== "manuscript.analysis" ||
      ["retryable_failed", "terminal_failed"].includes(j.state ?? ""),
  );
  return (
    <Panel
      title="协作主编"
      meta="候选先审阅，再进入正式稿"
      className={compact ? "" : "h-full overflow-auto"}
    >
      <form
        className="space-y-3 p-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (!pid) return;
          void perform(async () => {
            await submitAgentJob(pid, selectedRole, {
              instruction,
              targetIds: selectedRole === "writer" ? [beat] : [],
              chapterId:
                selectedRole === "writer"
                  ? `${pid}:manuscript-${crypto.randomUUID()}`
                  : "",
            });
            setInstruction("");
            await Promise.all([
              useV2Store.getState().refreshJobs(),
              useV2Store.getState().refreshManuscript(),
              useV2Store.getState().refreshStory(),
            ]);
          });
        }}
      >
        <label className="field">
          协作任务
          <select
            aria-label="协作任务"
            className="well-input"
            value={selectedRole}
            onChange={(e) => setRole(e.target.value as WorkerRole)}
          >
            <option value="planner">规划故事与节拍</option>
            <option value="writer">生成正文候选</option>
            <option value="auditor">检查故事一致性</option>
            <option value="coordinator">咨询协作主编</option>
          </select>
        </label>
        {selectedRole === "writer" && (
          <label className="field">
            已接受节拍
            <select
              aria-label="已接受节拍"
              className="well-input"
              value={beat}
              onChange={(e) => setBeat(e.target.value)}
              required
            >
              <option value="">选择本章要写的节拍</option>
              {story
                .filter((a) => a.kind === "narrative.beat")
                .map((a) => (
                  <option key={a.artifact_id} value={a.artifact_id}>
                    {titleOf(a)}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label className="field">
          创作要求
          <textarea
            className="well-input min-h-24 resize-y"
            aria-label="创作要求"
            value={instruction}
            onChange={(e) => setInstruction(e.target.value)}
            placeholder="描述故事方向、章节目的，或需要检查的问题…"
            required
          />
        </label>
        <button
          className="btn w-full py-2 text-xs"
          disabled={
            busy ||
            !pid ||
            !instruction.trim() ||
            (selectedRole === "writer" && !beat)
          }
        >
          {busy ? "处理中…" : "提交协作任务"}
        </button>
        <ErrorText>{error}</ErrorText>
      </form>
      <div className={compact ? "max-h-[48vh] overflow-auto" : ""}>
        {visible.length === 0 ? (
          <Empty>
            从一个构想开始。先让策划提出故事与节拍，接受后再生成正文。
          </Empty>
        ) : (
          [...visible].reverse().map((job) => {
            const id = job.job_id ?? job.id ?? "",
              state = job.state ?? "";
            return (
              <article
                key={id}
                aria-label={`${roleLabels[job.job_type ?? ""] ?? "创作"}任务`}
                className="border-t border-line p-4 text-xs"
              >
                <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                  <b>{roleLabels[job.job_type ?? ""] ?? "创作"}任务</b>
                  <Pill
                    tone={
                      state === "completed"
                        ? "green"
                        : state.endsWith("failed")
                          ? "red"
                          : "blue"
                    }
                  >
                    {jobLabels[state] ?? state}
                  </Pill>
                </div>
                {job.acceptance?.commit_id && (
                  <p className="mb-2 break-all text-green">
                    正式提交：{job.acceptance.commit_id}
                  </p>
                )}
                {job.output && (
                  <div className="mb-3 max-h-64 overflow-auto leading-6">
                    <AgentOutputView output={job.output} names={names} />
                  </div>
                )}
                {job.error != null && (
                  <p className="mb-2 break-words text-red">
                    {typeof job.error === "string"
                      ? job.error
                      : JSON.stringify(job.error)}
                  </p>
                )}
                <div className="flex flex-wrap gap-2">
                  {state === "waiting" &&
                    ["planner", "writer"].includes(job.job_type ?? "") &&
                    ["story_proposal", "manuscript_patch"].includes(
                      String(job.output?.kind),
                    ) && (
                      <button
                        className="btn px-3 py-1.5"
                        disabled={
                          busy || isDirty(useV2Store.getState().chapter)
                        }
                        onClick={() =>
                          void perform(async () => {
                            const s = useV2Store.getState();
                            const head = s.head ?? s.storyCommit;
                            if (!head)
                              throw new Error(
                                "项目版本尚未读取，请刷新项目后再接受候选。",
                              );
                            await acceptAgentJob(pid!, id, head);
                            await Promise.all([
                              s.refreshJobs(),
                              s.refreshStory(),
                              s.refreshManuscript(),
                            ]);
                          })
                        }
                      >
                        接受候选并提交
                      </button>
                    )}
                  {["queued", "retryable_failed"].includes(state) && (
                    <button
                      className="btn btn-gray px-2 py-1"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await runAgentJob(pid!, id);
                          await useV2Store.getState().refreshJobs();
                        })
                      }
                    >
                      运行或重试
                    </button>
                  )}
                  {!["completed", "cancelled", "terminal_failed"].includes(
                    state,
                  ) && (
                    <button
                      className="btn btn-gray px-2 py-1"
                      disabled={busy}
                      onClick={() =>
                        void perform(async () => {
                          await cancelAgentJob(pid!, id);
                          await useV2Store.getState().refreshJobs();
                        })
                      }
                    >
                      取消任务
                    </button>
                  )}
                  <button
                    className="text-blue underline"
                    disabled={busy}
                    onClick={() =>
                      void perform(async () =>
                        setEvents(await fetchAgentJobEvents(pid!, id)),
                      )
                    }
                  >
                    查看任务过程
                  </button>
                </div>
              </article>
            );
          })
        )}
      </div>
      {events.length > 0 && (
        <details open className="border-t border-line p-4 text-xs">
          <summary>任务过程</summary>
          <ol className="mt-3 space-y-2 text-sub">
            {events.map((e, i) => (
              <li key={String(e.sequence ?? i)}>
                {String(e.sequence ?? i + 1)} ·{" "}
                {eventLabels[String(e.type)] ?? "任务进度已更新"}
              </li>
            ))}
          </ol>
        </details>
      )}
    </Panel>
  );
}

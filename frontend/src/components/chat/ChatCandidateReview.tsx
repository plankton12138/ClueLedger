import { useEffect, useState } from "react";
import { fetchAgentJob, type AgentJob } from "../../api/v2";
import { isDirty, message, useV2Store } from "../../state/v2";
import { useChatStore } from "../../state/chat";
import type { CandidatePayload, ChatMessage, ResultPayload } from "../../api/chat";
import AgentOutputView from "../AgentOutputView";
import { ErrorText, Panel } from "../Workbench";

export default function ChatCandidateReview({ candidate, projectId, onClose, onAccepted }: {
  candidate: ChatMessage;
  projectId: string;
  onClose(): void;
  /** 接受成功后回调（写作台据此回到正文/讨论），缺省只刷新不导航。 */
  onAccepted?(result: ResultPayload): void;
}) {
  const payload = candidate.payload as CandidatePayload;
  const [job, setJob] = useState<AgentJob | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(payload.accepted);
  const service = useChatStore((s) => s.service);
  const chapter = useV2Store((s) => s.chapter);
  const dirty = isDirty(chapter) || chapter.saving;
  useEffect(() => {
    let alive = true;
    setJob(null); setError(""); setAccepted(payload.accepted);
    if (service?.kind === "api") {
      void fetchAgentJob(projectId, payload.job_ref).then((value) => {
        if (alive) { setJob(value); setAccepted(Boolean(value.acceptance?.commit_id)); }
      }).catch((e) => { if (alive) setError(message(e)); });
    }
    return () => { alive = false; };
  }, [projectId, payload.job_ref, payload.accepted, service]);
  return <Panel title="候选审阅" meta="接受前不会修改正式正文">
    <div className="space-y-4 p-5" aria-label="候选完整内容">
      <p className="text-xs">任务：{payload.job_ref}</p>
      {job?.output ? <AgentOutputView output={job.output} />
        : service?.kind === "simulator" ? <p>{payload.summary}</p>
        : !error && <p role="status">正在读取候选正文…</p>}
      {dirty && <p role="alert">当前正文有未保存稿，请先保存。预览会保留当前草稿；保存后重新准备候选以匹配最新版本。</p>}
      <ErrorText>{error}</ErrorText>
      <div className="flex gap-3">
        <button className="btn px-4 py-2" disabled={busy || accepted || dirty ||
          (service?.kind === "api" && (job?.state !== "waiting" ||
            !["story_proposal", "manuscript_patch"].includes(String(job.output?.kind))))}
          onClick={() => { void (async () => {
            if (!service || useV2Store.getState().currentId !== projectId) return;
            setBusy(true); setError("");
            try {
              const state = useV2Store.getState();
              const result = await service.acceptCandidate(projectId, candidate.conversation_id,
                candidate.message_id, state.head ?? state.storyCommit);
              setAccepted(true);
              await Promise.all([state.refreshJobs(), state.refreshManuscript(), state.refreshStory()]);
              onAccepted?.(result);
            } catch (e) { setError(message(e)); } finally { setBusy(false); }
          })(); }}>
          {accepted ? "已接受" : "接受候选并提交"}
        </button>
        <button className="btn btn-gray px-4 py-2" onClick={onClose}>返回讨论</button>
      </div>
    </div>
  </Panel>;
}

/** 更新命令与页面浏览分开；同键重试保存原提交体。 */
import { create } from "zustand";
import { overviewRequest, type Manifest, type UpdateProgress, type UpdateRequest, type UpdateResponse } from "../api/storyOverview";
import { stopTurn } from "../api/workspace";
import { requestKey } from "../api/moduleLibrary";
import { errorMessage } from "../api/errors";

type UpdateState = { pending?: UpdateRequest; submitting?: boolean; turnId?: string; progress?: UpdateProgress; error?: string; notice?: string };
interface OverviewTasks {
  workspaces: Record<string, UpdateState>;
  start(id: string, manifest: Manifest): Promise<void>;
  retry(id: string): Promise<void>;
  recover(id: string, turnId: string): void;
  poll(id: string): Promise<void>;
  stop(id: string): Promise<void>;
  clear(id: string): void;
}
function restoreRequests(): Record<string, UpdateState> {
  try {
    const saved = JSON.parse(sessionStorage.getItem("clueledger.overview.commands") ?? "{}");
    if (!saved || typeof saved !== "object") return {};
    const restored: Record<string, UpdateState> = {};
    for (const [id, value] of Object.entries(saved)) {
      if (!/^[A-Za-z0-9_-]{1,64}$/.test(id) || !value || typeof value !== "object" || !("pending" in value)) continue;
      const body = value.pending as Partial<UpdateRequest> | undefined;
      if (body && typeof body.request_key === "string" && typeof body.observed_generation === "string" && Number.isSafeInteger(body.as_of_chapter) && Number(body.as_of_chapter) >= 0) restored[id] = { pending: body as UpdateRequest };
    }
    return restored;
  } catch { return {}; }
}
export const useOverviewTasks = create<OverviewTasks>((set, get) => {
  const polling = new Set<string>();
  const patch = (id: string, value: Partial<UpdateState>) => {
    set(s => ({ workspaces: { ...s.workspaces, [id]: { ...s.workspaces[id], ...value } } }));
    try { sessionStorage.setItem("clueledger.overview.commands", JSON.stringify(Object.fromEntries(Object.entries(get().workspaces).filter(([, task]) => task.pending).map(([key, task]) => [key, { pending: task.pending }])))); } catch { /* 无存储权限时仍保留当前页面的请求体。 */ }
  };
  return {
    workspaces: restoreRequests(),
    async start(id, manifest) {
      const current = get().workspaces[id];
      if (current?.submitting || current?.pending || (current?.turnId && (!current.progress || ["running", "accepted", "stop_requested"].includes(current.progress.run_status)))) return;
      patch(id, { pending: { request_key: requestKey("overview"), observed_generation: manifest.generation, as_of_chapter: manifest.as_of_chapter }, progress: undefined, error: undefined, notice: undefined });
      await get().retry(id);
    },
    async retry(id) {
      const body = get().workspaces[id]?.pending;
      if (!body || get().workspaces[id]?.submitting) return;
      patch(id, { submitting: true, error: undefined });
      try {
        const response = await overviewRequest<UpdateResponse>(id, "/updates", {}, { method: "POST", body: JSON.stringify(body) });
        patch(id, { pending: undefined, submitting: false, turnId: response.state === "accepted" ? response.turn_id : undefined, notice: response.state === "noop" ? "总览已是最新" : undefined });
        if (response.state === "accepted") await get().poll(id);
      } catch (error) {
        // 明确的 HTTP 拒绝可以重新发起；网络结果不明则只允许原样重试。
        const rejected = error instanceof Error && "status" in error && Number(error.status) >= 400 && Number(error.status) < 500;
        patch(id, { submitting: false, pending: rejected ? undefined : body, error: errorMessage(error, "故事总览") });
      }
    },
    recover(id, turnId) { if (get().workspaces[id]?.turnId !== turnId) patch(id, { turnId, progress: undefined }); },
    async poll(id) {
      const turnId = get().workspaces[id]?.turnId;
      if (!turnId || polling.has(id)) return;
      polling.add(id);
      try {
        const progress = await overviewRequest<UpdateProgress>(id, `/updates/${turnId}`);
        if (get().workspaces[id]?.turnId === turnId) patch(id, { progress, error: undefined });
      } catch (error) { if (get().workspaces[id]?.turnId === turnId) patch(id, { error: errorMessage(error, "故事总览") }); }
      finally { polling.delete(id); }
    },
    async stop(id) { const turnId = get().workspaces[id]?.turnId; if (turnId) { try { await stopTurn(id, turnId); await get().poll(id); } catch (error) { patch(id, { error: errorMessage(error, "故事总览") }); } } },
    clear(id) { patch(id, { error: undefined }); },
  };
});

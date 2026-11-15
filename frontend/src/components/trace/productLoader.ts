import { activityError, activityRequest } from "../../api/activity";

import type { TraceDetailLoader } from "./types";

/** 产品页：经活动记录接口取事件与附件。 */
export function productTraceLoader(workspace: string): TraceDetailLoader {
  return {
    async loadEvent(eventId) {
      try {
        return await activityRequest<Record<string, unknown>>(
          workspace,
          `/${encodeURIComponent(eventId)}`,
        );
      } catch (reason) {
        throw new Error(activityError(reason));
      }
    },
    async loadAttachment(key) {
      try {
        return await activityRequest(workspace, `/attachments/${encodeURIComponent(key)}`);
      } catch (reason) {
        throw new Error(activityError(reason));
      }
    },
    async loadCaptureEnabled() {
      try {
        const settings = await activityRequest<{ capture_inputs: boolean }>(
          workspace,
          "/settings",
        );
        return settings.capture_inputs;
      } catch {
        return null;
      }
    },
  };
}

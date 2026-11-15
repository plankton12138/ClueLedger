import type { TraceDetailLoader } from "../components/trace";

import { fetchCapture, fetchEvent, fetchInput } from "./api";

/** 工作台：从证据包取事件与模型输入附件。 */
export function evidenceTraceLoader(runId: string, caseId: string, attempt: number): TraceDetailLoader {
  return {
    async loadEvent(eventId) {
      return fetchEvent(runId, caseId, attempt, eventId);
    },
    async loadAttachment(key) {
      const match = key.match(/(?:^|\/)([^/]+?)(?:\.json)?$/);
      const operationId = match?.[1] || key;
      return fetchInput(runId, caseId, attempt, operationId);
    },
    async loadCaptureEnabled() {
      try {
        const result = await fetchCapture(runId, caseId, attempt);
        return result.capture_inputs;
      } catch {
        return null;
      }
    },
  };
}

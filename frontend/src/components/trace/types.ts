import type { TraceEvent, TurnTrace } from "../../api/activity";

export type { TraceEvent, TurnTrace };

export type TraceDetailLoader = {
  loadEvent(eventId: string): Promise<Record<string, unknown>>;
  loadAttachment(key: string): Promise<unknown>;
  loadCaptureEnabled(): Promise<boolean | null>;
};

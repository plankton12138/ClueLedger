import type { TraceEvent, TurnTrace } from "../../api/activity";

export type { TraceEvent, TurnTrace };

/** 检查项钉在时间线某一步上的标记。 */
export type TraceMark = {
  span_id: string;
  verdict: "pass" | "fail" | "skipped" | "pending_review";
  check_id?: string;
};

/** 预期出现却未出现的步骤（虚线行）。 */
export type TraceMissingRow = {
  id: string;
  name: string;
  expected: string;
};

/** 详情区取数：产品调接口，工作台读证据包。 */
export type TraceDetailLoader = {
  loadEvent(eventId: string): Promise<Record<string, unknown>>;
  loadAttachment(key: string): Promise<unknown>;
  loadCaptureEnabled(): Promise<boolean | null>;
};

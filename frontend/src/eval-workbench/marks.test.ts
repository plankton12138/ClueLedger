import { describe, expect, it } from "vitest";

import { marksFromChecks, missingFromChecks } from "./marks";

describe("marksFromChecks", () => {
  it("extracts span marks and missing rows", () => {
    const checks = [
      {
        id: "committed_via_candidate",
        level: "must",
        verdict: "fail",
        reason: "无回执",
        evidence: [
          { type: "missing", expected: "候选提交回执", turn_id: "t1" },
          { type: "span", turn_id: "t1", span_id: "tool:1" },
        ],
      },
    ];
    expect(marksFromChecks(checks)).toEqual([
      { span_id: "tool:1", verdict: "fail", check_id: "committed_via_candidate" },
    ]);
    expect(missingFromChecks(checks)).toEqual([
      {
        id: "missing:committed_via_candidate:候选提交回执",
        name: "候选提交回执",
        expected: "候选提交回执",
      },
    ]);
  });
});

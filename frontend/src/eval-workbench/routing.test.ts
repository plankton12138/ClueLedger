import { describe, expect, it } from "vitest";

import { parseHash, routeHash } from "./routing";

describe("parseHash", () => {
  it("uses configuration and records routes", () => {
    expect(parseHash("#/assets")).toEqual({ page: "assets", tab: "suites" });
    expect(parseHash("#/assets?tab=envs")).toEqual({ page: "assets", tab: "envs" });
    expect(parseHash("#/assets?tab=evaluators")).toEqual({ page: "assets", tab: "evaluators" });
    expect(parseHash("#/run?suite=s1")).toEqual({
      page: "run",
      presetCaseIds: [],
      suiteId: "s1",
    });
    expect(parseHash("#/qa")).toEqual({ page: "reports", runId: null, filter: "review" });
    expect(parseHash("#/reports/smoke-3?case=c1&attempt=2")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: "c1",
      preferAttempt: 2,
    });
    expect(parseHash("#/reports/smoke-3?case=c1&attempt=2&focus=review")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: "c1",
      preferAttempt: 2,
      focus: "review",
    });
    expect(parseHash("#/reports/smoke-3?focus=bogus")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: null,
      preferAttempt: null,
    });
  });

  it("preserves a focused check and the filter to return to", () => {
    const target = {
      page: "reports", runId: "smoke-3", filter: "issues",
      preferCaseId: "c1", preferAttempt: 2, preferCheckId: "quality", focus: "checks",
    } as const;
    expect(parseHash(routeHash(target))).toEqual(target);
  });

  it("redirects legacy six-entry and older paths", () => {
    expect(parseHash("#/tasks")).toEqual({ page: "assets", tab: "suites" });
    expect(parseHash("#/envs")).toEqual({ page: "assets", tab: "envs" });
    expect(parseHash("#/evaluators")).toEqual({ page: "assets", tab: "evaluators" });
    expect(parseHash("#/assets/suites")).toEqual({ page: "assets", tab: "suites" });
    expect(parseHash("#/assets/envs")).toEqual({ page: "assets", tab: "envs" });
    expect(parseHash("#/cases")).toEqual({ page: "assets", tab: "suites" });
    expect(parseHash("#/pipeline")).toEqual({
      page: "run",
      presetCaseIds: [],
      suiteId: null,
    });
    expect(parseHash("#/launch")).toEqual({
      page: "run",
      presetCaseIds: [],
      suiteId: null,
    });
    expect(parseHash("#/runs/smoke-3")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: null,
      preferAttempt: null,
    });
  });
});

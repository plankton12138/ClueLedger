import { describe, expect, it } from "vitest";

import { parseHash } from "./routing";

describe("parseHash", () => {
  it("uses four-entry routes", () => {
    expect(parseHash("#/assets")).toEqual({ page: "assets", tab: "suites" });
    expect(parseHash("#/assets?tab=envs")).toEqual({ page: "assets", tab: "envs" });
    expect(parseHash("#/assets?tab=evaluators")).toEqual({ page: "assets", tab: "evaluators" });
    expect(parseHash("#/run?suite=s1")).toEqual({
      page: "run",
      presetCaseIds: [],
      suiteId: "s1",
    });
    expect(parseHash("#/qa")).toEqual({ page: "qa" });
    expect(parseHash("#/reports/smoke-3?case=c1&attempt=2")).toEqual({
      page: "reports",
      runId: "smoke-3",
      preferCaseId: "c1",
      preferAttempt: 2,
    });
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

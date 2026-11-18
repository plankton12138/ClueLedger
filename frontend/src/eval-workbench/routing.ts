export type AssetTab = "suites" | "envs" | "evaluators";

export type Route =
  | { page: "assets"; tab: AssetTab }
  | { page: "run"; presetCaseIds: string[]; suiteId: string | null }
  | {
      page: "reports";
      runId: string | null;
      preferCaseId?: string | null;
      preferAttempt?: number | null;
    }
  | { page: "qa" };

function parseCases(raw: string): string[] {
  return raw
    .split(",")
    .map(item => item.trim())
    .filter(Boolean)
    .map(item => {
      try {
        return decodeURIComponent(item);
      } catch {
        return item;
      }
    });
}

function parseAttempt(raw: string | null): number | null {
  if (raw == null || raw === "") return null;
  const value = Number(raw);
  return Number.isFinite(value) ? value : null;
}

function runRoute(
  path: string,
  prefix: "reports" | "runs",
  params: URLSearchParams,
): Route {
  if (path === prefix) return { page: "reports", runId: null, preferCaseId: null };
  const id = decodeURIComponent(path.slice(`${prefix}/`.length).split("/")[0] || "");
  return {
    page: "reports",
    runId: id || null,
    preferCaseId: params.get("case"),
    preferAttempt: parseAttempt(params.get("attempt")),
  };
}

export function parseHash(hash?: string): Route {
  const raw = (hash ?? (typeof location !== "undefined" ? location.hash : "")).replace(/^#/, "");
  const [pathPart, queryPart = ""] = raw.split("?");
  const path = pathPart.replace(/^\//, "");
  const params = new URLSearchParams(queryPart);

  if (path === "qa" || path.startsWith("qa/")) {
    return { page: "qa" };
  }

  if (path === "assets" || path.startsWith("assets/")) {
    const tab = path === "assets" ? params.get("tab") || "suites" : path.slice("assets/".length).split("/")[0];
    if (tab === "envs") return { page: "assets", tab: "envs" };
    if (tab === "evaluators") return { page: "assets", tab: "evaluators" };
    return { page: "assets", tab: "suites" };
  }

  // legacy six-entry → assets tabs
  if (path === "tasks" || path.startsWith("tasks/") || path === "cases" || path.startsWith("cases/")) {
    return { page: "assets", tab: "suites" };
  }
  if (path === "envs" || path.startsWith("envs/")) {
    return { page: "assets", tab: "envs" };
  }
  if (path === "evaluators" || path.startsWith("evaluators/")) {
    return { page: "assets", tab: "evaluators" };
  }

  if (
    path === "run" ||
    path.startsWith("run/") ||
    path.startsWith("pipeline") ||
    path.startsWith("launch")
  ) {
    return {
      page: "run",
      presetCaseIds: parseCases(params.get("cases") || ""),
      suiteId: params.get("suite"),
    };
  }

  if (path.startsWith("reports/") || path === "reports") {
    return runRoute(path, "reports", params);
  }

  if (path.startsWith("runs/") || path === "runs" || path === "") {
    return runRoute(path === "" ? "runs" : path, "runs", params);
  }

  return { page: "reports", runId: null, preferCaseId: null };
}

export function writeHash(route: Route): void {
  let next = "#/reports";
  if (route.page === "assets") {
    const tab = route.tab === "suites" ? "" : `?tab=${route.tab}`;
    next = `#/assets${tab}`;
  } else if (route.page === "qa") {
    next = "#/qa";
  } else if (route.page === "run") {
    const params = new URLSearchParams();
    if (route.presetCaseIds.length) {
      params.set("cases", route.presetCaseIds.map(encodeURIComponent).join(","));
    }
    if (route.suiteId) params.set("suite", route.suiteId);
    const q = params.toString();
    next = `#/run${q ? `?${q}` : ""}`;
  } else if (route.runId) {
    const params = new URLSearchParams();
    if (route.preferCaseId) params.set("case", route.preferCaseId);
    if (route.preferAttempt != null) params.set("attempt", String(route.preferAttempt));
    const q = params.toString();
    next = `#/reports/${encodeURIComponent(route.runId)}${q ? `?${q}` : ""}`;
  } else {
    next = "#/reports";
  }
  if (typeof location === "undefined") return;
  if (location.hash === next) return;
  location.hash = next;
}

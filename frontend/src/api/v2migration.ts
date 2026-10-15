/**
 * V1 → V2 迁移 REST 客户端（T17.4/T17.5）。
 *
 * 对应 server/v2_migration.py 的三个端点。错误信封两套口径并存：
 * slug 面 404 走 {"detail"}，应用层违规走 V2 信封 {"error": {code, why, fix}}——
 * 本模块统一抽出可读文本（why + fix 优先），三要素原样透给用户。
 */

export interface MigrationStatus {
  slug: string;
  architecture_version: string;
  v2_project_id: string | null;
  migrated: boolean;
}

/** 迁移报告的逐文件归类（镜像 v1_workspace_importer.SourceItem）。 */
export interface MigrationItem {
  path: string;
  item_type: string;
  outcome: "mapped" | "legacy" | "ambiguous" | "ignored" | "error";
  target: string;
  detail: string;
}

export interface MigrationReportWire {
  workspace: string;
  run_id: string;
  schema_version: number;
  ok: boolean;
  items: MigrationItem[];
  counts: { files: number; story_entities: number; chapters: number; revisions: number; chars: number };
}

export interface MigrationApplyResult {
  slug: string;
  project_id: string;
  status: string;
  commit_id: string | null;
  new_head: string | null;
  replayed: boolean;
  validation_ok: boolean;
  report: MigrationReportWire;
}

/** 抽错误文案：V2 信封（why/fix）优先，回落 V1 detail，再回落状态码。 */
export async function migrationErrorText(res: Response): Promise<string> {
  const body: unknown = await res.json().catch(() => null);
  if (body !== null && typeof body === "object") {
    const error = (body as { error?: { why?: unknown; fix?: unknown } }).error;
    if (error && typeof error === "object") {
      const why = typeof error.why === "string" ? error.why : "";
      const fix = typeof error.fix === "string" ? error.fix : "";
      if (why !== "") return fix !== "" ? `${why}（怎样算对：${fix}）` : why;
    }
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string" && detail !== "") return detail;
  }
  return `HTTP ${res.status}`;
}

export async function fetchMigrationStatus(slug: string): Promise<MigrationStatus> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/v2-migration/status`);
  if (!res.ok) {
    throw new Error(`迁移状态读取失败：${await migrationErrorText(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { migrated?: unknown }).migrated !== "boolean"
  ) {
    throw new Error(`迁移状态响应结构不符：期望 { slug, architecture_version, v2_project_id, migrated }`);
  }
  return body as MigrationStatus;
}

export async function dryRunMigration(slug: string): Promise<MigrationReportWire> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/v2-migration/dry-run`, {
    method: "POST",
  });
  if (!res.ok) {
    throw new Error(`迁移预检失败：${await migrationErrorText(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { run_id?: unknown }).run_id !== "string" ||
    !Array.isArray((body as { items?: unknown }).items)
  ) {
    throw new Error(`迁移报告响应结构不符：期望 { run_id, items, counts, ... }`);
  }
  return body as MigrationReportWire;
}

export async function applyMigration(
  slug: string,
  confirmRunId: string,
): Promise<MigrationApplyResult> {
  const res = await fetch(`/api/${encodeURIComponent(slug)}/v2-migration/apply`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ confirm_run_id: confirmRunId }),
  });
  if (!res.ok) {
    throw new Error(`迁移落盘失败：${await migrationErrorText(res)}`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (
    body === null ||
    typeof body !== "object" ||
    typeof (body as { project_id?: unknown }).project_id !== "string"
  ) {
    throw new Error(`迁移落盘响应结构不符：期望 { project_id, status, ... }`);
  }
  return body as MigrationApplyResult;
}

/** 报告摘要（纯函数，供面板与测试）：阻断项与各类计数。 */
export function summarizeReport(report: MigrationReportWire): {
  errors: MigrationItem[];
  blockers: string[];
  mapped: number;
  ignored: number;
} {
  const errors = report.items.filter((item) => item.outcome === "error");
  return {
    errors,
    blockers: errors.map((item) => `${item.path}：${item.detail}`),
    mapped: report.items.filter((item) => item.outcome === "mapped").length,
    ignored: report.items.filter((item) => item.outcome === "ignored").length,
  };
}

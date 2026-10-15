/**
 * 迁移 API 客户端测试（T17.4）：报告摘要纯函数 + 错误信封抽取 + 三端点契约。
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  applyMigration,
  dryRunMigration,
  fetchMigrationStatus,
  migrationErrorText,
  summarizeReport,
  type MigrationReportWire,
} from "./v2migration";

afterEach(() => {
  vi.unstubAllGlobals();
});

const report = (over: Partial<MigrationReportWire> = {}): MigrationReportWire => ({
  workspace: "winter",
  run_id: "a".repeat(64),
  schema_version: 3,
  ok: true,
  items: [
    { path: "chapters/ch01.md", item_type: "chapter", outcome: "mapped", target: "art_1", detail: "→ 3 段" },
    { path: "sessions.json", item_type: "session_index", outcome: "ignored", target: "-", detail: "只读历史归档" },
    { path: "bible/clues.yaml", item_type: "clue", outcome: "error", target: "-", detail: "STORY_RED_HERRING_WITHOUT_CAUSE@…basis" },
  ],
  counts: { files: 3, story_entities: 4, chapters: 1, revisions: 0, chars: 120 },
  ...over,
});

describe("summarizeReport", () => {
  it("阻断项带路径与原因；mapped/ignored 计数准确", () => {
    const summary = summarizeReport(report());
    expect(summary.blockers).toEqual(["bible/clues.yaml：STORY_RED_HERRING_WITHOUT_CAUSE@…basis"]);
    expect(summary.mapped).toBe(1);
    expect(summary.ignored).toBe(1);
  });

  it("无错误时 blockers 为空", () => {
    const clean = report({
      items: [{ path: "story.yaml", item_type: "story", outcome: "ignored", target: "-", detail: "生命周期" }],
    });
    expect(summarizeReport(clean).blockers).toEqual([]);
  });
});

describe("migrationErrorText", () => {
  const response = (body: unknown, status = 400) =>
    new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

  it("V2 信封：why + fix 组合", async () => {
    const text = await migrationErrorText(
      response({ error: { code: "X", why: "工作区已迁移", fix: "直接使用已有项目" } }),
    );
    expect(text).toBe("工作区已迁移（怎样算对：直接使用已有项目）");
  });

  it("V1 detail 回落", async () => {
    expect(await migrationErrorText(response({ detail: "工作区不存在：nope" }))).toBe(
      "工作区不存在：nope",
    );
  });

  it("无 body 时回落状态码", async () => {
    expect(await migrationErrorText(new Response("", { status: 500 }))).toBe("HTTP 500");
  });
});

describe("端点契约", () => {
  it("fetchMigrationStatus：结构不符抛错，正常透传", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("{}", { status: 200 })));
    await expect(fetchMigrationStatus("w")).rejects.toThrow("结构不符");

    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          JSON.stringify({ slug: "w", architecture_version: "v2", v2_project_id: "prj_1", migrated: true }),
          { status: 200 },
        ),
      ),
    );
    const status = await fetchMigrationStatus("w");
    expect(status.migrated).toBe(true);
  });

  it("dryRunMigration：POST dry-run，报告透传", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(init?.method).toBe("POST");
      return new Response(JSON.stringify(report()), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);
    const wire = await dryRunMigration("w");
    expect(wire.run_id).toBe("a".repeat(64));
  });

  it("applyMigration：确认 run_id 进请求体；错误信封透出", async () => {
    const fetchMock = vi.fn(async (_url: string, init?: RequestInit) => {
      expect(JSON.parse(String(init?.body)).confirm_run_id).toBe("a".repeat(64));
      return new Response(
        JSON.stringify({ error: { why: "目标项目非空", fix: "新建空项目" } }),
        { status: 400 },
      );
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(applyMigration("w", "a".repeat(64))).rejects.toThrow("目标项目非空");
  });
});

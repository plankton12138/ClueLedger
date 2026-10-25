import { beforeEach, describe, expect, it, vi } from "vitest";
import { useOverviewTasks } from "./storyOverview";
import type { Manifest } from "../api/storyOverview";
import { WorkspaceApiError } from "../api/workspace";
import { pageHash, parseHash } from "./navigation";

const manifest = { generation: "generation-1", as_of_chapter: 3 } as Manifest;
function response(value: unknown) { return new Response(JSON.stringify(value), { status: 200 }); }
beforeEach(() => { useOverviewTasks.setState({ workspaces: {} }); vi.restoreAllMocks(); });

describe("overview commands", () => {
  it("网络结果未知时原样重试，清单更新不替换已经提交的范围", async () => {
    const bodies: string[] = [];
    const fetch = vi.spyOn(globalThis, "fetch").mockImplementation(async (_url, init) => {
      bodies.push(String(init?.body));
      if (bodies.length === 1) throw new TypeError("lost response");
      return response({ state: "noop", generation: "g2" });
    });
    await useOverviewTasks.getState().start("book-a", manifest);
    const pending = useOverviewTasks.getState().workspaces["book-a"].pending;
    expect(pending?.observed_generation).toBe("generation-1");
    await useOverviewTasks.getState().start("book-a", { ...manifest, generation: "generation-2" });
    expect(fetch).toHaveBeenCalledTimes(1);
    await useOverviewTasks.getState().retry("book-a");
    expect(bodies[1]).toBe(bodies[0]);
    expect(useOverviewTasks.getState().workspaces["book-a"].pending).toBeUndefined();
    expect(useOverviewTasks.getState().workspaces["book-a"].notice).toBe("总览已是最新");
  });
  it("延迟返回的任务状态不能覆盖新任务或另一作品", async () => {
    let resolve!: (value: Response) => void;
    vi.spyOn(globalThis, "fetch").mockImplementation(() => new Promise(done => { resolve = done; }));
    const store = useOverviewTasks.getState();
    store.recover("book-a", "turn-old");
    const polling = store.poll("book-a");
    store.recover("book-a", "turn-new");
    store.recover("book-b", "turn-b");
    resolve(response({ turn_id: "turn-old", run_status: "completed" }));
    await polling;
    expect(useOverviewTasks.getState().workspaces["book-a"].turnId).toBe("turn-new");
    expect(useOverviewTasks.getState().workspaces["book-a"].progress).toBeUndefined();
    expect(useOverviewTasks.getState().workspaces["book-b"].turnId).toBe("turn-b");
  });
  it("明确拒绝的命令允许刷新后重新发起", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({ error: { code: "OVERVIEW_SOURCE_CHANGED_CONFLICT", why: "来源改变", fix: "刷新" } }), { status: 409 }));
    await useOverviewTasks.getState().start("book-a", manifest);
    expect(useOverviewTasks.getState().workspaces["book-a"].pending).toBeUndefined();
    expect(useOverviewTasks.getState().workspaces["book-a"].error).toContain("OVERVIEW_SOURCE_CHANGED_CONFLICT");
    expect(new WorkspaceApiError("x", "why", "fix", 409).status).toBe(409);
  });
});

describe("overview links", () => {
  it("精确对象及截止章跨刷新恢复，旧人物详情仍可访问", () => {
    const page = { kind: "overview" as const, workspaceId: "book-a", chapter: 4, order: "story" as const, bindingId: "bind_1", view: "characters" as const, facetId: "facet_1" };
    expect(parseHash(pageHash(page))).toEqual(page);
    expect(parseHash("#/workspaces/book-a/materials/characters?entry=old&chapter=4")).toMatchObject({ kind: "materials", entryId: "old", chapter: 4 });
    for (const query of ["binding=../x", "facet=%ZZ", "chapter=1.5", "chapter=9007199254740992", "view=unknown"]) expect(parseHash(`#/workspaces/book-a/overview?${query}`)).toBeNull();
  });
});

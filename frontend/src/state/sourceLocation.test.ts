import { beforeEach, describe, expect, it, vi } from "vitest";
import type { SourceLocation } from "../api/clues";

vi.mock("../api/clues", () => ({ fetchSourceLocation: vi.fn() }));
vi.mock("./workspace", () => ({ useWorkspaceStore: { getState: vi.fn() } }));
import { fetchSourceLocation } from "../api/clues";
import { useWorkspaceStore } from "./workspace";
import { sourceTaskChapter, useSourceLocationStore } from "./sourceLocation";
import { usePagePositions } from "./pagePositions";

const sample: SourceLocation = {
  evidence_id: "ev_first", clue_id: "clue_first", source: { kind: "file", path: "正文/0001.md", digest: "old" },
  freshness: "valid", quote: "引文", chapter: 1, as_of_chapter: 6, offset_unit: "unicode_codepoint",
  start: 3, end: 5, readable: true, current_digest: "old", excerpt: "引文", excerpt_start: 3,
};
function pending<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; }
const api = vi.mocked(fetchSourceLocation);
const openPath = vi.fn();
let workspace: { currentId: string; workspaceEpoch: number; fileEpoch: number; selectedPath: string; file: { path: string; digest: string }; error?: string; openPath: typeof openPath };

beforeEach(() => {
  vi.clearAllMocks();
  useSourceLocationStore.getState().clear();
  workspace = { currentId: "ws", workspaceEpoch: 1, fileEpoch: 1, selectedPath: "正文/0006.md", file: { path: "正文/0006.md", digest: "six" }, openPath };
  vi.mocked(useWorkspaceStore.getState).mockImplementation(() => workspace as unknown as ReturnType<typeof useWorkspaceStore.getState>);
  openPath.mockImplementation(async (path: string) => { workspace = { ...workspace, fileEpoch: workspace.fileEpoch + 1, selectedPath: path, file: { path, digest: "old" } }; });
});

describe("source navigation", () => {
  it("keeps reading file, browser cutoff and task cutoff separate", async () => {
    api.mockResolvedValue(sample);
    const page = { kind: "writing" as const, workspaceId: "ws", asOfChapter: 6 };
    useSourceLocationStore.getState().capture(page, 4);
    await useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    expect(workspace.selectedPath).toBe("正文/0001.md");
    expect(useSourceLocationStore.getState().asOf).toBe(6);
    expect(sourceTaskChapter("ws")).toBe(4);
    expect(useSourceLocationStore.getState().returnPage).toEqual(page);
    expect(useSourceLocationStore.getState().returnPath).toBe("正文/0006.md");
  });
  it("ignores responses after workspace switch or a newer source request", async () => {
    const first = pending<SourceLocation>();
    api.mockReturnValueOnce(first.promise).mockResolvedValueOnce({ ...sample, evidence_id: "ev_new" });
    const old = useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    await useSourceLocationStore.getState().load("ws", "ev_new", 6, 4);
    first.resolve(sample); await old;
    expect(useSourceLocationStore.getState().location?.evidence_id).toBe("ev_new");
    expect(openPath).toHaveBeenCalledTimes(1);
    const second = pending<SourceLocation>(); api.mockReturnValueOnce(second.promise);
    const loading = useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    workspace = { ...workspace, currentId: "other", workspaceEpoch: 2 };
    second.resolve(sample); await loading;
    expect(openPath).toHaveBeenCalledTimes(1);
    expect(sourceTaskChapter("other")).toBeUndefined();
  });
  it("does not override a file manually selected while a source request was pending", async () => {
    const response = pending<SourceLocation>(); api.mockReturnValue(response.promise);
    const loading = useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    workspace = { ...workspace, fileEpoch: 2 };
    response.resolve(sample); await loading;
    expect(openPath).not.toHaveBeenCalled();
    expect(useSourceLocationStore.getState().evidenceId).toBeNull();
  });
  it("invalidates old coordinates when the actual file read is newer", async () => {
    api.mockResolvedValue(sample);
    openPath.mockImplementation(async (path: string) => { workspace = { ...workspace, file: { path, digest: "new" } }; });
    await useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    expect(useSourceLocationStore.getState().location).toMatchObject({ freshness: "stale", start: null, end: null });
  });
  it("preserves old quote for unavailable sources and never opens an arbitrary file", async () => {
    api.mockResolvedValue({ ...sample, freshness: "source_missing", readable: false, start: null, end: null });
    await useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    expect(openPath).not.toHaveBeenCalled();
    expect(useSourceLocationStore.getState().location?.quote).toBe("引文");
  });
  it("does not highlight cached text when the file disappears during the actual read", async () => {
    api.mockResolvedValue(sample);
    workspace = { ...workspace, file: { path: "正文/0001.md", digest: "old" } };
    openPath.mockImplementation(async () => { workspace = { ...workspace, error: "[FILE_NOT_FOUND] 文件已删除" }; });
    await useSourceLocationStore.getState().load("ws", "ev_first", 6, 4);
    expect(useSourceLocationStore.getState().location).toMatchObject({ freshness: "unavailable", readable: false, start: null, end: null });
  });
  it("freezes the return offset until the reader has actually restored it", async () => {
    vi.stubGlobal("window", { location: { hash: "#/workspaces/ws/writing?source=old" } });
    vi.stubGlobal("requestAnimationFrame", vi.fn());
    const restore = pending<void>(); openPath.mockReturnValueOnce(restore.promise);
    const key = "ws:paper:正文/0006.md";
    usePagePositions.getState().offset(key, 432);
    useSourceLocationStore.getState().capture({ kind: "writing", workspaceId: "ws", asOfChapter: 6 }, 4);
    useSourceLocationStore.setState({ workspaceId: "ws", evidenceId: "ev_first" });
    const back = useSourceLocationStore.getState().back();
    // 隐藏阅读区的旧 scroll 事件不能覆盖独立保存的返回指令。
    usePagePositions.getState().offset(key, 2433);
    expect(useSourceLocationStore.getState().restorePosition).toMatchObject({ key, offset: 432 });
    restore.resolve(); await back;
    const instruction = useSourceLocationStore.getState().restorePosition!;
    useSourceLocationStore.getState().consumeRestore(instruction.epoch);
    expect(useSourceLocationStore.getState().restorePosition).toBeNull();
    vi.unstubAllGlobals();
  });
});

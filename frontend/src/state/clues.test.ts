import { beforeEach, expect, it, vi } from "vitest";
import type { ClueDetail, ClueList } from "../api/clues";
import { WorkspaceApiError } from "../api/workspace";
vi.mock("../api/clues", () => ({ fetchClues: vi.fn(), fetchClueDetail: vi.fn(), fetchClueReview: vi.fn() }));
import { fetchClues, fetchClueDetail, fetchClueReview } from "../api/clues";
import { clueDetailKey, clueListKey, clueReviewKey, useCluesStore } from "./clues";

const list = (id: string, cursor = ""): ClueList => ({ items: [{ clue_id: id, title: id } as ClueList["items"][0]], next_cursor: cursor, total: 2, as_of_chapter: 2, current_chapter: 3, ledger_revision: "r", view_revision: "v", chapters: [], chapter: null });
const detail = (id: string): ClueDetail => ({ clue_id: id, title: id } as ClueDetail);
function pending<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>(done => { resolve = done; }); return { resolve, promise }; }
beforeEach(() => { vi.resetAllMocks(); useCluesStore.getState().reset(null); useCluesStore.setState({ panes: {}, chapterPaths: {} }); });

it("rejects late lists after workspace changes and reentry", async () => {
  const old = pending<ClueList>();
  vi.mocked(fetchClues).mockReturnValueOnce(old.promise).mockResolvedValueOnce(list("other")).mockResolvedValueOnce(list("current"));
  const loading = useCluesStore.getState().loadList("a", "two", 2);
  await useCluesStore.getState().loadList("b", "two", 2);
  await useCluesStore.getState().loadList("a", "two", 2);
  old.resolve(list("late")); await loading;
  expect(useCluesStore.getState().lists[clueListKey("two", 2)].items[0].clue_id).toBe("current");
});
it("separates chapter cutoffs and accepts only the latest request for each query", async () => {
  const old = pending<ClueList>();
  vi.mocked(fetchClues).mockReturnValueOnce(old.promise).mockResolvedValueOnce(list("new")).mockResolvedValueOnce(list("early"));
  const loading = useCluesStore.getState().loadList("a", "two", 2);
  await useCluesStore.getState().loadList("a", "two", 2);
  await useCluesStore.getState().loadList("a", "one", 1);
  old.resolve(list("old")); await loading;
  expect(useCluesStore.getState().lists[clueListKey("two", 2)].items[0].clue_id).toBe("new");
  expect(useCluesStore.getState().lists[clueListKey("one", 1)].items[0].clue_id).toBe("early");
});
it("appends without duplicate identities and keeps records on cursor conflict", async () => {
  vi.mocked(fetchClues).mockResolvedValueOnce(list("one", "next")).mockResolvedValueOnce({ ...list("two", "last"), items: [...list("one").items, ...list("two").items] }).mockRejectedValueOnce(new WorkspaceApiError("CLUE_CURSOR_CONFLICT", "分页范围已变化", "重新读取第一页", 409));
  const store = useCluesStore.getState();
  await store.loadList("a", "two", 2); await store.loadList("a", "two", 2, true);
  expect(fetchClues).toHaveBeenLastCalledWith("a", "two", 2, "next");
  await store.loadList("a", "two", 2, true);
  const key = clueListKey("two", 2);
  expect(useCluesStore.getState().lists[key].items.map(item => item.clue_id)).toEqual(["one", "two"]);
  expect(useCluesStore.getState().errors[key]).toContain("分页范围已变化");
  expect(useCluesStore.getState().loading[key]).toBe(false);
});
it("ignores obsolete details and keeps pane and chapter separately", async () => {
  const old = pending<ClueDetail>();
  vi.mocked(fetchClueDetail).mockReturnValueOnce(old.promise).mockResolvedValueOnce(detail("new"));
  const store = useCluesStore.getState(); store.setPane("a", "clues"); store.setChapter("a", "two");
  const loading = store.loadDetail("a", "id", 2); await store.loadDetail("a", "id", 2);
  old.resolve(detail("old")); await loading;
  expect(useCluesStore.getState().details[clueDetailKey("id", 2)].title).toBe("new");
  store.reset("b"); expect(useCluesStore.getState().panes.a).toBe("clues");
  expect(useCluesStore.getState().chapterPaths.a).toBe("two");
});

it("loads a review report per cutoff and keeps the old one when a newer read fails", async () => {
  const report = { report_id: "cr_1", status: "findings", findings: [], freshness: "valid" } as never;
  vi.mocked(fetchClueReview).mockResolvedValueOnce(report).mockRejectedValueOnce(new WorkspaceApiError("CLUE_REVIEW_NOT_FOUND", "报告不存在", "重新读取", 404));
  const store = useCluesStore.getState();
  await store.loadReview("a", "cr_1", 2);
  expect(useCluesStore.getState().reviews[clueReviewKey("cr_1", 2)]).toEqual(report);
  expect(fetchClueReview).toHaveBeenCalledWith("a", "cr_1", 2);
  await store.loadReview("a", "cr_1", 2);
  expect(useCluesStore.getState().reviews[clueReviewKey("cr_1", 2)]).toEqual(report);
  expect(useCluesStore.getState().errors[clueReviewKey("cr_1", 2)]).toContain("报告不存在");
});

it("keeps a late review report out of the next workspace", async () => {
  const old = pending<never>();
  vi.mocked(fetchClueReview)
    .mockReturnValueOnce(old.promise)
    .mockResolvedValueOnce({ report_id: "cr_b", status: "no_findings", findings: [], freshness: "valid" } as never);
  const loading = useCluesStore.getState().loadReview("a", "cr_a", 2);
  await useCluesStore.getState().loadReview("b", "cr_b", 2);
  old.resolve({ report_id: "cr_a", status: "findings", findings: [] } as never);
  await loading;
  expect(useCluesStore.getState().reviews[clueReviewKey("cr_a", 2)]).toBeUndefined();
  expect(useCluesStore.getState().reviews[clueReviewKey("cr_b", 2)]?.report_id).toBe("cr_b");
});

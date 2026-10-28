import { create } from "zustand";
import { fetchClueDetail, fetchClues, fetchClueReview, type ClueDetail, type ClueList, type ClueReviewReport } from "../api/clues";
import { errorMessage } from "../api/errors";

export const clueListKey = (path: string | null, asOf: number | null) => JSON.stringify(["list", path, asOf]);
export const clueDetailKey = (id: string, asOf: number | null) => JSON.stringify(["detail", id, asOf]);
export const clueReviewKey = (id: string, asOf: number | null) => JSON.stringify(["review", id, asOf]);
export type CluePane = "chat" | "clues";
interface CluesState {
  workspaceId: string | null;
  session: number;
  lists: Record<string, ClueList>;
  details: Record<string, ClueDetail>;
  reviews: Record<string, ClueReviewReport>;
  loading: Record<string, boolean>;
  errors: Record<string, string>;
  selected: Record<string, string | null>;
  panes: Record<string, CluePane>;
  chapterPaths: Record<string, string>;
  setChapter(workspaceId: string, path: string): void;
  setPane(workspaceId: string, pane: CluePane): void;
  select(key: string, id: string | null): void;
  reset(workspaceId: string | null): void;
  loadList(workspaceId: string, path: string | null, asOf: number | null, more?: boolean): Promise<void>;
  loadDetail(workspaceId: string, clueId: string, asOf: number | null): Promise<void>;
  loadReview(workspaceId: string, reportId: string, asOf: number | null): Promise<void>;
}
let serial = 0;
const requests = new Map<string, number>();
export const useCluesStore = create<CluesState>()((set, get) => ({
  workspaceId: null, session: 0, lists: {}, details: {}, reviews: {}, loading: {}, errors: {}, selected: {}, panes: {}, chapterPaths: {},
  setChapter(workspaceId, path) { if (get().chapterPaths[workspaceId] !== path) set(state => ({ chapterPaths: { ...state.chapterPaths, [workspaceId]: path } })); },
  setPane(workspaceId, pane) { set(state => ({ panes: { ...state.panes, [workspaceId]: pane } })); },
  select(key, id) { set(state => ({ selected: { ...state.selected, [key]: id } })); },
  reset(workspaceId) { set({ workspaceId, session: get().session + 1, lists: {}, details: {}, reviews: {}, loading: {}, errors: {}, selected: {} }); },
  async loadList(workspaceId, path, asOf, more = false) {
    if (get().workspaceId !== workspaceId) get().reset(workspaceId);
    const key = clueListKey(path, asOf), session = get().session, token = ++serial;
    const cursor = more ? get().lists[key]?.next_cursor ?? "" : "";
    if (more && !cursor) return;
    requests.set(key, token);
    set(state => ({ loading: { ...state.loading, [key]: true }, errors: { ...state.errors, [key]: "" } }));
    const current = () => get().workspaceId === workspaceId && get().session === session && requests.get(key) === token;
    try {
      const data = await fetchClues(workspaceId, path, asOf, cursor);
      if (!current()) return;
      const previous = more ? get().lists[key]?.items ?? [] : [];
      const items = [...new Map([...previous, ...data.items].map(item => [item.clue_id, item])).values()];
      set(state => ({ lists: { ...state.lists, [key]: { ...data, items } } }));
    } catch (error) { if (current()) set(state => ({ errors: { ...state.errors, [key]: errorMessage(error, "线索列表") } })); }
    finally { if (current()) set(state => ({ loading: { ...state.loading, [key]: false } })); }
  },
  async loadDetail(workspaceId, clueId, asOf) {
    if (get().workspaceId !== workspaceId) get().reset(workspaceId);
    const key = clueDetailKey(clueId, asOf), session = get().session, token = ++serial;
    requests.set(key, token);
    set(state => ({ loading: { ...state.loading, [key]: true }, errors: { ...state.errors, [key]: "" } }));
    const current = () => get().workspaceId === workspaceId && get().session === session && requests.get(key) === token;
    try {
      const detail = await fetchClueDetail(workspaceId, clueId, asOf);
      if (current()) set(state => ({ details: { ...state.details, [key]: detail } }));
    } catch (error) { if (current()) set(state => ({ errors: { ...state.errors, [key]: errorMessage(error, "线索详情") } })); }
    finally { if (current()) set(state => ({ loading: { ...state.loading, [key]: false } })); }
  },
  async loadReview(workspaceId, reportId, asOf) {
    if (get().workspaceId !== workspaceId) get().reset(workspaceId);
    const key = clueReviewKey(reportId, asOf), session = get().session, token = ++serial;
    requests.set(key, token);
    set(state => ({ loading: { ...state.loading, [key]: true }, errors: { ...state.errors, [key]: "" } }));
    const current = () => get().workspaceId === workspaceId && get().session === session && requests.get(key) === token;
    try {
      const report = await fetchClueReview(workspaceId, reportId, asOf);
      if (current()) set(state => ({ reviews: { ...state.reviews, [key]: report } }));
    } catch (error) { if (current()) set(state => ({ errors: { ...state.errors, [key]: errorMessage(error, "核对报告") } })); }
    finally { if (current()) set(state => ({ loading: { ...state.loading, [key]: false } })); }
  },
}));

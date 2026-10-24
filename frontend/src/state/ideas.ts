/**
 * 灵感本界面状态（0.8 C）。
 *
 * 编辑用草稿区，保存才产生修订；切换条目、打开最近删除或刷新列表时，未保存草稿
 * 不会被静默丢弃。冲突由服务端版本号判定，界面只提示刷新，不替作者合并。
 */

import { create } from "zustand";

import {
  createIdea,
  getIdea,
  listIdeaRevisions,
  listIdeas,
  requestKey,
  restoreIdea,
  trashIdea,
  updateIdea,
  type IdeaRevision,
  type IdeaSummary,
  type IdeaView,
} from "../api/ideas";
import { WorkspaceApiError } from "../api/workspace";

interface IdeaDraft {
  title: string;
  body: string;
  tags: string;
}

interface IdeasStore {
  workspaceId: string | null;
  items: IdeaSummary[];
  deletedItems: IdeaSummary[];
  total: number;
  query: string;
  showDeleted: boolean;
  loading: boolean;
  error: string | null;
  notice: string | null;
  busy: boolean;

  selectedId: string | null;
  selected: IdeaView | null;
  revisions: IdeaRevision[];
  newDraft: boolean;
  draft: IdeaDraft;
  dirty: boolean;

  load(workspaceId: string): Promise<void>;
  refresh(): Promise<void>;
  setQuery(query: string): Promise<void>;
  setShowDeleted(value: boolean): Promise<void>;
  select(ideaId: string): Promise<void>;
  startNew(): void;
  setDraft(patch: Partial<IdeaDraft>): void;
  save(): Promise<void>;
  cancel(): void;
  trashSelected(): Promise<void>;
  restoreSelected(): Promise<void>;
  restoreRevision(revisionId: string): Promise<void>;
  clearNotice(): void;
}

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) return `${error.why}。${error.fix}`;
  return error instanceof Error ? error.message : "操作没有完成";
}

const EMPTY_DRAFT: IdeaDraft = { title: "", body: "", tags: "" };

function draftOf(view: IdeaView | null): IdeaDraft {
  if (!view) return { ...EMPTY_DRAFT };
  return { title: view.title, body: view.body, tags: view.tags.join("、") };
}

function tagList(text: string): string[] {
  return text
    .split(/[、,，\s]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export const useIdeasStore = create<IdeasStore>()((set, get) => ({
  workspaceId: null,
  items: [],
  deletedItems: [],
  total: 0,
  query: "",
  showDeleted: false,
  loading: false,
  error: null,
  notice: null,
  busy: false,

  selectedId: null,
  selected: null,
  revisions: [],
  newDraft: false,
  draft: { ...EMPTY_DRAFT },
  dirty: false,

  async load(workspaceId) {
    set({
      workspaceId,
      items: [],
      deletedItems: [],
      selectedId: null,
      selected: null,
      revisions: [],
      newDraft: false,
      draft: { ...EMPTY_DRAFT },
      dirty: false,
      error: null,
      notice: null,
    });
    await get().refresh();
  },

  async refresh() {
    const { workspaceId, query } = get();
    if (!workspaceId) return;
    set({ loading: true, error: null });
    try {
      const [active, deleted] = await Promise.all([
        listIdeas(workspaceId, { q: query, limit: 50 }),
        listIdeas(workspaceId, { q: query, deleted: true, limit: 50 }),
      ]);
      if (get().workspaceId !== workspaceId) return;
      set({
        items: active.items,
        deletedItems: deleted.items,
        total: active.total,
        loading: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ loading: false, error: describe(error) });
    }
  },

  async setQuery(query) {
    set({ query });
    await get().refresh();
  },

  async setShowDeleted(value) {
    set({ showDeleted: value });
  },

  async select(ideaId) {
    const { workspaceId, dirty } = get();
    if (!workspaceId) return;
    if (dirty) {
      set({ notice: "当前草稿还没保存；先保存或取消，再切换到其他灵感。" });
      return;
    }
    set({ busy: true, error: null, notice: null, selectedId: ideaId, newDraft: false });
    try {
      const [view, revisions] = await Promise.all([
        getIdea(workspaceId, ideaId),
        listIdeaRevisions(workspaceId, ideaId),
      ]);
      if (get().workspaceId !== workspaceId) return;
      set({
        selected: view,
        revisions: revisions.items,
        draft: draftOf(view),
        dirty: false,
        busy: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ busy: false, error: describe(error) });
    }
  },

  startNew() {
    if (get().dirty) {
      set({ notice: "当前草稿还没保存；先保存或取消，再新建灵感。" });
      return;
    }
    set({
      selectedId: null,
      selected: null,
      revisions: [],
      newDraft: true,
      draft: { ...EMPTY_DRAFT },
      dirty: false,
      error: null,
      notice: null,
    });
  },

  setDraft(patch) {
    set((state) => ({ draft: { ...state.draft, ...patch }, dirty: true }));
  },

  async save() {
    const { workspaceId, selected, newDraft, draft, dirty } = get();
    if (!workspaceId || !dirty) return;
    if (!draft.body.trim()) {
      set({ error: "灵感正文不能为空。" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const view =
        newDraft || !selected
          ? await createIdea(workspaceId, {
              title: draft.title,
              body: draft.body,
              tags: tagList(draft.tags),
              request_key: requestKey("idea"),
            })
          : await updateIdea(workspaceId, selected.idea_id, {
              expected_digest: selected.digest,
              expected_revision: selected.revision_id,
              title: draft.title,
              body: draft.body,
              tags: tagList(draft.tags),
              request_key: requestKey("idea"),
            });
      if (get().workspaceId !== workspaceId) return;
      set({
        selected: view,
        selectedId: view.idea_id,
        newDraft: false,
        draft: draftOf(view),
        dirty: false,
        busy: false,
        notice: "已保存为新的灵感修订。",
      });
      await get().refresh();
      const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
      set({ revisions: revisions.items });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ busy: false, error: describe(error) });
    }
  },

  cancel() {
    const { selected } = get();
    if (selected) {
      set({ draft: draftOf(selected), dirty: false });
    } else {
      set({
        selectedId: null,
        selected: null,
        revisions: [],
        newDraft: false,
        draft: { ...EMPTY_DRAFT },
        dirty: false,
      });
    }
  },

  async trashSelected() {
    const { workspaceId, selected } = get();
    if (!workspaceId || !selected || selected.deleted) return;
    set({ busy: true, error: null, notice: null });
    try {
      const view = await trashIdea(workspaceId, selected.idea_id, {
        expected_digest: selected.digest,
        expected_revision: selected.revision_id,
        request_key: requestKey("idea-trash"),
      });
      set({
        selected: view,
        draft: draftOf(view),
        dirty: false,
        busy: false,
        notice: "已移到最近删除；可从那里恢复。",
      });
      await get().refresh();
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreSelected() {
    const { workspaceId, selected } = get();
    if (!workspaceId || !selected || !selected.deleted) return;
    set({ busy: true, error: null, notice: null });
    try {
      // 恢复「删除状态」的修订只会再生成一条删除修订；撤销删除要恢复最近一条未删除的历史。
      const history = await listIdeaRevisions(workspaceId, selected.idea_id);
      const alive = history.items.find((item) => !item.deleted);
      if (!alive) {
        set({ busy: false, error: "这条灵感只有已删除的修订，无法恢复。" });
        return;
      }
      const view = await restoreIdea(workspaceId, selected.idea_id, {
        target_revision: alive.id,
        expected_digest: selected.digest,
        request_key: requestKey("idea-restore"),
      });
      set({
        selected: view,
        draft: draftOf(view),
        dirty: false,
        busy: false,
        notice: "已恢复；恢复会生成一条新修订。",
      });
      await get().refresh();
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreRevision(revisionId) {
    const { workspaceId, selected } = get();
    if (!workspaceId || !selected) return;
    set({ busy: true, error: null, notice: null });
    try {
      const view = await restoreIdea(workspaceId, selected.idea_id, {
        target_revision: revisionId,
        expected_digest: selected.digest,
        request_key: requestKey("idea-history"),
      });
      set({
        selected: view,
        draft: draftOf(view),
        dirty: false,
        busy: false,
        notice: "已把这一版恢复为当前修订；后续修订仍保留。",
      });
      const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
      set({ revisions: revisions.items });
      await get().refresh();
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  clearNotice() {
    set({ notice: null, error: null });
  },
}));

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
  getIdeaRevision,
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
import { errorMessage } from "../api/errors";

interface IdeaDraft {
  title: string;
  body: string;
  tags: string;
}

interface IdeaSaveCommand {
  signature: string;
  request: { title: string; body: string; tags: string[]; request_key: string; expected_digest?: string; expected_revision?: string };
  running: boolean;
}
interface IdeasStore {
  reading: boolean;
  cursor: string | null;
  deletedCursor: string | null;
  loadingMore: boolean;
  revisionsCursor: string | null;
  revisionsLoading: boolean;
  preview: Awaited<ReturnType<typeof getIdeaRevision>> | null;
  previewEpoch: number;
  previewLoading: boolean;
  loadMore(): Promise<void>;
  loadMoreRevisions(): Promise<void>;
  previewRevision(id: string): Promise<void>;
  closePreview(): void;
  activeKey: string | null;
  editSequence: number;
  selectionEpoch: number;
  newDraftSequence: number;
  queryEpoch: number;
  entryEditors: Record<string, Record<string, IdeaEditorState>>;
  saveCommands: Record<string, IdeaSaveCommand>;
  selectDraft(key: string): void;
  workspaceId: string | null;
  session: number;
  editors: Record<string, IdeaEditorState>;
  items: IdeaSummary[];
  deletedItems: IdeaSummary[];
  total: number;
  deletedTotal: number;
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

type IdeaEditorState = Pick<
  IdeasStore,
  "revisionsCursor" | "activeKey" | "editSequence" | "query" | "showDeleted" | "selectedId" | "selected" | "revisions" | "newDraft" | "draft" | "dirty"
>;

function editorOf(state: IdeasStore): IdeaEditorState {
  const { revisionsCursor, activeKey, editSequence, query, showDeleted, selectedId, selected, revisions, newDraft, draft, dirty } = state;
  return { revisionsCursor, activeKey, editSequence, query, showDeleted, selectedId, selected, revisions, newDraft, draft, dirty };
}

function remember(state: IdeasStore): IdeasStore["entryEditors"] {
  if (!state.workspaceId || !state.activeKey) return state.entryEditors;
  return { ...state.entryEditors, [state.workspaceId]: { ...state.entryEditors[state.workspaceId], [state.activeKey]: editorOf(state) } };
}

function describe(error: unknown): string {
  return errorMessage(error, "灵感操作");
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
  reading: false, cursor: null, deletedCursor: null, loadingMore: false, revisionsCursor: null, revisionsLoading: false, preview: null, previewLoading: false, previewEpoch: 0,
  activeKey: null, editSequence: 0, selectionEpoch: 0, queryEpoch: 0, newDraftSequence: 0,
  entryEditors: {}, saveCommands: {},
  workspaceId: null,
  session: 0,
  editors: {},
  items: [],
  deletedItems: [],
  total: 0,
  deletedTotal: 0,
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
    const current = get();
    // 页面重新挂载只刷新列表，不重置编辑器；不同作品分别保留草稿。
    if (current.workspaceId === workspaceId) {
      await get().refresh();
      return;
    }
    const entryEditors = remember(current);
    const editors = { ...current.editors };
    if (current.workspaceId) editors[current.workspaceId] = editorOf(current);
    const editor: IdeaEditorState = editors[workspaceId] ?? {
      revisionsCursor: null, activeKey: null, editSequence: 0,
      query: "",
      showDeleted: false,
      selectedId: null,
      selected: null,
      revisions: [],
      newDraft: false,
      draft: { ...EMPTY_DRAFT },
      dirty: false,
    };
    set({
      workspaceId,
      session: current.session + 1,
      editors, entryEditors, selectionEpoch: current.selectionEpoch + 1, queryEpoch: current.queryEpoch + 1,
      preview: null, previewLoading: false, previewEpoch: current.previewEpoch + 1, cursor: null, deletedCursor: null, loadingMore: false,
      items: [],
      deletedItems: [],
      total: 0,
      ...editor,
      reading: false, loading: false,
      busy: false,
      error: null,
      notice: null,
    });
    await get().refresh();
  },

  async refresh() {
    const { workspaceId, query, session } = get();
    if (!workspaceId) return;
    const epoch = get().queryEpoch + 1;
    set({ loading: true, loadingMore: false, error: null, queryEpoch: epoch });
    try {
      const [active, deleted] = await Promise.all([
        listIdeas(workspaceId, { q: query, limit: 50 }),
        listIdeas(workspaceId, { q: query, deleted: true, limit: 50 }),
      ]);
      if (get().session !== session || get().query !== query || get().queryEpoch !== epoch) return;
      set({
        items: active.items,
        deletedItems: deleted.items, cursor: active.next_cursor, deletedCursor: deleted.next_cursor,
        total: active.total, deletedTotal: deleted.total,
        loading: false,
      });
    } catch (error) {
      if (get().session !== session || get().query !== query || get().queryEpoch !== epoch) return;
      set({ loading: false, error: describe(error) });
    }
  },

  async loadMore() {
    const state = get();
    const { workspaceId, query, showDeleted, queryEpoch, session } = state;
    const cursor = showDeleted ? state.deletedCursor : state.cursor;
    if (!workspaceId || !cursor || state.loadingMore) return;
    set({ loadingMore: true });
    try {
      const page = await listIdeas(workspaceId, { q: query, deleted: showDeleted, cursor, limit: 50 });
      if (get().session !== session || get().queryEpoch !== queryEpoch) return;
      const items = showDeleted ? get().deletedItems : get().items;
      const merged = [...new Map([...items, ...page.items].map(item => [item.idea_id, item])).values()];
      set(showDeleted ? { deletedItems: merged, deletedCursor: page.next_cursor, loadingMore: false } : { items: merged, cursor: page.next_cursor, loadingMore: false });
    } catch (error) {
      if (get().session === session && get().queryEpoch === queryEpoch) set({ loadingMore: false, error: describe(error) });
    }
  },

  async loadMoreRevisions() {
    const { workspaceId, selectedId, revisionsCursor: cursor, revisionsLoading, selectionEpoch, session } = get();
    if (!workspaceId || !selectedId || !cursor || revisionsLoading) return;
    set({ revisionsLoading: true });
    try {
      const page = await listIdeaRevisions(workspaceId, selectedId, { cursor });
      if (get().session !== session || get().selectionEpoch !== selectionEpoch) return;
      set({ revisions: [...new Map([...get().revisions, ...page.items].map(item => [item.id, item])).values()], revisionsCursor: page.next_cursor, revisionsLoading: false });
    } catch (error) {
      if (get().session === session && get().selectionEpoch === selectionEpoch) set({ revisionsLoading: false, error: describe(error) });
    }
  },

  async previewRevision(id) {
    const { workspaceId, selectedId, session, selectionEpoch } = get();
    if (!workspaceId || !selectedId) return;
    const epoch = get().previewEpoch + 1;
    set({ preview: null, previewLoading: true, previewEpoch: epoch });
    try {
      const preview = await getIdeaRevision(workspaceId, selectedId, id);
      if (get().session !== session || get().selectionEpoch !== selectionEpoch || get().previewEpoch !== epoch) return;
      set({ preview, previewLoading: false });
    } catch (error) {
      if (get().session === session && get().selectionEpoch === selectionEpoch && get().previewEpoch === epoch) set({ previewLoading: false, error: describe(error) });
    }
  },
  closePreview() { set({ preview: null, previewLoading: false, previewEpoch: get().previewEpoch + 1 }); },

  async setQuery(query) {
    set({ query });
    await get().refresh();
  },

  async setShowDeleted(value) {
    set({ showDeleted: value });
  },

  async select(ideaId) {
    const current = get();
    const { workspaceId, session } = current;
    if (!workspaceId || (current.selectedId === ideaId && current.selected)) return;
    const entryEditors = remember(current);
    const cached = entryEditors[workspaceId]?.[ideaId];
    const epoch = current.selectionEpoch + 1;
    set({ ...cached, query: current.query, showDeleted: current.showDeleted, entryEditors, activeKey: ideaId, selectedId: ideaId,
      preview: null, previewLoading: false, previewEpoch: current.previewEpoch + 1, revisionsLoading: false, revisionsCursor: cached?.revisionsCursor ?? null,
      selected: cached?.selected ?? null, draft: cached?.draft ?? { ...EMPTY_DRAFT },
      revisions: cached?.revisions ?? [], dirty: cached?.dirty ?? false, editSequence: cached?.editSequence ?? 0,
      selectionEpoch: epoch, reading: true, busy: false, error: null, notice: null, newDraft: false });
    try {
      const [view, revisions] = await Promise.all([getIdea(workspaceId, ideaId), listIdeaRevisions(workspaceId, ideaId)]);
      if (get().session !== session || get().selectionEpoch !== epoch) return;
      set({ reading: false, selected: get().dirty && get().selected ? get().selected : view,
        revisions: revisions.items, revisionsCursor: revisions.next_cursor, draft: get().dirty ? get().draft : draftOf(view) });
    } catch (error) {
      if (get().session !== session || get().selectionEpoch !== epoch) return;
      set({ reading: false, error: describe(error) });
    }
  },

  selectDraft(key) {
    const state = get();
    if (!state.workspaceId || state.activeKey === key) return;
    const entryEditors = remember(state);
    const cached = entryEditors[state.workspaceId]?.[key];
    if (cached) set({ ...cached, entryEditors, query: state.query, showDeleted: state.showDeleted,
      selectionEpoch: state.selectionEpoch + 1, reading: false, busy: false, error: null, notice: null, preview: null, previewLoading: false, previewEpoch: state.previewEpoch + 1, revisionsLoading: false });
  },

  startNew() {
    const state = get();
    set({ entryEditors: remember(state), activeKey: `new-idea-${state.newDraftSequence + 1}`, newDraftSequence: state.newDraftSequence + 1, editSequence: 0,
      selectionEpoch: state.selectionEpoch + 1, reading: false, revisionsLoading: false, selectedId: null, selected: null, revisions: [],
      newDraft: true, draft: { ...EMPTY_DRAFT }, dirty: false, busy: false, error: null, notice: null, revisionsCursor: null, preview: null, previewLoading: false, previewEpoch: state.previewEpoch + 1 });
  },

  setDraft(patch) {
    set(state => ({ draft: { ...state.draft, ...patch }, dirty: true, editSequence: state.editSequence + 1 }));
    set({ entryEditors: remember(get()) });
  },

  async save() {
    const state = get();
    const { workspaceId, selected, newDraft, draft, dirty, activeKey, editSequence } = state;
    if (!workspaceId || !activeKey || !dirty) return;
    if (!draft.body.trim()) {
      set({ error: "[IDEA_BODY_REQUIRED] 灵感正文：正文不能为空。建议：输入正文后再保存。" });
      return;
    }
    const address = JSON.stringify([workspaceId, activeKey]);
    const payload = { title: draft.title, body: draft.body, tags: tagList(draft.tags),
      ...(!newDraft && selected ? { expected_digest: selected.digest, expected_revision: selected.revision_id } : {}) };
    const signature = JSON.stringify(payload);
    let command = state.saveCommands[address];
    if (command?.running) return;
    if (!command || command.signature !== signature) command = { signature, request: { ...payload, request_key: requestKey("idea") }, running: false };
    command = { ...command, running: true };
    set({ busy: true, error: null, notice: null, saveCommands: { ...state.saveCommands, [address]: command } });
    try {
      const view = newDraft || !selected
        ? await createIdea(workspaceId, command.request)
        : await updateIdea(workspaceId, selected.idea_id, command.request as Required<typeof command.request>);
      const current = get();
      const cache = remember(current);
      const latest = current.workspaceId === workspaceId && current.activeKey === activeKey
        ? editorOf(current) : cache[workspaceId]?.[activeKey] ?? editorOf(state);
      const editor = { ...latest, activeKey: view.idea_id, selectedId: view.idea_id, selected: view, newDraft: false,
        draft: latest.editSequence === editSequence ? draftOf(view) : latest.draft, dirty: latest.editSequence !== editSequence };
      const entries = { ...cache[workspaceId] };
      delete entries[activeKey]; entries[view.idea_id] = editor;
      const editors = { ...current.editors };
      if (editors[workspaceId]?.activeKey === activeKey) editors[workspaceId] = editor;
      set({ entryEditors: { ...cache, [workspaceId]: entries }, editors });
      if (current.workspaceId === workspaceId && current.activeKey === activeKey) {
        set({ ...editor, selectionEpoch: current.selectionEpoch + 1, query: current.query, showDeleted: current.showDeleted, busy: false, notice: "已保存为新的灵感修订。" });
        const epoch = get().selectionEpoch;
        const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
        if (get().workspaceId === workspaceId && get().activeKey === view.idea_id && get().selectionEpoch === epoch) set({ revisions: revisions.items, revisionsCursor: revisions.next_cursor });
      }
      if (get().workspaceId === workspaceId) await get().refresh();
    } catch (error) {
      if (get().workspaceId === workspaceId && get().activeKey === activeKey) set({ busy: false, error: describe(error) });
    } finally {
      set({ saveCommands: { ...get().saveCommands, [address]: { ...command, running: false } } });
    }
  },

  cancel() {
    if (get().busy) return;
    const { selected, workspaceId, activeKey } = get();
    if (selected) {
      set({ draft: draftOf(selected), dirty: false });
    } else {
      set({
        activeKey: null,
        selectedId: null,
        selected: null,
        revisions: [],
        newDraft: false,
        draft: { ...EMPTY_DRAFT },
        dirty: false,
      });
    }
    if (!selected && workspaceId && activeKey) {
      const entries = { ...get().entryEditors[workspaceId] };
      delete entries[activeKey];
      set({ entryEditors: { ...get().entryEditors, [workspaceId]: entries } });
    }
    set({ entryEditors: remember(get()) });
  },

  async trashSelected() {
    const { workspaceId, selected, dirty, busy, session, selectionEpoch } = get();
    if (!workspaceId || !selected || selected.deleted || busy) return;
    if (dirty) {
      set({ notice: "当前草稿还没保存；先保存或放弃修改，再删除灵感。" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const view = await trashIdea(workspaceId, selected.idea_id, {
        expected_digest: selected.digest,
        expected_revision: selected.revision_id,
        request_key: requestKey("idea-trash"),
      });
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({
        selected: view,
        draft: get().dirty ? get().draft : draftOf(view),
        dirty: get().dirty, preview: null, previewLoading: false, previewEpoch: get().previewEpoch + 1,
        busy: false,
        notice: "已移到最近删除；可从那里恢复。",
      });
      const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
      if (get().session !== session || get().selectedId !== view.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ revisions: revisions.items, revisionsCursor: revisions.next_cursor });
      await get().refresh();
    } catch (error) {
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreSelected() {
    const { workspaceId, selected, dirty, busy, session, selectionEpoch } = get();
    if (!workspaceId || !selected || !selected.deleted || busy) return;
    if (dirty) {
      set({ notice: "当前草稿还没保存；先保存或放弃修改，再恢复灵感。" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      // 恢复「删除状态」的修订只会再生成一条删除修订；撤销删除要恢复最近一条未删除的历史。
      let history = await listIdeaRevisions(workspaceId, selected.idea_id);
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      let alive = history.items.find((item) => !item.deleted);
      const seen = new Set<string>();
      while (!alive && history.next_cursor) {
        if (seen.has(history.next_cursor)) throw new Error("[IDEA_CURSOR_INVALID] 灵感历史：分页游标重复。建议：重新读取历史。");
        seen.add(history.next_cursor);
        history = await listIdeaRevisions(workspaceId, selected.idea_id, { cursor: history.next_cursor });
        if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
        alive = history.items.find(item => !item.deleted);
      }
      if (!alive) {
        set({ busy: false, error: "[IDEA_RESTORE_UNAVAILABLE] 灵感历史：只有删除修订，无法恢复正文。建议：核对历史后新建灵感。" });
        return;
      }
      const view = await restoreIdea(workspaceId, selected.idea_id, {
        target_revision: alive.id,
        expected_digest: selected.digest,
        request_key: requestKey("idea-restore"),
      });
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({
        selected: view,
        draft: get().dirty ? get().draft : draftOf(view),
        dirty: get().dirty, preview: null, previewLoading: false, previewEpoch: get().previewEpoch + 1,
        busy: false,
        notice: "已恢复；恢复会生成一条新修订。",
      });
      const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
      if (get().session !== session || get().selectedId !== view.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ revisions: revisions.items, revisionsCursor: revisions.next_cursor });
      await get().refresh();
    } catch (error) {
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreRevision(revisionId) {
    const { workspaceId, selected, dirty, busy, session, selectionEpoch } = get();
    if (!workspaceId || !selected || busy) return;
    if (dirty) {
      set({ notice: "当前草稿还没保存；先保存或放弃修改，再恢复历史修订。" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const view = await restoreIdea(workspaceId, selected.idea_id, {
        target_revision: revisionId,
        expected_digest: selected.digest,
        request_key: requestKey("idea-history"),
      });
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({
        selected: view,
        draft: get().dirty ? get().draft : draftOf(view),
        dirty: get().dirty, preview: null, previewLoading: false, previewEpoch: get().previewEpoch + 1,
        busy: false,
        notice: "已把这一版恢复为当前修订；后续修订仍保留。",
      });
      const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
      if (get().session !== session || get().selectedId !== view.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ revisions: revisions.items, revisionsCursor: revisions.next_cursor });
      await get().refresh();
    } catch (error) {
      if (get().session !== session || get().selectedId !== selected.idea_id || get().selectionEpoch !== selectionEpoch) return;
      set({ busy: false, error: describe(error) });
    }
  },

  clearNotice() {
    set({ notice: null, error: null });
  },
}));

/**
 * 灵感本界面状态（0.8 I1）。
 *
 * 两件事分开：
 *
 * - **快速记录**：一个只有正文的草稿，提交后清空，可以连续记录。
 * - **条目编辑**：按条目保存修改稿，带编辑序号与基线修订。
 *
 * 保存是一次冻结的命令：请求键、正文快照和编辑序号在提交时确定。保存期间继续输入
 * 不会丢，也不会把同一段文字另建成第二条灵感；结果不明时用同一个请求键重放。
 * 冲突由服务端版本判定，界面只提示，不替作者合并。
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
  type IdeaRevisionView,
  type IdeaSummary,
  type IdeaView,
} from "../api/ideas";
import { errorMessage } from "../api/errors";

export interface IdeaDraft {
  /** 草稿标识：快速记录用 `capture`，条目修改稿用条目 ID。 */
  key: string;
  workspaceId: string;
  ideaId?: string;
  body: string;
  /** 每次输入 +1；保存结果只在与快照序号相同时清空或替换正文。 */
  editSequence: number;
  /** 保存基线：修订、指纹与已保存的正文。没有基线表示这是一条新记录。 */
  base?: { revisionId: string; digest: string; body: string };
}

export interface SavedIdea {
  workspaceId: string;
  ideaId: string;
  revisionId: string;
  digest: string;
  body: string;
}

export type SaveResult =
  | { status: "saved" | "unchanged"; value: SavedIdea }
  | { status: "failed"; error: string };

export interface IdeaScope {
  capture: IdeaDraft;
  editing: Record<string, IdeaDraft>;
  activeId: string | null;
  selected: IdeaView | null;
  revisions: IdeaRevision[];
  revisionsCursor: string | null;
  query: string;
  showDeleted: boolean;
  /** 刚删除的条目：界面据此提供「撤销」。 */
  lastTrashed: string | null;
}

interface Command {
  signature: string;
  requestKey: string;
}

interface IdeasStore {
  workspaceId: string | null;
  scopes: Record<string, IdeaScope>;
  session: number;

  items: IdeaSummary[];
  deletedItems: IdeaSummary[];
  total: number;
  deletedTotal: number;
  invalid: number;
  cursor: string | null;
  deletedCursor: string | null;
  loading: boolean;
  loadingMore: boolean;

  reading: boolean;
  revisionsLoading: boolean;
  preview: IdeaRevisionView | null;
  previewLoading: boolean;
  previewEpoch: number;

  busy: boolean;
  error: string | null;
  notice: string | null;

  queryEpoch: number;
  selectionEpoch: number;
  commands: Record<string, Command>;

  load(workspaceId: string): Promise<void>;
  refresh(): Promise<void>;
  loadMore(): Promise<void>;
  setQuery(query: string): Promise<void>;
  setShowDeleted(value: boolean): Promise<void>;

  select(ideaId: string): Promise<void>;
  clearSelection(): void;

  setCaptureBody(body: string): void;
  capture(): Promise<SaveResult>;
  setEntryBody(ideaId: string, body: string): void;
  saveEntry(ideaId: string): Promise<SaveResult>;
  discardEntry(ideaId: string): void;
  saveExact(workspaceId: string, draftKey: string): Promise<SaveResult>;
  /** 引用前的固定版本：有脏稿先保存，成功才返回可引用的修订。 */
  prepareReference(ideaId: string): Promise<SavedIdea | null>;

  trashEntry(ideaId: string): Promise<void>;
  restoreEntry(ideaId: string): Promise<void>;
  restoreRevision(revisionId: string): Promise<void>;
  undoTrash(): Promise<void>;

  previewRevision(revisionId: string): Promise<void>;
  loadMoreRevisions(): Promise<void>;
  closePreview(): void;
  clearNotice(): void;
}

const EMPTY_SCOPE: IdeaScope = {
  capture: { key: "capture", workspaceId: "", body: "", editSequence: 0 },
  editing: {},
  activeId: null,
  selected: null,
  revisions: [],
  revisionsCursor: null,
  query: "",
  showDeleted: false,
  lastTrashed: null,
};

/** 新建一个空作用域；作品切换与测试都用它，避免共享可变对象。 */
export function newScope(workspaceId: string): IdeaScope {
  return { ...EMPTY_SCOPE, capture: { ...EMPTY_SCOPE.capture, workspaceId } };
}

/** 同一草稿同时只允许一次保存；重复点击拿到同一个结果。 */
const inflight = new Map<string, Promise<SaveResult>>();

export function scopeOf(state: IdeasStore, workspaceId?: string | null): IdeaScope {
  const id = workspaceId ?? state.workspaceId;
  if (!id) return EMPTY_SCOPE;
  return state.scopes[id] ?? newScope(id);
}

function patchScope(state: IdeasStore, patch: Partial<IdeaScope>): Partial<IdeasStore> {
  const id = state.workspaceId;
  if (!id) return {};
  return { scopes: { ...state.scopes, [id]: { ...scopeOf(state, id), ...patch } } };
}

export function draftDirty(draft: IdeaDraft): boolean {
  if (!draft.base) return draft.body.length > 0;
  return draft.body !== draft.base.body;
}

/** 引用预览：合并空白后截断，只用于显示，不写回服务端。 */
export function referencePreview(body: string, limit = 48): string {
  const text = body.replace(/\s+/gu, " ").trim();
  const chars = Array.from(text);
  return chars.slice(0, limit).join("") + (chars.length > limit ? "…" : "");
}

/** 当前作品的灵感作用域；作品还没载入时返回空作用域，不新建对象。 */
export function useIdeaScope(): IdeaScope {
  const scope = useIdeasStore(state =>
    state.workspaceId ? state.scopes[state.workspaceId] : undefined,
  );
  return scope ?? EMPTY_SCOPE;
}

function freshDraft(workspaceId: string, view: IdeaView): IdeaDraft {
  return {
    key: view.idea_id,
    workspaceId,
    ideaId: view.idea_id,
    body: view.body,
    editSequence: 0,
    base: { revisionId: view.revision_id, digest: view.digest, body: view.body },
  };
}

function draftIn(scope: IdeaScope, draftKey: string): IdeaDraft | undefined {
  if (draftKey === "capture") return scope.capture;
  return scope.editing[draftKey];
}

function describe(error: unknown): string {
  return errorMessage(error, "灵感操作");
}

const BODY_REQUIRED = "[IDEA_BODY_REQUIRED] 灵感正文：正文不能为空。建议：写一句想记下来的内容后再保存。";

export const useIdeasStore = create<IdeasStore>()((set, get) => {
  function commandFor(address: string, signature: string, prefix: string): Command {
    const existing = get().commands[address];
    if (existing && existing.signature === signature) return existing;
    const command: Command = { signature, requestKey: requestKey(prefix) };
    set({ commands: { ...get().commands, [address]: command } });
    return command;
  }

  async function reconcile(
    workspaceId: string,
    draftKey: string,
    snapshot: IdeaDraft,
    view: IdeaView,
  ): Promise<SaveResult> {
    const saved: SavedIdea = {
      workspaceId,
      ideaId: view.idea_id,
      revisionId: view.revision_id,
      digest: view.digest,
      body: view.body,
    };
    const unchanged = Boolean(snapshot.base && view.revision_id === snapshot.base.revisionId);
    const state = get();
    const scope = scopeOf(state, workspaceId);
    const latest = draftIn(scope, draftKey) ?? snapshot;
    const clean = latest.editSequence === snapshot.editSequence;
    const base = { revisionId: view.revision_id, digest: view.digest, body: view.body };

    if (draftKey === "capture") {
      const nextCapture: IdeaDraft = {
        key: "capture",
        workspaceId,
        body: "",
        editSequence: 0,
      };
      const editing = { ...scope.editing };
      if (!clean) {
        // 保存期间作者又写了内容：留在同一条灵感的修改稿里，下一次保存更新它。
        editing[view.idea_id] = {
          key: view.idea_id,
          workspaceId,
          ideaId: view.idea_id,
          body: latest.body,
          editSequence: latest.editSequence,
          base,
        };
      }
      set({
        scopes: {
          ...state.scopes,
          [workspaceId]: {
            ...scope,
            capture: nextCapture,
            editing,
            activeId: state.workspaceId === workspaceId ? view.idea_id : scope.activeId,
          },
        },
        notice: clean ? "已记录。" : "已记录；之后输入的内容留在同一条灵感的修改稿里。",
        busy: false,
      });
    } else {
      const editing = {
        ...scope.editing,
        [view.idea_id]: {
          ...latest,
          key: view.idea_id,
          ideaId: view.idea_id,
          body: clean ? view.body : latest.body,
          base,
        },
      };
      set({
        scopes: { ...state.scopes, [workspaceId]: { ...scope, editing } },
        notice: unchanged ? "内容没有变化。" : "已保存为新的灵感修订。",
        busy: false,
      });
    }

    if (get().workspaceId === workspaceId) {
      const current = scopeOf(get(), workspaceId);
      if (current.activeId === view.idea_id) {
        set(patchScope(get(), { selected: view }));
        const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
        if (scopeOf(get(), workspaceId).activeId === view.idea_id) {
          set(
            patchScope(get(), {
              revisions: revisions.items,
              revisionsCursor: revisions.next_cursor,
            }),
          );
        }
      }
      await get().refresh();
    }
    return { status: unchanged ? "unchanged" : "saved", value: saved };
  }

  async function runSave(workspaceId: string, draftKey: string): Promise<SaveResult> {
    const state = get();
    const scope = scopeOf(state, workspaceId);
    const draft = draftIn(scope, draftKey);
    if (!draft) return { status: "failed", error: "找不到要保存的草稿。" };
    if (!draft.body.trim()) {
      set({ error: BODY_REQUIRED, busy: false });
      return { status: "failed", error: BODY_REQUIRED };
    }
    const snapshot: IdeaDraft = { ...draft, base: draft.base ? { ...draft.base } : undefined };
    const address = `${workspaceId}:${draftKey}:save`;
    const signature = JSON.stringify([
      snapshot.body,
      snapshot.ideaId ?? "",
      snapshot.base?.revisionId ?? "",
    ]);
    const command = commandFor(address, signature, snapshot.ideaId ? "idea-edit" : "idea");
    set({ busy: true, error: null, notice: null });
    try {
      const view =
        snapshot.ideaId && snapshot.base
          ? await updateIdea(workspaceId, snapshot.ideaId, {
              body: snapshot.body,
              request_key: command.requestKey,
              expected_digest: snapshot.base.digest,
              expected_revision: snapshot.base.revisionId,
            })
          : await createIdea(workspaceId, {
              body: snapshot.body,
              request_key: command.requestKey,
            });
      return await reconcile(workspaceId, draftKey, snapshot, view);
    } catch (error) {
      const message = describe(error);
      set({ busy: false, error: message });
      return { status: "failed", error: message };
    }
  }

  return {
    workspaceId: null,
    scopes: {},
    session: 0,

    items: [],
    deletedItems: [],
    total: 0,
    deletedTotal: 0,
    invalid: 0,
    cursor: null,
    deletedCursor: null,
    loading: false,
    loadingMore: false,

    reading: false,
    revisionsLoading: false,
    preview: null,
    previewLoading: false,
    previewEpoch: 0,

    busy: false,
    error: null,
    notice: null,

    queryEpoch: 0,
    selectionEpoch: 0,
    commands: {},

    async load(workspaceId) {
      const current = get();
      if (current.workspaceId === workspaceId) {
        await get().refresh();
        return;
      }
      const scope = current.scopes[workspaceId];
      set({
        workspaceId,
        session: current.session + 1,
        scopes: scope ? current.scopes : { ...current.scopes, [workspaceId]: newScope(workspaceId) },
        items: [],
        deletedItems: [],
        total: 0,
        deletedTotal: 0,
        invalid: 0,
        cursor: null,
        deletedCursor: null,
        loading: false,
        loadingMore: false,
        reading: false,
        revisionsLoading: false,
        preview: null,
        previewLoading: false,
        previewEpoch: current.previewEpoch + 1,
        busy: false,
        error: null,
        notice: null,
        queryEpoch: current.queryEpoch + 1,
        selectionEpoch: current.selectionEpoch + 1,
      });
      await get().refresh();
    },

    async refresh() {
      const { workspaceId, session } = get();
      if (!workspaceId) return;
      const scope = scopeOf(get(), workspaceId);
      const epoch = get().queryEpoch + 1;
      set({ loading: true, loadingMore: false, error: null, queryEpoch: epoch });
      try {
        const [active, deleted] = await Promise.all([
          listIdeas(workspaceId, { q: scope.query, order: "created", limit: 50 }),
          listIdeas(workspaceId, { q: scope.query, order: "created", deleted: true, limit: 50 }),
        ]);
        if (get().session !== session || get().queryEpoch !== epoch) return;
        set({
          items: active.items,
          deletedItems: deleted.items,
          cursor: active.next_cursor,
          deletedCursor: deleted.next_cursor,
          total: active.total,
          deletedTotal: deleted.total,
          invalid: active.invalid,
          loading: false,
        });
      } catch (error) {
        if (get().session !== session || get().queryEpoch !== epoch) return;
        set({ loading: false, error: describe(error) });
      }
    },

    async loadMore() {
      const state = get();
      const { workspaceId, session, queryEpoch, loadingMore } = state;
      if (!workspaceId || loadingMore) return;
      const scope = scopeOf(state, workspaceId);
      const deleted = scope.showDeleted;
      const cursor = deleted ? state.deletedCursor : state.cursor;
      if (!cursor) return;
      set({ loadingMore: true });
      try {
        const page = await listIdeas(workspaceId, {
          q: scope.query,
          deleted,
          cursor,
          order: "created",
          limit: 50,
        });
        if (get().session !== session || get().queryEpoch !== queryEpoch) return;
        const items = deleted ? get().deletedItems : get().items;
        const merged = [...new Map([...items, ...page.items].map(item => [item.idea_id, item])).values()];
        set(
          deleted
            ? { deletedItems: merged, deletedCursor: page.next_cursor, loadingMore: false }
            : { items: merged, cursor: page.next_cursor, loadingMore: false },
        );
      } catch (error) {
        if (get().session === session && get().queryEpoch === queryEpoch) {
          set({ loadingMore: false, error: describe(error) });
        }
      }
    },

    async setQuery(query) {
      set(patchScope(get(), { query }));
      await get().refresh();
    },

    async setShowDeleted(value) {
      set(patchScope(get(), { showDeleted: value }));
    },

    async select(ideaId) {
      const current = get();
      const { workspaceId, session } = current;
      if (!workspaceId) return;
      const scope = scopeOf(current, workspaceId);
      if (scope.activeId === ideaId && scope.selected) return;
      const epoch = current.selectionEpoch + 1;
      set({
        ...patchScope(current, {
          activeId: ideaId,
          selected: null,
          revisions: [],
          revisionsCursor: null,
          lastTrashed: scope.lastTrashed,
        }),
        selectionEpoch: epoch,
        reading: true,
        busy: false,
        error: null,
        notice: null,
        preview: null,
        previewLoading: false,
        previewEpoch: current.previewEpoch + 1,
        revisionsLoading: false,
      });
      try {
        const [view, revisions] = await Promise.all([
          getIdea(workspaceId, ideaId),
          listIdeaRevisions(workspaceId, ideaId),
        ]);
        if (get().session !== session || get().selectionEpoch !== epoch) return;
        const latest = scopeOf(get(), workspaceId);
        const editing = latest.editing[ideaId]
          ? latest.editing
          : { ...latest.editing, [ideaId]: freshDraft(workspaceId, view) };
        set({
          ...patchScope(get(), {
            selected: view,
            revisions: revisions.items,
            revisionsCursor: revisions.next_cursor,
            editing,
          }),
          reading: false,
        });
      } catch (error) {
        if (get().session !== session || get().selectionEpoch !== epoch) return;
        set({ reading: false, error: describe(error) });
      }
    },

    clearSelection() {
      const state = get();
      set({
        ...patchScope(state, { activeId: null, selected: null, revisions: [], revisionsCursor: null }),
        selectionEpoch: state.selectionEpoch + 1,
        preview: null,
        previewLoading: false,
      });
    },

    setCaptureBody(body) {
      const state = get();
      const scope = scopeOf(state);
      set(
        patchScope(state, {
          capture: { ...scope.capture, body, editSequence: scope.capture.editSequence + 1 },
        }),
      );
    },

    capture() {
      const { workspaceId } = get();
      if (!workspaceId) return Promise.resolve<SaveResult>({ status: "failed", error: "先打开作品目录。" });
      return get().saveExact(workspaceId, "capture");
    },

    setEntryBody(ideaId, body) {
      const state = get();
      const scope = scopeOf(state);
      const draft = scope.editing[ideaId];
      if (!draft) return;
      set(
        patchScope(state, {
          editing: {
            ...scope.editing,
            [ideaId]: { ...draft, body, editSequence: draft.editSequence + 1 },
          },
        }),
      );
    },

    saveEntry(ideaId) {
      const { workspaceId } = get();
      if (!workspaceId) return Promise.resolve<SaveResult>({ status: "failed", error: "先打开作品目录。" });
      return get().saveExact(workspaceId, ideaId);
    },

    saveExact(workspaceId, draftKey) {
      const key = `${workspaceId}:${draftKey}`;
      const running = inflight.get(key);
      if (running) return running;
      const promise = runSave(workspaceId, draftKey).finally(() => inflight.delete(key));
      inflight.set(key, promise);
      return promise;
    },

    async prepareReference(ideaId) {
      const { workspaceId } = get();
      if (!workspaceId) return null;
      const scope = scopeOf(get(), workspaceId);
      const draft = scope.editing[ideaId];
      if (draft && draftDirty(draft)) {
        const result = await get().saveExact(workspaceId, ideaId);
        if (result.status === "failed") return null;
        return result.value;
      }
      if (draft?.base) {
        return {
          workspaceId,
          ideaId,
          revisionId: draft.base.revisionId,
          digest: draft.base.digest,
          body: draft.base.body,
        };
      }
      const view = scope.selected;
      if (view && view.idea_id === ideaId) {
        return {
          workspaceId,
          ideaId,
          revisionId: view.revision_id,
          digest: view.digest,
          body: view.body,
        };
      }
      return null;
    },

    discardEntry(ideaId) {
      const state = get();
      const scope = scopeOf(state);
      const draft = scope.editing[ideaId];
      if (!draft) return;
      if (!draft.base) {
        const editing = { ...scope.editing };
        delete editing[ideaId];
        set(patchScope(state, { editing }));
        return;
      }
      set(
        patchScope(state, {
          editing: {
            ...scope.editing,
            [ideaId]: { ...draft, body: draft.base.body, editSequence: draft.editSequence + 1 },
          },
        }),
      );
    },

    async trashEntry(ideaId) {
      const state = get();
      const { workspaceId, session } = state;
      if (!workspaceId || state.busy) return;
      const scope = scopeOf(state, workspaceId);
      const view = scope.selected;
      if (!view || view.idea_id !== ideaId || view.deleted) return;
      const draft = scope.editing[ideaId];
      if (draft && draftDirty(draft)) {
        set({ notice: "当前草稿还没保存；先保存或放弃修改，再删除灵感。" });
        return;
      }
      const address = `${workspaceId}:${ideaId}:trash`;
      const signature = JSON.stringify([view.digest, view.revision_id]);
      const command = commandFor(address, signature, "idea-trash");
      set({ busy: true, error: null, notice: null });
      try {
        const next = await trashIdea(workspaceId, ideaId, {
          expected_digest: view.digest,
          expected_revision: view.revision_id,
          request_key: command.requestKey,
        });
        if (get().session !== session) return;
        const latest = scopeOf(get(), workspaceId);
        const editing = { ...latest.editing };
        delete editing[ideaId];
        set({
          ...patchScope(get(), { selected: next, editing, lastTrashed: ideaId }),
          busy: false,
          notice: "已移到最近删除。",
        });
        const revisions = await listIdeaRevisions(workspaceId, ideaId);
        if (get().session === session) {
          set(patchScope(get(), { revisions: revisions.items, revisionsCursor: revisions.next_cursor }));
        }
        await get().refresh();
      } catch (error) {
        if (get().session !== session) return;
        set({ busy: false, error: describe(error) });
      }
    },

    async restoreEntry(ideaId) {
      const state = get();
      const { workspaceId, session } = state;
      if (!workspaceId || state.busy) return;
      const scope = scopeOf(state, workspaceId);
      const view = scope.selected;
      if (!view || view.idea_id !== ideaId || !view.deleted) return;
      const draft = scope.editing[ideaId];
      if (draft && draftDirty(draft)) {
        set({ notice: "当前草稿还没保存；先保存或放弃修改，再恢复灵感。" });
        return;
      }
      set({ busy: true, error: null, notice: null });
      try {
        // 恢复「删除状态」的修订只会再生成一条删除修订；撤销删除要恢复最近一条未删除的历史。
        let history = await listIdeaRevisions(workspaceId, ideaId);
        let alive = history.items.find(item => !item.deleted);
        const seen = new Set<string>();
        while (!alive && history.next_cursor) {
          if (seen.has(history.next_cursor)) {
            throw new Error("[IDEA_CURSOR_INVALID] 灵感历史：分页游标重复。建议：重新读取历史。");
          }
          seen.add(history.next_cursor);
          history = await listIdeaRevisions(workspaceId, ideaId, { cursor: history.next_cursor });
          alive = history.items.find(item => !item.deleted);
        }
        if (!alive) {
          set({
            busy: false,
            error:
              "[IDEA_RESTORE_UNAVAILABLE] 灵感历史：只有删除修订，无法恢复正文。建议：核对历史后新建灵感。",
          });
          return;
        }
        const address = `${workspaceId}:${ideaId}:restore`;
        const signature = JSON.stringify([view.digest, alive.id]);
        const command = commandFor(address, signature, "idea-restore");
        const next = await restoreIdea(workspaceId, ideaId, {
          target_revision: alive.id,
          expected_digest: view.digest,
          request_key: command.requestKey,
        });
        if (get().session !== session) return;
        set({
          ...patchScope(get(), {
            selected: next,
            lastTrashed: null,
            editing: { ...scopeOf(get(), workspaceId).editing, [ideaId]: freshDraft(workspaceId, next) },
          }),
          busy: false,
          notice: "已恢复；恢复会生成一条新修订。",
        });
        const revisions = await listIdeaRevisions(workspaceId, ideaId);
        if (get().session === session) {
          set(patchScope(get(), { revisions: revisions.items, revisionsCursor: revisions.next_cursor }));
        }
        await get().refresh();
      } catch (error) {
        if (get().session !== session) return;
        set({ busy: false, error: describe(error) });
      }
    },

    async restoreRevision(revisionId) {
      const state = get();
      const { workspaceId, session } = state;
      if (!workspaceId || state.busy) return;
      const scope = scopeOf(state, workspaceId);
      const view = scope.selected;
      if (!view) return;
      const draft = scope.editing[view.idea_id];
      if (draft && draftDirty(draft)) {
        set({ notice: "当前草稿还没保存；先保存或放弃修改，再恢复历史修订。" });
        return;
      }
      set({ busy: true, error: null, notice: null });
      try {
        const address = `${workspaceId}:${view.idea_id}:restore`;
        const signature = JSON.stringify([view.digest, revisionId]);
        const command = commandFor(address, signature, "idea-history");
        const next = await restoreIdea(workspaceId, view.idea_id, {
          target_revision: revisionId,
          expected_digest: view.digest,
          request_key: command.requestKey,
        });
        if (get().session !== session) return;
        set({
          ...patchScope(get(), {
            selected: next,
            editing: {
              ...scopeOf(get(), workspaceId).editing,
              [view.idea_id]: freshDraft(workspaceId, next),
            },
          }),
          busy: false,
          notice: "已把这一版恢复为当前修订；后续修订仍保留。",
          preview: null,
          previewLoading: false,
          previewEpoch: get().previewEpoch + 1,
        });
        const revisions = await listIdeaRevisions(workspaceId, view.idea_id);
        if (get().session === session) {
          set(patchScope(get(), { revisions: revisions.items, revisionsCursor: revisions.next_cursor }));
        }
        await get().refresh();
      } catch (error) {
        if (get().session !== session) return;
        set({ busy: false, error: describe(error) });
      }
    },

    async undoTrash() {
      const { workspaceId } = get();
      if (!workspaceId) return;
      const scope = scopeOf(get(), workspaceId);
      const ideaId = scope.lastTrashed;
      if (!ideaId) return;
      if (scope.activeId !== ideaId) await get().select(ideaId);
      await get().restoreEntry(ideaId);
    },

    async loadMoreRevisions() {
      const { workspaceId, session, selectionEpoch, revisionsLoading } = get();
      if (!workspaceId || revisionsLoading) return;
      const scope = scopeOf(get(), workspaceId);
      const ideaId = scope.activeId;
      if (!ideaId || !scope.revisionsCursor) return;
      set({ revisionsLoading: true });
      try {
        const page = await listIdeaRevisions(workspaceId, ideaId, { cursor: scope.revisionsCursor });
        if (get().session !== session || get().selectionEpoch !== selectionEpoch) return;
        const current = scopeOf(get(), workspaceId);
        const merged = [...new Map([...current.revisions, ...page.items].map(item => [item.id, item])).values()];
        set(patchScope(get(), { revisions: merged, revisionsCursor: page.next_cursor }));
        set({ revisionsLoading: false });
      } catch (error) {
        if (get().session === session && get().selectionEpoch === selectionEpoch) {
          set({ revisionsLoading: false, error: describe(error) });
        }
      }
    },

    async previewRevision(revisionId) {
      const { workspaceId, session, selectionEpoch } = get();
      if (!workspaceId) return;
      const ideaId = scopeOf(get(), workspaceId).activeId;
      if (!ideaId) return;
      const epoch = get().previewEpoch + 1;
      set({ preview: null, previewLoading: true, previewEpoch: epoch });
      try {
        const preview = await getIdeaRevision(workspaceId, ideaId, revisionId);
        if (get().session !== session || get().selectionEpoch !== selectionEpoch) return;
        if (get().previewEpoch !== epoch) return;
        set({ preview, previewLoading: false });
      } catch (error) {
        if (get().session === session && get().selectionEpoch === selectionEpoch) {
          set({ previewLoading: false, error: describe(error) });
        }
      }
    },

    closePreview() {
      set({ preview: null, previewLoading: false, previewEpoch: get().previewEpoch + 1 });
    },

    clearNotice() {
      set({ notice: null, error: null });
    },
  };
});

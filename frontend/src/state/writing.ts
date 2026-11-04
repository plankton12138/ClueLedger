/**
 * 单一写作页的状态（1.0.9 P4）。
 *
 * 这里保存三件与页面生命周期无关的东西：服务端工作台投影、按章节分开的正文草稿、
 * 目标化候选。切换章节不会丢掉正在写的文字；草稿同时写入本地存储，刷新或网络中断后
 * 仍可恢复。保存一律以服务端摘要为前提，冲突时保留本地文字并等待作者选择。
 */

import { create } from "zustand";

import { errorMessage } from "../api/errors";
import { WorkspaceApiError } from "../api/workspace";
import { useWorkspaceStore } from "./workspace";
import {
  confirmIndexCandidate,
  readAssist,
  readWorkbench,
  renameChapter as renameChapterApi,
  renameUnplannedProse as renameUnplannedProseApi,
  reviewChapterIndex,
  saveChapterProse,
  startAssist,
  stopAssist,
  writingKey,
  type AssistInput,
  type CandidateStatus,
  type WritingWorkbenchView,
  type WorkbenchSelection,
} from "../api/writing";

const DRAFT_PREFIX = "hastings.writing.draft.";
const AUTOSAVE_DELAY = 1200;
const POLL_INTERVAL = 900;

export interface DraftConflict {
  serverText: string;
  serverDigest: string;
}

export interface WritingDraft {
  text: string;
  /** 最后一次服务端确认的文字与摘要；保存前用它比较。 */
  baseText: string;
  baseDigest: string;
  dirty: boolean;
  saving: boolean;
  error: string | null;
  conflict: DraftConflict | null;
  savedAt: string | null;
  /** 同一内容的网络重试复用同一个请求键。 */
  pendingKey: string | null;
  pendingText: string | null;
  /** 最近一次保存产生的修订；首次起草或内容未变时为空。 */
  lastRevisionId: string | null;
  /** 稿纸修订产生的草稿：只由「确认修改」入稿，不在加载或输入后自动保存。 */
  confirmRequired: boolean;
}

export interface AssistState {
  candidateId: string | null;
  status: CandidateStatus | "idle";
  text: string;
  error: string | null;
  stopping: boolean;
}

interface PersistedDraft {
  text: string;
  baseText: string;
  baseDigest: string;
  confirmRequired?: boolean;
}

interface WritingStore {
  workspaceId: string | null;
  selection: WorkbenchSelection;
  workbench: WritingWorkbenchView | null;
  loading: boolean;
  error: string | null;
  drafts: Record<string, WritingDraft>;
  assists: Record<string, AssistState>;

  load(workspaceId: string, selection?: WorkbenchSelection): Promise<void>;
  refresh(workspaceId?: string, selection?: WorkbenchSelection): Promise<void>;
  updateText(workspaceId: string, chapterId: string, text: string, schedule?: boolean, confirmRequired?: boolean): void;
  /** 修改章节名；返回错误文案，成功后刷新工作台。 */
  renameChapter(workspaceId: string, chapterId: string, title: string | null, expectedPlanRevision: string): Promise<string | null>;
  /** 修改未挂章节正文的首行标题；返回错误文案。 */
  renameProse(workspaceId: string, path: string, title: string, expectedDigest: string): Promise<string | null>;
  save(workspaceId: string, chapterId: string): Promise<boolean>;
  adoptServer(workspaceId: string, chapterId: string): Promise<void>;
  overrideWithLocal(workspaceId: string, chapterId: string): Promise<boolean>;
  discardLocal(workspaceId: string, chapterId: string): Promise<void>;
  flush(workspaceId: string): void;

  confirmCandidate(workspaceId: string, chapterId: string, candidateId: string): Promise<boolean>;
  reviewIndex(workspaceId: string, chapterId: string): Promise<boolean>;

  assistStart(workspaceId: string, key: string, input: AssistInput): Promise<void>;
  assistStop(workspaceId: string, key: string): Promise<void>;
  assistClear(key: string): void;
  /** 轮询收到候选后更新；只在状态文件内部使用。 */
  assistApply(key: string, candidate: { candidate_id: string; status: CandidateStatus; text: string; error: string | null }): void;
  assistApplyError(key: string): void;

  reset(): void;
}

const saveTimers: Record<string, ReturnType<typeof setTimeout>> = {};
const pollTimers: Record<string, ReturnType<typeof setInterval>> = {};
let loadToken = 0;

export function writingDraftKey(workspaceId: string, chapterId: string): string {
  return `${workspaceId}::${chapterId}`;
}

function storage(): Storage | null {
  try {
    return globalThis.localStorage ? globalThis.localStorage : null;
  } catch {
    return null;
  }
}

function readPersisted(workspaceId: string, chapterId: string): PersistedDraft | null {
  const store = storage();
  if (!store) return null;
  try {
    const raw = store.getItem(`${DRAFT_PREFIX}${workspaceId}.${chapterId}`);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<PersistedDraft> | null;
    if (!parsed || typeof parsed.text !== "string" || typeof parsed.baseDigest !== "string") return null;
    return {
      text: parsed.text,
      baseText: typeof parsed.baseText === "string" ? parsed.baseText : "",
      baseDigest: parsed.baseDigest,
      confirmRequired: parsed.confirmRequired === true,
    };
  } catch {
    return null;
  }
}

function writePersisted(workspaceId: string, chapterId: string, draft: WritingDraft): void {
  const store = storage();
  if (!store) return;
  try {
    store.setItem(
      `${DRAFT_PREFIX}${workspaceId}.${chapterId}`,
      JSON.stringify({
        text: draft.text,
        baseText: draft.baseText,
        baseDigest: draft.baseDigest,
        confirmRequired: draft.confirmRequired,
      } satisfies PersistedDraft),
    );
  } catch {
    // 存储不可写或配额满时只放弃刷新恢复能力，不影响本次编辑。
  }
}

function clearPersisted(workspaceId: string, chapterId: string): void {
  const store = storage();
  if (!store) return;
  try {
    store.removeItem(`${DRAFT_PREFIX}${workspaceId}.${chapterId}`);
  } catch {
    // 与写入相同：清理失败不改变正文状态。
  }
}

function seedDraft(
  previous: Record<string, WritingDraft>,
  workspaceId: string,
  chapterId: string,
  serverText: string,
  serverDigest: string,
): WritingDraft {
  const key = writingDraftKey(workspaceId, chapterId);
  const local = previous[key];
  const persisted = readPersisted(workspaceId, chapterId);
  if (local?.conflict) {
    return {
      ...local,
      baseText: serverText,
      baseDigest: serverDigest,
      conflict: { serverText, serverDigest },
    };
  }
  if (local?.dirty) {
    // 本地有未提交文字：服务端正文未变就继续自动保存；正文变了才进入冲突。
    if (local.baseDigest === serverDigest) return local;
    return {
      ...local,
      conflict: { serverText, serverDigest },
      error: null,
      pendingKey: null,
      pendingText: null,
      lastRevisionId: local.lastRevisionId,
    };
  }
  if (persisted && persisted.text !== serverText) {
    const conflict = persisted.baseDigest === serverDigest ? null : { serverText, serverDigest };
    return {
      text: persisted.text,
      baseText: conflict ? serverText : persisted.baseText,
      baseDigest: persisted.baseDigest || serverDigest,
      dirty: conflict === null,
      saving: false,
      error: null,
      conflict,
      savedAt: null,
      pendingKey: null,
      pendingText: null,
      lastRevisionId: local?.lastRevisionId ?? null,
      confirmRequired: persisted.confirmRequired === true,
    };
  }
  clearPersisted(workspaceId, chapterId);
  return {
    text: serverText,
    baseText: serverText,
    baseDigest: serverDigest,
    dirty: false,
    saving: false,
    error: null,
    conflict: null,
    savedAt: null,
    pendingKey: null,
    pendingText: null,
    lastRevisionId: null,
    confirmRequired: false,
  };
}

function scheduleSave(workspaceId: string, chapterId: string): void {
  const key = writingDraftKey(workspaceId, chapterId);
  if (saveTimers[key]) clearTimeout(saveTimers[key]);
  saveTimers[key] = setTimeout(() => {
    delete saveTimers[key];
    void useWritingStore.getState().save(workspaceId, chapterId);
  }, AUTOSAVE_DELAY);
}

function stopPoll(key: string): void {
  const timer = pollTimers[key];
  if (timer) clearInterval(timer);
  delete pollTimers[key];
}

function flushDrafts(workspaceId: string, drafts: Record<string, WritingDraft>): void {
  const prefix = `${workspaceId}::`;
  for (const key of Object.keys(drafts)) {
    const draft = drafts[key];
    if (key.startsWith(prefix) && draft?.dirty) void useWritingStore.getState().save(workspaceId, key.slice(prefix.length));
  }
}

export const useWritingStore = create<WritingStore>()((set, get) => ({
  workspaceId: null,
  selection: {},
  workbench: null,
  loading: false,
  error: null,
  drafts: {},
  assists: {},

  async load(workspaceId, selection = {}) {
    const token = ++loadToken;
    const previousWorkspace = get().workspaceId;
    if (previousWorkspace && previousWorkspace !== workspaceId) flushDrafts(previousWorkspace, get().drafts);
    set({
      workspaceId,
      selection,
      loading: true,
      error: null,
      ...(previousWorkspace !== workspaceId ? { assists: {} } : {}),
    });
    try {
      const view = await readWorkbench(workspaceId, selection);
      if (token !== loadToken || get().workspaceId !== workspaceId) return;
      const drafts = { ...get().drafts };
      const chapter = view.chapter;
      if (chapter?.plan && chapter.prose) {
        const key = writingDraftKey(workspaceId, chapter.plan.id);
        drafts[key] = seedDraft(
          drafts,
          workspaceId,
          chapter.plan.id,
          chapter.prose.text,
          chapter.prose.digest,
        );
      }
      set({
        workbench: view,
        loading: false,
        error: null,
        drafts,
      });
      // 从本地存储恢复的未保存文字继续自动保存；冲突草稿等作者决定。
      const prefix = `${workspaceId}::`;
      for (const [key, draft] of Object.entries(get().drafts)) {
        // 稿纸修订只由作者点「确认修改」入稿；其余未保存草稿继续自动保存。
        if (key.startsWith(prefix) && draft.dirty && !draft.conflict && !draft.confirmRequired) {
          scheduleSave(workspaceId, key.slice(prefix.length));
        }
      }
    } catch (error) {
      if (token !== loadToken || get().workspaceId !== workspaceId) return;
      set({ loading: false, error: errorMessage(error, "写作工作台") });
    }
  },

  async refresh(workspaceId, selection) {
    const id = workspaceId ?? get().workspaceId;
    if (!id) return;
    await get().load(id, selection ?? get().selection);
  },

  updateText(workspaceId, chapterId, text, schedule = true, confirmRequired = false) {
    const key = writingDraftKey(workspaceId, chapterId);
    const current = get().drafts[key];
    if (!current) return;
    const dirty = text !== current.baseText;
    const next: WritingDraft = {
      ...current,
      text,
      dirty,
      error: null,
      confirmRequired: dirty && (current.confirmRequired || confirmRequired),
      // 新文字必须重新取请求键；同一文字的失败重试才复用旧键。
      ...(text === current.pendingText ? {} : { pendingKey: null, pendingText: null }),
    };
    set(state => ({ drafts: { ...state.drafts, [key]: next } }));
    if (next.dirty) {
      writePersisted(workspaceId, chapterId, next);
      // 中文输入法组合期间先不排队保存；组合结束后再按同一次输入处理。
      if (schedule) scheduleSave(workspaceId, chapterId);
    } else {
      if (saveTimers[key]) clearTimeout(saveTimers[key]);
      delete saveTimers[key];
      clearPersisted(workspaceId, chapterId);
    }
  },

  async renameChapter(workspaceId, chapterId, title, expectedPlanRevision) {
    const key = writingDraftKey(workspaceId, chapterId);
    const draft = get().drafts[key];
    if (draft?.conflict) return "章节：正文有未处理的版本冲突。建议：先处理正文冲突再改名。";
    if (draft?.dirty) {
      const saved = await get().save(workspaceId, chapterId);
      if (!saved) return "章节：正文还有未保存的修改。建议：先处理正文冲突再改名。";
    }
    try {
      await renameChapterApi(workspaceId, chapterId, {
        title: title?.trim() || null,
        expected_plan_revision: expectedPlanRevision,
        request_key: writingKey(`chapter-rename-${chapterId}`),
      });
      await get().refresh(workspaceId);
      return null;
    } catch (error) {
      return errorMessage(error, `章节/${chapterId}`);
    }
  },

  async renameProse(workspaceId, path, title, expectedDigest) {
    try {
      await renameUnplannedProseApi(workspaceId, {
        path,
        title: title.trim(),
        expected_digest: expectedDigest,
      });
      await Promise.all([
        get().refresh(workspaceId),
        useWorkspaceStore.getState().refreshCatalog(),
      ]);
      const workspace = useWorkspaceStore.getState();
      if (workspace.currentId === workspaceId && workspace.selectedPath === path) {
        await workspace.openPath(path);
      }
      return null;
    } catch (error) {
      return errorMessage(error, path);
    }
  },

  async save(workspaceId, chapterId) {
    const key = writingDraftKey(workspaceId, chapterId);
    const draft = get().drafts[key];
    if (!draft || draft.saving || !draft.dirty || draft.conflict) return false;
    const requestKey = draft.pendingKey && draft.pendingText === draft.text
      ? draft.pendingKey
      : writingKey(`prose-${chapterId}`);
    const sent = draft.text;
    set(state => ({
      drafts: {
        ...state.drafts,
        [key]: {
          ...state.drafts[key]!,
          saving: true,
          error: null,
          pendingKey: requestKey,
          pendingText: sent,
        },
      },
    }));
    try {
      const result = await saveChapterProse(workspaceId, chapterId, {
        text: sent,
        expected_digest: draft.baseDigest,
        draft_revision: requestKey,
        request_key: requestKey,
      });
      let followUp = false;
      set(state => {
        const current = state.drafts[key];
        if (!current) return {};
        const typedMore = current.text !== sent;
        followUp = typedMore;
        const nextDraft: WritingDraft = {
          ...current,
          baseText: sent,
          baseDigest: result.prose_digest,
          dirty: typedMore,
          saving: false,
          error: null,
          conflict: null,
          savedAt: new Date().toISOString(),
          pendingKey: null,
          pendingText: null,
          lastRevisionId: result.revision_id ?? current.lastRevisionId,
          confirmRequired: false,
        };
        const currentView = state.workbench;
        const workbench = currentView?.workspace_id === workspaceId
          ? {
              ...currentView,
              plan_revision: result.plan_revision,
              chapter: currentView.chapter?.plan?.id === chapterId
                ? {
                    ...currentView.chapter,
                    plan: currentView.chapter.plan
                      ? { ...currentView.chapter.plan, prose_path: result.path }
                      : null,
                    prose: {
                      ...currentView.chapter.prose!,
                      path: result.path,
                      text: sent,
                      digest: result.prose_digest,
                      editable: false as const,
                    },
                    index: result.index,
                  }
                : currentView.chapter,
            }
          : currentView;
        return { drafts: { ...state.drafts, [key]: nextDraft }, workbench };
      });
      if (result.revision_id && typeof window !== "undefined") {
        window.dispatchEvent(
          new CustomEvent("hastings:revision-changed", { detail: { chapterId } }),
        );
      }
      const latest = get().drafts[key];
      if (latest?.dirty) {
        writePersisted(workspaceId, chapterId, latest);
        if (followUp) scheduleSave(workspaceId, chapterId);
      } else clearPersisted(workspaceId, chapterId);
      return true;
    } catch (error) {
      if (error instanceof WorkspaceApiError && error.code === "PROSE_DIGEST_MISMATCH") {
        const serverText = String(error.details.current_text ?? "");
        const serverDigest = String(error.details.current_digest ?? "");
        set(state => ({
          drafts: {
            ...state.drafts,
            [key]: {
              ...state.drafts[key]!,
              saving: false,
              error: null,
              pendingKey: null,
              pendingText: null,
              conflict: { serverText, serverDigest },
            },
          },
        }));
        return false;
      }
      set(state => ({
        drafts: {
          ...state.drafts,
          [key]: {
            ...state.drafts[key]!,
            saving: false,
            error: errorMessage(error, `章节/${chapterId}`),
            pendingKey: requestKey,
            pendingText: sent,
          },
        },
      }));
      return false;
    }
  },

  async adoptServer(workspaceId, chapterId) {
    const key = writingDraftKey(workspaceId, chapterId);
    const draft = get().drafts[key];
    if (!draft?.conflict) return;
    set(state => ({
      drafts: {
        ...state.drafts,
        [key]: {
          ...state.drafts[key]!,
          text: draft.conflict!.serverText,
          baseText: draft.conflict!.serverText,
          baseDigest: draft.conflict!.serverDigest,
          dirty: false,
          saving: false,
          error: null,
          conflict: null,
          pendingKey: null,
          pendingText: null,
          confirmRequired: false,
        },
      },
    }));
    clearPersisted(workspaceId, chapterId);
    await get().refresh(workspaceId, { chapterId });
  },

  async overrideWithLocal(workspaceId, chapterId) {
    const key = writingDraftKey(workspaceId, chapterId);
    const draft = get().drafts[key];
    if (!draft?.conflict) return false;
    set(state => ({
      drafts: {
        ...state.drafts,
        [key]: {
          ...state.drafts[key]!,
          baseText: draft.conflict!.serverText,
          baseDigest: draft.conflict!.serverDigest,
          dirty: true,
          error: null,
          conflict: null,
          pendingKey: null,
          pendingText: null,
          confirmRequired: true,
        },
      },
    }));
    const latest = get().drafts[key];
    if (latest) writePersisted(workspaceId, chapterId, latest);
    return get().save(workspaceId, chapterId);
  },

  async discardLocal(workspaceId, chapterId) {
    const key = writingDraftKey(workspaceId, chapterId);
    if (saveTimers[key]) clearTimeout(saveTimers[key]);
    delete saveTimers[key];
    clearPersisted(workspaceId, chapterId);
    set(state => {
      const drafts = { ...state.drafts };
      delete drafts[key];
      return { drafts };
    });
    await get().refresh(workspaceId, { chapterId });
  },

  async confirmCandidate(workspaceId, chapterId, candidateId) {
    const view = get().workbench;
    const chapter = view?.workspace_id === workspaceId && view.chapter?.plan?.id === chapterId ? view.chapter : null;
    if (!chapter?.prose) return false;
    try {
      await confirmIndexCandidate(workspaceId, candidateId, {
        chapter_id: chapterId,
        expected_prose_digest: chapter.prose.digest,
        request_key: writingKey(`index-confirm-${candidateId}`),
      });
      await get().refresh(workspaceId, { chapterId });
      return true;
    } catch (error) {
      useWorkspaceStore.getState().reportError(errorMessage(error, "确认入账"));
      return false;
    }
  },

  async reviewIndex(workspaceId, chapterId) {
    try {
      await reviewChapterIndex(workspaceId, chapterId);
      await get().refresh(workspaceId, { chapterId });
      return true;
    } catch (error) {
      useWorkspaceStore.getState().reportError(errorMessage(error, "让模型看看本章"));
      return false;
    }
  },

  flush(workspaceId) {
    flushDrafts(workspaceId, get().drafts);
  },

  async assistStart(workspaceId, key, input) {
    const existing = get().assists[key];
    if (existing?.status === "streaming") return;
    set(state => ({
      assists: {
        ...state.assists,
        [key]: { candidateId: null, status: "streaming", text: "", error: null, stopping: false },
      },
    }));
    try {
      const candidate = await startAssist(workspaceId, input);
      set(state => ({
        assists: {
          ...state.assists,
          [key]: {
            candidateId: candidate.candidate_id,
            status: candidate.status,
            text: candidate.text,
            error: candidate.error,
            stopping: false,
          },
        },
      }));
      if (candidate.status === "streaming") pollCandidate(workspaceId, key, candidate.candidate_id);
    } catch (error) {
      set(state => ({
        assists: {
          ...state.assists,
          [key]: {
            candidateId: null,
            status: "failed",
            text: "",
            error: errorMessage(error, "模型辅助"),
            stopping: false,
          },
        },
      }));
    }
  },

  async assistStop(workspaceId, key) {
    const state = get().assists[key];
    if (!state?.candidateId) return;
    stopPoll(key);
    set(current => ({
      assists: {
        ...current.assists,
        [key]: { ...current.assists[key]!, stopping: true },
      },
    }));
    try {
      const candidate = await stopAssist(workspaceId, state.candidateId);
      set(current => ({
        assists: {
          ...current.assists,
          [key]: {
            candidateId: candidate.candidate_id,
            status: candidate.status,
            text: candidate.text,
            error: candidate.error,
            stopping: false,
          },
        },
      }));
    } catch (error) {
      set(current => ({
        assists: {
          ...current.assists,
          [key]: {
            ...current.assists[key]!,
            stopping: false,
            error: errorMessage(error, "停止生成"),
          },
        },
      }));
    }
  },

  assistClear(key) {
    stopPoll(key);
    set(state => {
      const assists = { ...state.assists };
      delete assists[key];
      return { assists };
    });
  },

  assistApply(key, candidate) {
    set(state => ({
      assists: {
        ...state.assists,
        [key]: {
          candidateId: candidate.candidate_id,
          status: candidate.status,
          text: candidate.text,
          error: candidate.error,
          stopping: false,
        },
      },
    }));
  },

  assistApplyError(key) {
    set(state => ({
      assists: {
        ...state.assists,
        [key]: {
          ...state.assists[key],
          status: "failed",
          error: "读取候选状态失败；保留当前文字，稍后重试或重新生成。",
        },
      },
    }));
  },

  reset() {
    loadToken += 1;
    for (const timer of Object.values(saveTimers)) clearTimeout(timer);
    for (const key of Object.keys(saveTimers)) delete saveTimers[key];
    for (const key of Object.keys(pollTimers)) stopPoll(key);
    set({ workspaceId: null, selection: {}, workbench: null, loading: false, error: null, drafts: {}, assists: {} });
  },
}));

function pollCandidate(workspaceId: string, key: string, candidateId: string): void {
  stopPoll(key);
  pollTimers[key] = setInterval(() => {
    void (async () => {
      try {
        const candidate = await readAssist(workspaceId, candidateId);
        const state = useWritingStore.getState();
        if (!state.assists[key] || state.workspaceId !== workspaceId) {
          stopPoll(key);
          return;
        }
        state.assistApply(key, candidate);
        if (candidate.status !== "streaming") stopPoll(key);
      } catch {
        const state = useWritingStore.getState();
        if (state.assists[key]) state.assistApplyError(key);
        stopPoll(key);
      }
    })();
  }, POLL_INTERVAL);
}

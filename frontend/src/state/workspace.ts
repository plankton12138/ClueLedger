/**
 * 文件工作区状态（0.4 V04-4）。
 *
 * 界面只读这里的状态：目录、两块内容、当前文件、对话事件、Git 状态与抽屉。
 * 所有变更都经 `api/workspace.ts` 的真实请求；没有「只在前端成立」的能力。
 *
 * 一轮对话用事件流推进：先 POST 启动，再按游标读 SSE，收到 `run_finished` 后
 * 刷新目录、当前文件与 Git 状态——文件是磁盘上的事实，不是前端拼接的结果。
 */

import { create } from "zustand";

import {
  conversationMessages,
  createConversation,
  gitCommitDiff,
  gitDiff,
  gitInit,
  gitLog,
  gitRecord,
  gitRevert,
  gitStatus,
  importManuscript,
  listConversations,
  listFiles,
  listWorkspaces,
  openWorkspace,
  readFile,
  searchText,
  startTurn,
  stopTurn,
  streamEvents,
  WorkspaceApiError,
  type Catalog,
  type Conversation,
  type FileView,
  type GitCommit,
  type GitStatus,
  type RunResult,
  type SearchResult,
  type WorkspaceBinding,
  type WorkspaceEvent,
} from "../api/workspace";

const EMPTY_CATALOG: Catalog = { chapters: [], settings: [] };

interface WorkspaceStore {
  bindings: WorkspaceBinding[];
  currentId: string | null;
  catalog: Catalog;
  file: FileView | null;
  selectedPath: string | null;
  search: SearchResult | null;
  loading: boolean;
  error: string | null;
  notice: string | null;

  conversations: Conversation[];
  conversationId: string | null;
  events: WorkspaceEvent[];
  running: boolean;
  activeTurn: string | null;
  lastResult: RunResult | null;

  git: GitStatus | null;
  diff: string;
  log: GitCommit[];
  drawerOpen: boolean;
  selectedCommit: GitCommit | null;
  commitDiff: string;

  init(): Promise<void>;
  open(path: string, create: boolean): Promise<void>;
  selectWorkspace(id: string): Promise<void>;
  refreshAll(): Promise<void>;
  refreshGit(): Promise<void>;
  openPath(path: string): Promise<void>;
  runSearch(query: string): Promise<void>;
  clearSearch(): void;
  upload(file: File): Promise<void>;

  newConversation(): Promise<void>;
  selectConversation(id: string): Promise<void>;
  send(text: string): Promise<void>;
  stop(): Promise<void>;

  openDrawer(): Promise<void>;
  closeDrawer(): void;
  initGit(): Promise<void>;
  recordChanges(): Promise<void>;
  revert(commit: string): Promise<void>;
  showCommit(commit: GitCommit): Promise<void>;
  dismissError(): void;
  dismissNotice(): void;
}

let abortStream: AbortController | null = null;
let initializing = false;

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) {
    return `${error.why}。${error.fix}`;
  }
  return error instanceof Error ? error.message : "请求失败";
}

function mergeEvent(events: WorkspaceEvent[], event: WorkspaceEvent): WorkspaceEvent[] {
  if (events.some((item) => item.seq === event.seq)) return events;
  return [...events, event].sort((left, right) => left.seq - right.seq);
}

function lastSeq(events: WorkspaceEvent[]): number {
  return events.length ? events[events.length - 1]!.seq : 0;
}

function resultFrom(event: WorkspaceEvent): RunResult {
  return {
    status: event.status ?? "completed",
    recording: event.recording ?? "no_change",
    changed_paths: event.changed_paths ?? [],
    commit: event.commit ?? null,
    note: event.note ?? "",
  };
}

export const useWorkspaceStore = create<WorkspaceStore>()((set, get) => ({
  bindings: [],
  currentId: null,
  catalog: EMPTY_CATALOG,
  file: null,
  selectedPath: null,
  search: null,
  loading: false,
  error: null,
  notice: null,

  conversations: [],
  conversationId: null,
  events: [],
  running: false,
  activeTurn: null,
  lastResult: null,

  git: null,
  diff: "",
  log: [],
  drawerOpen: false,
  selectedCommit: null,
  commitDiff: "",

  async init() {
    if (initializing) return;
    initializing = true;
    try {
      const bindings = await listWorkspaces();
      set({ bindings });
      const current = bindings.find((item) => item.exists);
      if (current) await get().selectWorkspace(current.id);
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      initializing = false;
    }
  },

  async open(path, create) {
    set({ loading: true, error: null });
    try {
      const binding = await openWorkspace(path, create);
      set({ bindings: await listWorkspaces() });
      await get().selectWorkspace(binding.id);
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ loading: false });
    }
  },

  async selectWorkspace(id) {
    abortStream?.abort();
    abortStream = null;
    set({
      currentId: id,
      catalog: EMPTY_CATALOG,
      file: null,
      selectedPath: null,
      search: null,
      conversations: [],
      conversationId: null,
      events: [],
      running: false,
      activeTurn: null,
      lastResult: null,
      git: null,
      diff: "",
      log: [],
      selectedCommit: null,
      commitDiff: "",
      error: null,
      notice: null,
    });
    await get().refreshAll();
  },

  async refreshAll() {
    const id = get().currentId;
    if (!id) return;
    try {
      const [catalog, conversations, git] = await Promise.all([
        listFiles(id),
        listConversations(id),
        gitStatus(id),
      ]);
      set({ catalog, conversations, git });
      // 外部编辑后回读当前文件；文件已不存在时清空选择。
      const path = get().selectedPath;
      if (path) {
        const stillThere = [...catalog.chapters, ...catalog.settings].some(
          (entry) => entry.path === path && entry.exists,
        );
        if (stillThere) await get().openPath(path);
        else set({ selectedPath: null, file: null });
      }
      if (!get().conversationId && conversations.length) {
        await get().selectConversation(conversations[0]!.id);
      }
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async refreshGit() {
    const id = get().currentId;
    if (!id) return;
    try {
      const [git, diff, log] = await Promise.all([gitStatus(id), gitDiff(id), gitLog(id)]);
      set({ git, diff: diff.diff, log: log.commits });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async openPath(path) {
    const id = get().currentId;
    if (!id) return;
    try {
      const file = await readFile(id, path);
      set({ file, selectedPath: path, error: null });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async runSearch(query) {
    const id = get().currentId;
    if (!id || !query.trim()) return;
    try {
      set({ search: await searchText(id, query.trim()) });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  clearSearch() {
    set({ search: null });
  },

  async upload(file) {
    const id = get().currentId;
    if (!id) return;
    set({ loading: true, error: null });
    try {
      const catalog = await importManuscript(id, file);
      set({ catalog, notice: `已导入 ${file.name}` });
      await get().refreshAll();
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      set({ loading: false });
    }
  },

  async newConversation() {
    const id = get().currentId;
    if (!id) return;
    try {
      const conversation = await createConversation(id);
      set((state) => ({
        conversations: [conversation, ...state.conversations],
        conversationId: conversation.id,
        events: [],
        lastResult: null,
      }));
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async selectConversation(id) {
    const workspaceId = get().currentId;
    if (!workspaceId) return;
    abortStream?.abort();
    abortStream = null;
    try {
      const { events } = await conversationMessages(workspaceId, id, 0);
      const finished = [...events].reverse().find((event) => event.type === "run_finished");
      set({
        conversationId: id,
        events,
        lastResult: finished ? resultFrom(finished) : null,
      });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async send(text) {
    const id = get().currentId;
    const trimmed = text.trim();
    if (!id || !trimmed || get().running) return;
    set({ error: null, notice: null });
    try {
      let conversationId = get().conversationId;
      if (!conversationId) {
        const conversation = await createConversation(id);
        conversationId = conversation.id;
        set((state) => ({ conversations: [conversation, ...state.conversations] }));
      }
      const after = lastSeq(get().events);
      const turn = await startTurn(id, conversationId, trimmed, `ui-${Date.now()}`);
      set({ running: true, activeTurn: turn.turn_id, conversationId });
      await consume(id, conversationId, after, set, get);
    } catch (error) {
      set({ running: false, activeTurn: null, error: describe(error) });
    }
  },

  async stop() {
    const id = get().currentId;
    const turn = get().activeTurn;
    if (!id || !turn) return;
    try {
      await stopTurn(id, turn);
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async openDrawer() {
    set({ drawerOpen: true });
    await get().refreshGit();
  },

  closeDrawer() {
    set({ drawerOpen: false, selectedCommit: null, commitDiff: "" });
  },

  async initGit() {
    const id = get().currentId;
    if (!id) return;
    try {
      const git = await gitInit(id);
      set({ git, notice: "已启用版本记录" });
      await get().refreshGit();
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async recordChanges() {
    const id = get().currentId;
    if (!id) return;
    try {
      const { commit } = await gitRecord(id);
      set({ notice: commit ? "已记录本次改动" : "没有需要记录的改动" });
      await get().refreshGit();
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async revert(commit) {
    const id = get().currentId;
    if (!id) return;
    try {
      await gitRevert(id, commit);
      set({ notice: "已撤销这次修改", selectedCommit: null, commitDiff: "" });
      await get().refreshGit();
      await get().refreshAll();
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async showCommit(commit) {
    const id = get().currentId;
    if (!id) return;
    try {
      const { diff } = await gitCommitDiff(id, commit.commit);
      set({ selectedCommit: commit, commitDiff: diff });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  dismissError() {
    set({ error: null });
  },

  dismissNotice() {
    set({ notice: null });
  },
}));

async function consume(
  workspaceId: string,
  conversationId: string,
  after: number,
  set: (partial: Partial<WorkspaceStore>) => void,
  get: () => WorkspaceStore,
): Promise<void> {
  const controller = new AbortController();
  abortStream = controller;
  try {
    for await (const event of streamEvents(workspaceId, conversationId, after, controller.signal)) {
      set({ events: mergeEvent(get().events, event) });
      if (event.type === "run_finished") {
        set({ lastResult: resultFrom(event) });
        break;
      }
    }
  } catch (error) {
    if (!controller.signal.aborted) set({ error: describe(error) });
  } finally {
    if (abortStream === controller) abortStream = null;
    set({ running: false, activeTurn: null });
    await get().refreshAll();
  }
}

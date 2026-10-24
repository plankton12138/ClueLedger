/**
 * 文件工作区状态（0.4 V04-4）。
 *
 * 这里只管作品本身：目录、两块内容、当前文件、Git 状态与抽屉。对话与会话是另一份
 * 状态（`state/conversations.ts`）：一个作品可以有多个会话，各自的事件、运行轮次与
 * 草稿都不该挤进文件阅读状态里。
 *
 * 一轮对话结束后回调这里回读目录与 Git——文件是磁盘上的事实，不是前端拼接的结果。
 */

import { create } from "zustand";

import {
  gitCommitDiff,
  gitDiff,
  gitInit,
  gitLog,
  gitRecord,
  gitRevert,
  gitStatus,
  importManuscript,
  listFiles,
  listWorkspaces,
  openWorkspace,
  pruneStaleWorkspaces,
  readFile,
  searchText,
  WorkspaceApiError,
  type Catalog,
  type FileView,
  type GitCommit,
  type GitStatus,
  type SearchResult,
  type WorkspaceBinding,
} from "../api/workspace";
import { registerRunFinished, useConversationsStore } from "./conversations";

const EMPTY_CATALOG: Catalog = { chapters: [], characters: [], settings: [] };

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

  git: GitStatus | null;
  diff: string;
  log: GitCommit[];
  drawerOpen: boolean;
  selectedCommit: GitCommit | null;
  commitDiff: string;

  init(): Promise<void>;
  open(path: string, create: boolean): Promise<void>;
  selectWorkspace(id: string): Promise<void>;
  refreshBindings(): Promise<void>;
  pruneMissing(): Promise<void>;
  refreshAll(): Promise<void>;
  refreshGit(): Promise<void>;
  openPath(path: string): Promise<void>;
  runSearch(query: string): Promise<void>;
  clearSearch(): void;
  upload(file: File): Promise<void>;

  openDrawer(): Promise<void>;
  closeDrawer(): void;
  initGit(): Promise<void>;
  recordChanges(): Promise<void>;
  revert(commit: string): Promise<void>;
  showCommit(commit: GitCommit): Promise<void>;
  reportError(message: string): void;
  dismissError(): void;
  dismissNotice(): void;
}

let initializing = false;

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) {
    return `${error.why}。${error.fix}`;
  }
  return error instanceof Error ? error.message : "请求失败";
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

  async refreshBindings() {
    // 派生或新建之后，登记列表会多一条；只刷新列表，不改变当前作品。
    try {
      set({ bindings: await listWorkspaces() });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async selectWorkspace(id) {
    set({
      currentId: id,
      catalog: EMPTY_CATALOG,
      file: null,
      selectedPath: null,
      search: null,
      git: null,
      diff: "",
      log: [],
      selectedCommit: null,
      commitDiff: "",
      error: null,
      notice: null,
    });
    // 会话按作品各自加载：切作品不把上一部作品的会话带过来。
    await useConversationsStore.getState().load(id);
    await get().refreshAll();
  },

  async pruneMissing() {
    try {
      const result = await pruneStaleWorkspaces();
      set({ bindings: result.workspaces, error: null });
      const currentId = get().currentId;
      if (currentId && !result.workspaces.some((item) => item.id === currentId)) {
        const next = result.workspaces.find((item) => item.exists);
        if (next) await get().selectWorkspace(next.id);
        else {
          set({
            currentId: null,
            catalog: EMPTY_CATALOG,
            file: null,
            selectedPath: null,
            search: null,
            git: null,
            diff: "",
            log: [],
            selectedCommit: null,
            commitDiff: "",
          });
          useConversationsStore.getState().reset();
        }
      }
      set({ notice: `已清理 ${result.removed.length} 个目录已不存在的作品。` });
    } catch (error) {
      set({ error: describe(error) });
    }
  },

  async refreshAll() {
    const id = get().currentId;
    if (!id) return;
    try {
      const [catalog, git] = await Promise.all([listFiles(id), gitStatus(id)]);
      set({ catalog, git });
      // 外部编辑后回读当前文件；文件已不存在时清空选择。
      const path = get().selectedPath;
      if (path) {
        const stillThere = [...catalog.chapters, ...catalog.characters, ...catalog.settings].some(
          (entry) => entry.path === path && entry.exists,
        );
        if (stillThere) await get().openPath(path);
        else set({ selectedPath: null, file: null });
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

  reportError(message) {
    set({ error: message });
  },

  dismissError() {
    set({ error: null });
  },

  dismissNotice() {
    set({ notice: null });
  },
}));

// 一轮结束后回读磁盘：目录、当前文件与 Git 都可能已经变了。
registerRunFinished(() => {
  void useWorkspaceStore.getState().refreshAll();
  void useWorkspaceStore.getState().refreshGit();
});

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
  removeWorkspace,
  renameWorkspace,
  searchText,
  type Catalog,
  type FileView,
  type GitCommit,
  type GitStatus,
  type SearchResult,
  type WorkspaceBinding,
} from "../api/workspace";
import { errorMessage } from "../api/errors";
import { registerModuleChanges, registerRunFinished, useConversationsStore } from "./conversations";
import { useWorkspaceModulesStore } from "./workspaceModules";
import { useModuleLibraryStore } from "./moduleLibrary";

const EMPTY_CATALOG: Catalog = { chapters: [], characters: [], settings: [] };

interface WorkspaceStore {
  ready: boolean;
  positions: Record<string, { path: string | null; search: SearchResult | null }>;
  workspaceEpoch: number;
  fileEpoch: number;
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

  init(preferredId?: string | null): Promise<void>;
  /** 打开或新建作品并切过去；成功返回作品标识，失败返回 null。 */
  open(path: string, create: boolean): Promise<string | null>;
  selectWorkspace(id: string): Promise<void>;
  /** 明确确认完整路径后删除作品文件与应用记录。 */
  remove(id: string, confirmedPath: string): Promise<boolean>;
  /** 重命名作品目录并换绑登记；成功返回新标识，失败返回 null。 */
  rename(id: string, name: string): Promise<string | null>;
  refreshBindings(): Promise<void>;
  pruneMissing(): Promise<void>;
  refreshAll(): Promise<void>;
  refreshGit(): Promise<void>;
  openPath(path: string, preserveModule?: boolean): Promise<void>;
  runSearch(query: string): Promise<void>;
  clearSearch(): void;
  upload(file: File): Promise<void>;

  openDrawer(): Promise<void>;
  closeDrawer(): void;
  initGit(): Promise<void>;
  recordChanges(): Promise<void>;
  reverting: boolean;
  revert(commit: string): Promise<void>;
  showCommit(commit: GitCommit): Promise<void>;
  reportError(message: string): void;
  dismissError(): void;
  dismissNotice(): void;
}

let initializing = false;

function describe(error: unknown): string {
  return errorMessage(error, "作品操作");
}

export const useWorkspaceStore = create<WorkspaceStore>()((set, get) => ({
  ready: false, positions: {}, workspaceEpoch: 0, fileEpoch: 0,
  bindings: [],
  currentId: null,
  catalog: EMPTY_CATALOG,
  file: null,
  selectedPath: null,
  search: null,
  loading: false,
  reverting: false,
  error: null,
  notice: null,

  git: null,
  diff: "",
  log: [],
  drawerOpen: false,
  selectedCommit: null,
  commitDiff: "",

  async init(preferredId) {
    if (initializing) return;
    initializing = true;
    try {
      const bindings = await listWorkspaces();
      set({ bindings });
      const current = bindings.find(item => item.id === preferredId && item.exists) ?? bindings.find((item) => item.exists);
      if (current) await get().selectWorkspace(current.id);
    } catch (error) {
      set({ error: describe(error) });
    } finally {
      initializing = false;
      set({ ready: true });
    }
  },

  async open(path, create) {
    set({ loading: true, error: null });
    try {
      const binding = await openWorkspace(path, create);
      set({ bindings: await listWorkspaces() });
      await get().selectWorkspace(binding.id);
      return binding.id;
    } catch (error) {
      set({ error: describe(error) });
      return null;
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
    const previous = get();
    const positions = { ...previous.positions };
    if (previous.currentId) positions[previous.currentId] = { path: previous.selectedPath, search: previous.search };
    set({
      positions, workspaceEpoch: previous.workspaceEpoch + 1, fileEpoch: previous.fileEpoch + 1,
      currentId: id,
      catalog: EMPTY_CATALOG,
      file: null,
      selectedPath: positions[id]?.path ?? null,
      search: positions[id]?.search ?? null,
      git: null,
      diff: "",
      log: [],
      selectedCommit: null,
      commitDiff: "",
      error: null,
      notice: null,
    });
    // 会话按作品各自加载：切作品不把上一部作品的会话带过来。
    const epoch = get().workspaceEpoch;
    await useConversationsStore.getState().load(id);
    if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
    await get().refreshAll();
  },

  async remove(id, confirmedPath) {
    set({ loading: true, error: null });
    try {
      const result = await removeWorkspace(id, confirmedPath);
      set({ bindings: result.workspaces });
      if (get().currentId === id) {
        const next = result.workspaces.find((item) => item.exists);
        if (next) await get().selectWorkspace(next.id);
        else {
          set({
            workspaceEpoch: get().workspaceEpoch + 1,
            fileEpoch: get().fileEpoch + 1,
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
          useWorkspaceModulesStore.getState().close();
        }
      }
      const positions = { ...get().positions };
      delete positions[id];
      set({ positions, notice: `已删除作品「${result.removed.name}」及本地文件。` });
      return true;
    } catch (error) {
      set({ error: describe(error) });
      return false;
    } finally {
      set({ loading: false });
    }
  },

  async rename(id, name) {
    set({ loading: true, error: null });
    try {
      const result = await renameWorkspace(id, name);
      set({ bindings: result.workspaces });
      const newId = result.workspace.id;
      // 标识由路径算出：当前作品的目录名变了，作品标识与地址栏也要跟着换。
      if (get().currentId === id) await get().selectWorkspace(newId);
      set({ notice: `已把作品重命名为「${result.workspace.name}」。` });
      return newId;
    } catch (error) {
      set({ error: describe(error) });
      return null;
    } finally {
      set({ loading: false });
    }
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
    const epoch = get().workspaceEpoch;
    try {
      const [catalog, git] = await Promise.all([listFiles(id), gitStatus(id)]);
      if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
      set({ catalog, git });
      const modules = useModuleLibraryStore.getState();
      if (modules.workspaceId===id) await modules.loadAssembly();
      else if (!modules.open) await modules.trackAssembly(id);
      if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
      const selectedModule = useWorkspaceModulesStore.getState().selected;
      const assembly = useModuleLibraryStore.getState().assembly;
      if (assembly && selectedModule?.workspaceId===id && !assembly.bindings.some(item=>item.id===selectedModule.bindingId)) useWorkspaceModulesStore.getState().close();
      // 外部编辑后回读当前文件；文件已不存在时清空选择。
      const path = get().selectedPath;
      if (path) {
        const stillThere = [...catalog.chapters, ...catalog.characters, ...catalog.settings].some(
          (entry) => entry.path === path && entry.exists,
        );
        if (stillThere) await get().openPath(path,true);
        else set({ selectedPath: null, file: null });
      }
    } catch (error) {
      if (get().currentId === id && get().workspaceEpoch === epoch) set({ error: describe(error) });
    }
  },

  async refreshGit() {
    const id = get().currentId;
    if (!id) return;
    const epoch = get().workspaceEpoch;
    try {
      const [git, diff, log] = await Promise.all([gitStatus(id), gitDiff(id), gitLog(id)]);
      if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
      set({ git, diff: diff.diff, log: log.commits });
    } catch (error) {
      if (get().currentId === id && get().workspaceEpoch === epoch) set({ error: describe(error) });
    }
  },

  async openPath(path,preserveModule) {
    if (!preserveModule) useWorkspaceModulesStore.getState().close();
    const id = get().currentId;
    if (!id) return;
    const epoch = get().fileEpoch + 1;
    set({ fileEpoch: epoch });
    try {
      const file = await readFile(id, path);
      if (get().currentId !== id || get().fileEpoch !== epoch) return;
      set({ file, selectedPath: path, error: null });
    } catch (error) {
      if (get().currentId !== id || get().fileEpoch !== epoch) return;
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
    if (!id || get().reverting) return;
    set({ reverting: true });
    try {
      await gitRevert(id, commit);
      if (get().currentId !== id) return;
      set({ notice: "已撤销这次修改", selectedCommit: null, commitDiff: "" });
      await get().refreshGit();
      await get().refreshAll();
    } catch (error) {
      if (get().currentId === id) set({ error: describe(error) });
    } finally {
      set({ reverting: false });
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

registerModuleChanges((workspaceId, event) => {
  const library = useModuleLibraryStore.getState();
  if (library.workspaceId === workspaceId) void library.loadAssembly();
  else if (!library.open) void library.trackAssembly(workspaceId);
  if (event.type !== "module_changes_applied" && event.type !== "module_changes_reverted") return;
  const ids = new Set<string>();
  for (const saved of event.saved ?? []) if (saved.module_id) ids.add(saved.module_id);
  for (const operation of event.operations ?? []) {
    if (operation.saved?.module_id) ids.add(operation.saved.module_id);
    if (!operation.saved && operation.module_id) ids.add(operation.module_id);
  }
  useWorkspaceModulesStore.getState().invalidateChanged([...ids]);
});

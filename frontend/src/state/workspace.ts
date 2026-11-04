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
  listFiles,
  fetchReadingPosition,
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
import { usePagePositions } from "./pagePositions";
import { useWritingStore } from "./writing";

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
  /** 按作品与路径缓存已读正文：切回时先显示，后台回读校验磁盘版本。 */
  fileCache: Record<string, FileView>;
  selectedPath: string | null;
  search: SearchResult | null;
  /** 作品级操作（打开、删除）进行中；阅读区不看它。 */
  loading: boolean;
  /** 当前选择还没有可显示的正文（缓存未命中）；后台回读校验不算载入。 */
  fileLoading: boolean;
  openingPath: string | null;
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
  /** 只重读目录；引用选择器展开时用它让列表与当前磁盘一致。 */
  refreshCatalog(): Promise<void>;
  refreshGit(): Promise<void>;
  openPath(path: string, preserveModule?: boolean): Promise<void>;
  runSearch(query: string): Promise<void>;
  clearSearch(): void;

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
const FILE_CACHE_LIMIT = 20;

function fileCacheKey(workspaceId: string, path: string): string {
  return `${workspaceId}\u0000${path}`;
}

function cacheFile(cache: Record<string, FileView>, key: string, file: FileView): Record<string, FileView> {
  const next = { ...cache, [key]: file };
  const keys = Object.keys(next);
  // 对象键保持插入顺序：超过上限时丢弃最早读入的一篇。
  if (keys.length > FILE_CACHE_LIMIT) delete next[keys[0]!];
  return next;
}

function dropWorkspaceFiles(cache: Record<string, FileView>, workspaceId: string): Record<string, FileView> {
  const prefix = `${workspaceId}\u0000`;
  const keys = Object.keys(cache).filter(key => key.startsWith(prefix));
  if (!keys.length) return cache;
  const next = { ...cache };
  for (const key of keys) delete next[key];
  return next;
}

function describe(error: unknown): string {
  return errorMessage(error, "作品操作");
}

export const useWorkspaceStore = create<WorkspaceStore>()((set, get) => ({
  ready: false, positions: {}, workspaceEpoch: 0, fileEpoch: 0,
  bindings: [],
  currentId: null,
  catalog: EMPTY_CATALOG,
  file: null,
  fileCache: {},
  selectedPath: null,
  search: null,
  loading: false,
  fileLoading: false,
  openingPath: null,
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
    const selectedPath = positions[id]?.path ?? null;
    set({
      positions, workspaceEpoch: previous.workspaceEpoch + 1, fileEpoch: previous.fileEpoch + 1,
      currentId: id,
      catalog: EMPTY_CATALOG,
      file: null,
      selectedPath,
      search: positions[id]?.search ?? null,
      git: null,
      diff: "",
      log: [],
      selectedCommit: null,
      commitDiff: "",
      error: null,
      notice: null,
      // 目录与上次阅读位置还没回来之前，先按载入中显示，避免误报「没有打开文章」。
      fileLoading: true,
      openingPath: selectedPath,
    });
    // 会话按作品各自加载：切作品不把上一部作品的会话带过来。
    const epoch = get().workspaceEpoch;
    await useConversationsStore.getState().load(id);
    if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
    if (!get().selectedPath) {
      try {
        const position = await fetchReadingPosition(id);
        if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
        if (position.path) {
          usePagePositions.getState().offset(`${id}:paper:${position.path}`, position.offset);
          set({ selectedPath: position.path, openingPath: position.path });
        }
      } catch (error) {
        if (get().currentId === id && get().workspaceEpoch === epoch) set({ notice: describe(error) });
      }
    }
    const readEpoch = get().fileEpoch;
    await get().refreshAll();
    // 目录里没有可打开的文章时不会发起读取；此时由这里结束载入。读取请求会自己收尾。
    if (get().currentId === id && get().workspaceEpoch === epoch && get().fileEpoch === readEpoch) {
      set({ fileLoading: false, openingPath: null });
    }
  },

  async remove(id, confirmedPath) {
    set({ loading: true, error: null });
    try {
      const result = await removeWorkspace(id, confirmedPath);
      set(state => ({ bindings: result.workspaces, fileCache: dropWorkspaceFiles(state.fileCache, id) }));
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
            fileLoading: false,
            openingPath: null,
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
      set(state => ({ bindings: result.workspaces, fileCache: dropWorkspaceFiles(state.fileCache, id) }));
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
      set(state => ({
        bindings: result.workspaces,
        error: null,
        fileCache: result.removed.reduce((cache, item) => dropWorkspaceFiles(cache, item.id), state.fileCache),
      }));
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
            fileLoading: false,
            openingPath: null,
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

  async refreshCatalog() {
    const id = get().currentId;
    if (!id) return;
    const epoch = get().workspaceEpoch;
    try {
      const catalog = await listFiles(id);
      if (get().currentId !== id || get().workspaceEpoch !== epoch) return;
      set({ catalog });
    } catch (error) {
      if (get().currentId === id && get().workspaceEpoch === epoch) set({ error: describe(error) });
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
        // 用户刚点的文章还在读（openingPath 指向另一篇）时，这次回读不能顶掉它。
        const opening = get().openingPath;
        if (!stillThere) set({ selectedPath: null, file: null });
        else if (opening === null || opening === path) await get().openPath(path,true);
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
    const key = fileCacheKey(id, path);
    const cached = get().fileCache[key];
    // 已读过的文章先显示缓存，避免切回时整屏等待；网络回读只用于校验磁盘版本。
    set({
      fileEpoch: epoch,
      fileLoading: !cached,
      openingPath: path,
      ...(cached ? { file: cached, selectedPath: path } : {}),
    });
    try {
      const file = await readFile(id, path);
      if (get().currentId !== id || get().fileEpoch !== epoch) return;
      set(state => ({ file, selectedPath: path, error: null, fileCache: cacheFile(state.fileCache, key, file) }));
    } catch (error) {
      if (get().currentId !== id || get().fileEpoch !== epoch) return;
      set({ error: describe(error) });
    } finally {
      // 后发的读取或作品切换会接管载入态；只有本次仍是最新请求时才收起。
      if (get().currentId === id && get().fileEpoch === epoch) set({ fileLoading: false, openingPath: null });
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
  // 构思与正文也可能被这一轮改动；工作台按当前选择重读，不用前端拼接结果。
  const writing = useWritingStore.getState();
  if (writing.workspaceId) void writing.refresh();
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

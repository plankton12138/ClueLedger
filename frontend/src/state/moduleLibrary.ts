/**
 * 模块库与装配的界面状态（0.8 D）。
 *
 * 本状态装配库浏览、精确版本读取和作品绑定；原始编辑草稿归 moduleEditor，
 * 发布与应用命令归 modulePublication。原文完整读取后才开放编辑，装配只保存精确引用。
 *
 * 迟到响应同时核对资源与请求代次，切换后不能覆盖当前详情或装配。
 */

import { create } from "zustand";

import {
  createModule,
  deriveWorkspace,
  entryBody,
  fetchAssembly,
  fetchImportInfo,
  fetchPresets,
  fetchCompleteRecord,
  findModuleInfo,
  fetchRevision,
  fetchRevisions,
  forkModule,
  importLegacyMaterials,
  listModules,
  putAssembly,
  restoreModule,
  requestKey,
  trashModule,
  type AssemblyBinding,
  type AssemblyView,
  type EntryInput,
  type ImportInfo,
  type ModuleInfo,
  type ModuleKind,
  type ModuleSpec,
  type RevisionDetail,
  type RevisionSummary,
} from "../api/moduleLibrary";
import { useModuleEditorStore } from "./moduleEditor";
import { publishEntries, retryApplication, publicationError, type Publication } from "./modulePublication";

type KindFilter = ModuleKind | "all";

export interface ModuleLibraryStore {
  publications: Record<string, Publication>;
  creations: Record<string, { input: Parameters<typeof createModule>[0]; running: boolean; result?: Awaited<ReturnType<typeof createModule>> }>;
  retryApplication(commandId: string): Promise<boolean>;
  selectionEpoch: number;
  queryEpoch: number;
  session: number;
  assemblyEpoch: number;
  open: boolean;
  workspaceId: string | null;
  kind: KindFilter;
  includeDeleted: boolean;
  query: string;
  presets: ModuleSpec[];
  modules: ModuleInfo[];
  cursor: string | null;
  total: number;
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  notice: string | null;

  selectedModuleId: string | null;
  selectedInfo: ModuleInfo | null;
  selectedRevisionId: string | null;
  revisions: RevisionSummary[];
  revisionsCursor: string | null;
  revisionsLoading: boolean;
  loadMoreRevisions(): Promise<void>;
  detail: RevisionDetail | null;
  bodies: Record<string, string>;
  detailLoading: boolean;
  assembly: AssemblyView | null;
  importInfo: ImportInfo | null;
  busy: boolean;

  openDrawer(workspaceId: string | null): Promise<void>;
  closeDrawer(): void;
  setKind(kind: KindFilter): void;
  setIncludeDeleted(value: boolean): void;
  setQuery(query: string): void;
  refresh(): Promise<void>;
  loadMore(): Promise<void>;
  selectModule(moduleId: string, revisionId?: string | null): Promise<void>;
  selectRevision(revisionId: string): Promise<void>;
  create(input: { kind: ModuleKind; name: string; title: string; body: string; draftId?: string }): Promise<boolean>;
  fork(name: string): Promise<boolean>;
  trashSelected(): Promise<void>;
  restoreSelected(): Promise<void>;
  saveToLibrary(entries: EntryInput[]): Promise<boolean>;
  saveAndUse(entries: EntryInput[]): Promise<boolean>;
  derive(
    name: string,
    replacements?: Record<string, string>,
  ): Promise<{ id: string; name: string; path: string } | null>;
  bind(moduleId: string, revisionId: string): Promise<void>;
  unbind(bindingId: string): Promise<void>;
  upgrade(bindingId: string, revisionId: string): Promise<void>;
  reorder(bindingId: string, direction: -1 | 1): Promise<void>;
  importLegacy(): Promise<void>;
  loadAssembly(): Promise<void>;
  loadImportInfo(): Promise<void>;
  /** 只跟踪当前作品的装配，不打开抽屉；用于旧轮次材料是否仍适用。 */
  trackAssembly(workspaceId: string): Promise<void>;
  clearNotice(): void;
}

function describe(error: unknown): string {
  return publicationError(error);
}

function selected(modules: ModuleInfo[], moduleId: string): ModuleInfo | undefined {
  return modules.find((item) => item.module_id === moduleId);
}

const initial = {
  publications: {} as Record<string, Publication>,
  creations: {} as ModuleLibraryStore["creations"],
  selectionEpoch: 0,
  queryEpoch: 0,
  session: 0,
  assemblyEpoch: 0,
  open: false,
  workspaceId: null as string | null,
  kind: "all" as KindFilter,
  includeDeleted: false,
  query: "",
  presets: [] as ModuleSpec[],
  modules: [] as ModuleInfo[],
  cursor: null as string | null,
  total: 0,
  loading: false,
  loadingMore: false,
  error: null as string | null,
  notice: null as string | null,
  selectedModuleId: null as string | null,
  selectedInfo: null as ModuleInfo | null,
  selectedRevisionId: null as string | null,
  revisions: [] as RevisionSummary[],
  revisionsCursor: null as string | null,
  revisionsLoading: false,
  detail: null as RevisionDetail | null,
  bodies: {} as Record<string, string>,
  detailLoading: false,
  assembly: null as AssemblyView | null,
  importInfo: null as ImportInfo | null,
  busy: false,
};

export const useModuleLibraryStore = create<ModuleLibraryStore>()((set, get) => ({
  ...initial,

  async openDrawer(workspaceId) {
    set({
      open: true,
      busy: false,
      workspaceId,
      detail: null, bodies: {}, selectedModuleId: null, selectedInfo: null, selectedRevisionId: null,
      detailLoading: false, loadingMore: false, error: null, notice: null,
      assembly: get().workspaceId === workspaceId ? get().assembly : null,
      importInfo: null,
      selectionEpoch: get().selectionEpoch + 1,
      queryEpoch: get().queryEpoch + 1,
      session: get().session + 1,
    });
    const session = get().session;
    try {
      if (get().presets.length === 0) {
        const presets = await fetchPresets();
        if (get().session !== session) return;
        set({ presets });
      }
    } catch (error) {
      if (get().session !== session) return;
      set({ error: describe(error) });
    }
    await Promise.all([get().refresh(), get().loadAssembly(), get().loadImportInfo()]);
  },

  closeDrawer() {
    set({ open: false, selectedModuleId: null, detail: null, bodies: {}, detailLoading: false, error: null,
      loading: false, loadingMore: false, selectionEpoch: get().selectionEpoch + 1,
      queryEpoch: get().queryEpoch + 1, session: get().session + 1 });
  },

  setKind(kind) {
    set({ kind, modules: [], cursor: null, total: 0 });
    void get().refresh();
  },

  setIncludeDeleted(value) {
    set({ includeDeleted: value, modules: [], cursor: null, total: 0 });
    void get().refresh();
  },

  setQuery(query) {
    set({ query });
    void get().refresh();
  },

  async refresh() {
    const { workspaceId, kind, query, includeDeleted } = get();
    const epoch = get().queryEpoch + 1;
    set({ loading: true, loadingMore: false, error: null, queryEpoch: epoch });
    try {
      const page = await listModules({
        kind: kind === "all" ? null : kind,
        q: query,
        limit: 20,
        includeDeleted,
      });
      if (get().workspaceId !== workspaceId || get().queryEpoch !== epoch) return;
      set({ modules: page.items, cursor: page.next_cursor, total: page.total, loading: false });
    } catch (error) {
      if (get().workspaceId !== workspaceId || get().queryEpoch !== epoch) return;
      set({ loading: false, error: describe(error) });
    }
  },

  async loadMore() {
    const { workspaceId, kind, query, cursor, modules, loadingMore, includeDeleted, queryEpoch: epoch } = get();
    if (!cursor || loadingMore) return;
    set({ loadingMore: true });
    try {
      const page = await listModules({
        kind: kind === "all" ? null : kind,
        q: query,
        cursor,
        limit: 20,
        includeDeleted,
      });
      if (get().workspaceId !== workspaceId || get().queryEpoch !== epoch || get().cursor !== cursor) return;
      set({
        modules: [...modules, ...page.items],
        cursor: page.next_cursor,
        total: page.total,
        loadingMore: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId || get().queryEpoch !== epoch) return;
      set({ loadingMore: false, error: describe(error) });
    }
  },

  async selectModule(moduleId, revisionId) {
    const { workspaceId, modules } = get();
    const epoch = get().selectionEpoch + 1;
    set({ selectionEpoch: epoch, selectedModuleId: moduleId, selectedRevisionId: revisionId ?? null,
      selectedInfo: null, detail: null, bodies: {}, revisions: [], revisionsCursor: null, revisionsLoading: false, detailLoading: true, error: null });
    let info = selected(modules, moduleId);
    if (!info) {
      try { info = await findModuleInfo(moduleId) ?? undefined; }
      catch (error) {
        if (get().selectionEpoch === epoch) set({ detailLoading: false, error: describe(error) });
        return;
      }
      if (get().selectionEpoch !== epoch) return;
    }
    set({ selectedInfo: info ?? null });
    const revision = revisionId ?? info?.head ?? null;
    if (!revision) {
      set({ selectedModuleId: moduleId, selectedRevisionId: null, detail: null, bodies: {}, detailLoading: false });
      return;
    }
    set({
      selectedModuleId: moduleId,
      selectedRevisionId: revision,
      detailLoading: true,
      error: null,
    });
    try {
      const [detail, history] = await Promise.all([
        fetchRevision(moduleId, revision),
        fetchRevisions(moduleId),
      ]);
      if (get().workspaceId !== workspaceId || get().selectionEpoch !== epoch) return;
      if (detail.module_id !== moduleId || detail.revision_id !== revision) {
        throw new Error("[MODULE_IDENTITY_MISMATCH] 模块详情：返回版本与选择不一致。建议：重新读取模块。");
      }
      const records = await Promise.all(
        detail.entries.map((entry) => fetchCompleteRecord(moduleId, revision, entry.entry_id, entry)),
      );
      if (get().workspaceId !== workspaceId || get().selectionEpoch !== epoch) return;
      const bodies: Record<string, string> = {};
      for (const record of records) bodies[record.entry_id] = entryBody(record.text);
      useModuleEditorStore.getState().ensure(detail, bodies);
      set({
        detail,
        bodies,
        revisions: history.items,
        revisionsCursor: history.next_cursor,
        selectedRevisionId: revision,
        detailLoading: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId || get().selectionEpoch !== epoch) return;
      set({ detailLoading: false, error: describe(error) });
    }
  },

  async selectRevision(revisionId) {
    const moduleId = get().selectedModuleId;
    if (!moduleId) return;
    await get().selectModule(moduleId, revisionId);
  },

  async loadMoreRevisions() {
    const { selectedModuleId: moduleId, selectionEpoch, revisionsCursor: cursor, revisionsLoading } = get();
    if (!moduleId || !cursor || revisionsLoading) return;
    set({ revisionsLoading: true });
    try {
      const page = await fetchRevisions(moduleId, { cursor });
      if (get().selectionEpoch !== selectionEpoch) return;
      const merged = new Map([...get().revisions, ...page.items].map(item => [item.revision_id, item]));
      set({ revisions: [...merged.values()], revisionsCursor: page.next_cursor, revisionsLoading: false });
    } catch (error) {
      if (get().selectionEpoch === selectionEpoch) set({ revisionsLoading: false, error: describe(error) });
    }
  },

  async create(input) {
    const { session, selectionEpoch } = get();
    const signature = JSON.stringify(input);
    let command = get().creations[signature];
    if (command?.running) return false;
    if (command?.result) return true;
    command ??= { input: { kind: input.kind, name: input.name,
      entries: [{ entry_id: "first", title: input.title || input.name, body: input.body }],
      request_key: requestKey("create") }, running: false };
    command = { ...command, running: true };
    set({ busy: true, error: null, notice: null, creations: { ...get().creations, [signature]: command } });
    const ownsView = () => get().session === session && get().selectionEpoch === selectionEpoch;
    try {
      const created = await createModule(command.input);
      set({ creations: { ...get().creations, [signature]: { ...command, running: false, result: created } } });
      if (ownsView()) {
        set({ busy: false, notice: "已新建模块并发布第一版。" });
        await get().refresh();
        if (ownsView()) await get().selectModule(created.module.module_id, created.module.head);
      }
      return true;
    } catch (error) {
      set({ creations: { ...get().creations, [signature]: { ...command, running: false } } });
      if (ownsView()) set({ busy: false, error: describe(error) });
      return false;
    } finally {
      if (get().session === session) set({ busy: false });
    }
  },

  async saveToLibrary(entries) {
    return publishEntries(get, set, entries, false);
  },

  async saveAndUse(entries) {
    return publishEntries(get, set, entries, true);
  },

  async retryApplication(commandId) {
    return retryApplication(get, set, commandId);
  },

  async derive(name, replacements = {}) {
    const operationSession = get().session;
    const { workspaceId, assembly } = get();
    const trimmed = name.trim();
    if (!workspaceId || !assembly) return null;
    if (!trimmed) {
      set({ error: "新作品需要一个名称" });
      return null;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const result = await deriveWorkspace(workspaceId, {
        name: trimmed,
        replacements,
        expected_digest: assembly.digest,
        request_key: requestKey("derive"),
      });
      if (get().session !== operationSession) return null;
      set({ busy: false, notice: `已用当前组合新建作品：${result.workspace.name}。` });
      return result.workspace;
    } catch (error) {
      if (get().session !== operationSession) return null;
      set({ busy: false, error: describe(error) });
      return null;
    }
  },

  async bind(moduleId, revisionId) {
    const operationSession = get().session;
    const { workspaceId, assembly, detail } = get();
    if (!workspaceId) return;
    const libraryId = assembly?.library_id ?? detail?.library_id ?? "";
    if (!libraryId) {
      set({ error: "还没有模块库，无法绑定模块" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const next = await putAssembly(workspaceId, {
        bindings: [
          ...(assembly?.bindings ?? []).map((item) => ({
            id: item.id,
            label: item.label,
            module: item.module,
          })),
          { module: { library_id: libraryId, module_id: moduleId, revision_id: revisionId } },
        ],
        links: assembly?.links ?? [],
        expected_digest: assembly?.digest ?? null,
        request_key: requestKey("bind"),
      });
      if (get().session !== operationSession) return;
      set({ assembly: next, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已装入本作品。" });
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async unbind(bindingId) {
    const operationSession = get().session;
    const { workspaceId, assembly } = get();
    if (!workspaceId || !assembly) return;
    set({ busy: true, error: null, notice: null });
    try {
      const next = await putAssembly(workspaceId, {
        bindings: assembly.bindings
          .filter((item) => item.id !== bindingId)
          .map((item) => ({ id: item.id, label: item.label, module: item.module })),
        // 卸下只移除与被卸绑定有关的关系，其他关联保持原样。
        links: assembly.links.filter(
          ([left, right]) => left !== bindingId && right !== bindingId,
        ),
        expected_digest: assembly.digest,
        request_key: requestKey("unbind"),
      });
      if (get().session !== operationSession) return;
      set({ assembly: next, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已卸下；后续查询不再包含这个模块。" });
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async upgrade(bindingId, revisionId) {
    const operationSession = get().session;
    const { workspaceId, assembly } = get();
    if (!workspaceId || !assembly) return;
    const binding = assembly.bindings.find((item) => item.id === bindingId);
    if (!binding) {
      set({ error: "这个作品没有对应的绑定，请刷新后重试" });
      return;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const next = await putAssembly(workspaceId, {
        bindings: assembly.bindings.map((item) =>
          item.id === bindingId
            ? {
                id: item.id,
                label: item.label,
                module: { ...item.module, revision_id: revisionId },
              }
            : { id: item.id, label: item.label, module: item.module },
        ),
        links: assembly.links,
        expected_digest: assembly.digest,
        request_key: requestKey("upgrade"),
      });
      if (get().session !== operationSession) return;
      set({ assembly: next, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已切换本作品使用的版本。" });
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async reorder(bindingId, direction) {
    const operationSession = get().session;
    const { workspaceId, assembly } = get();
    if (!workspaceId || !assembly) return;
    const index = assembly.bindings.findIndex((item) => item.id === bindingId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= assembly.bindings.length) return;
    const bindings = [...assembly.bindings];
    [bindings[index], bindings[target]] = [bindings[target], bindings[index]];
    set({ busy: true, error: null, notice: null });
    try {
      const next = await putAssembly(workspaceId, {
        bindings: bindings.map((item) => ({
          id: item.id,
          label: item.label,
          module: item.module,
        })),
        links: assembly.links,
        expected_digest: assembly.digest,
        request_key: requestKey("reorder"),
      });
      if (get().session !== operationSession) return;
      set({ assembly: next, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已调整装配顺序。" });
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async fork(name) {
    const operationSession = get().session;
    const { selectedModuleId, selectedRevisionId } = get();
    if (!selectedModuleId || !selectedRevisionId) return false;
    if (!name.trim()) {
      set({ error: "变体需要一个名称" });
      return false;
    }
    set({ busy: true, error: null, notice: null });
    try {
      const created = await forkModule(selectedModuleId, {
        base_revision: selectedRevisionId,
        name: name.trim(),
        request_key: requestKey("fork"),
      });
      if (get().session !== operationSession) return false;
      set({ busy: false, notice: "已另存变体；原模块与已绑定作品不受影响。" });
      await get().refresh();
      if (get().session !== operationSession) return false;
      await get().selectModule(created.module.module_id, created.module.head);
      if (get().session !== operationSession) return false;
      return true;
    } catch (error) {
      if (get().session !== operationSession) return false;
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  async trashSelected() {
    const operationSession = get().session;
    const { selectedModuleId, modules } = get();
    const info = selectedModuleId ? selected(modules, selectedModuleId) ?? get().selectedInfo : undefined;
    if (!selectedModuleId || !info) return;
    set({ busy: true, error: null, notice: null });
    try {
      const updated = await trashModule(selectedModuleId, {
        expected_head: info.head,
        request_key: requestKey("trash"),
      });
      if (get().session !== operationSession) return;
      if (get().selectedModuleId === selectedModuleId) set({ selectedInfo: updated });
      set({ busy: false, notice: "已删除；已绑定这个版本的作品仍可读取。" });
      await get().refresh();
      if (get().session !== operationSession) return;
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreSelected() {
    const operationSession = get().session;
    const { selectedModuleId, modules } = get();
    const info = selectedModuleId ? selected(modules, selectedModuleId) ?? get().selectedInfo : undefined;
    if (!selectedModuleId || !info) return;
    set({ busy: true, error: null, notice: null });
    try {
      const updated = await restoreModule(selectedModuleId, {
        expected_head: info.head,
        request_key: requestKey("restore"),
      });
      if (get().session !== operationSession) return;
      if (get().selectedModuleId === selectedModuleId) set({ selectedInfo: updated });
      set({ busy: false, notice: "已恢复，可以重新装入作品。" });
      await get().refresh();
      if (get().session !== operationSession) return;
    } catch (error) {
      if (get().session !== operationSession) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async importLegacy() {
    const operationSession = get().session;
    const { workspaceId, importInfo } = get();
    if (!workspaceId) return;
    set({ busy: true, error: null, notice: null });
    try {
      const result = await importLegacyMaterials(workspaceId, {
        expected_digest: importInfo?.digest ?? null,
        request_key: requestKey("import"),
      });
      if (get().session !== operationSession) return;
      if (get().workspaceId !== workspaceId) return;
      const created = result.items.filter((item) => item.action === "created").length;
      const published = result.items.filter((item) => item.action === "published").length;
      const unchanged = result.items.filter((item) => item.action === "unchanged").length;
      set({
        assembly: result.assembly,
        busy: false,
        notice:
          result.items.length === 0
            ? "没有可导入的旧资料。"
            : `已导入 ${result.items.length} 项旧资料（新建 ${created}、更新 ${published}、无变化 ${unchanged}）。`,
      });
      await Promise.all([get().refresh(), get().loadImportInfo()]);
      if (get().session !== operationSession) return;
    } catch (error) {
      if (get().session !== operationSession) return;
      if (get().workspaceId !== workspaceId) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async loadAssembly() {
    const operationSession = get().session;
    const { workspaceId } = get();
    if (!workspaceId) return;
    const assemblyEpoch = get().assemblyEpoch + 1;
    set({ assemblyEpoch });
    try {
      const assembly = await fetchAssembly(workspaceId);
      if (get().session !== operationSession || get().assemblyEpoch !== assemblyEpoch) return;
      if (get().workspaceId !== workspaceId) return;
      set({ assembly });
    } catch (error) {
      if (get().session !== operationSession || get().assemblyEpoch !== assemblyEpoch) return;
      if (get().workspaceId !== workspaceId) return;
      set({ error: describe(error) });
    }
  },

  async loadImportInfo() {
    const operationSession = get().session;
    const { workspaceId } = get();
    if (!workspaceId) return;
    try {
      const importInfo = await fetchImportInfo(workspaceId);
      if (get().session !== operationSession) return;
      if (get().workspaceId !== workspaceId) return;
      set({ importInfo });
    } catch (error) {
      if (get().session !== operationSession) return;
      if (get().workspaceId !== workspaceId) return;
      set({ error: describe(error) });
    }
  },

  async trackAssembly(workspaceId) {
    if (get().workspaceId === workspaceId && get().assembly) return;
    set({ workspaceId, assembly: null, detail: null, bodies: {}, selectedModuleId: null,
      selectedRevisionId: null, detailLoading: false, selectionEpoch: get().selectionEpoch + 1,
      queryEpoch: get().queryEpoch + 1, session: get().session + 1 });
    const operationSession = get().session;
    const assemblyEpoch = get().assemblyEpoch + 1;
    set({ assemblyEpoch });
    try {
      const assembly = await fetchAssembly(workspaceId);
      if (get().session !== operationSession || get().assemblyEpoch !== assemblyEpoch) return;
      if (get().workspaceId !== workspaceId) return;
      set({ assembly });
    } catch {
      // 没有装配或接口不可用时不打断写作；旧轮次只是不显示模块变更提示。
    }
  },

  clearNotice() {
    set({ notice: null, error: null });
  },
}));

/** 装配里某条绑定对应的显示名；缺依赖时用模块 ID 兜底。 */
export function bindingLabel(binding: AssemblyBinding): string {
  return binding.label || binding.name || binding.module.module_id;
}

export function bindingProblems(binding: AssemblyBinding): string {
  const problems = binding.problems ?? [];
  if (problems.length === 0) return "";
  const labels: Record<string, string> = {
    module_missing: "模块不在库里",
    revision_missing: "版本缺失",
    module_trashed: "模块已删除",
    library_missing: "库不可用",
    library_mismatch: "来自另一个库",
  };
  return problems.map((item) => labels[item] ?? item).join("、");
}

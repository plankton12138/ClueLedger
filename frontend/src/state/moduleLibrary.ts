/**
 * 模块库与装配的界面状态（0.8 D）。
 *
 * 两件事放在一个 store 里，是因为界面总是一起用：浏览库、选中版本、装入或保存并用于
 * 本作品。库原文只在详情里按版本读一次，装配只保存精确引用。
 *
 * 迟到的响应按作品与模块丢弃：切换作品后旧结果不能写进新作品的装配。
 */

import { create } from "zustand";

import {
  createModule,
  deriveWorkspace,
  entryBody,
  fetchAssembly,
  fetchImportInfo,
  fetchPresets,
  fetchRecord,
  fetchRevision,
  fetchRevisions,
  forkModule,
  importLegacyMaterials,
  listModules,
  postModuleUpdate,
  publishRevision,
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
import { WorkspaceApiError } from "../api/workspace";

type KindFilter = ModuleKind | "all";

interface ModuleLibraryStore {
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
  selectedRevisionId: string | null;
  revisions: RevisionSummary[];
  detail: RevisionDetail | null;
  bodies: Record<string, string>;
  detailLoading: boolean;
  assembly: AssemblyView | null;
  importInfo: ImportInfo | null;
  busy: boolean;

  openDrawer(workspaceId: string): Promise<void>;
  closeDrawer(): void;
  setKind(kind: KindFilter): void;
  setIncludeDeleted(value: boolean): void;
  setQuery(query: string): void;
  refresh(): Promise<void>;
  loadMore(): Promise<void>;
  selectModule(moduleId: string, revisionId?: string | null): Promise<void>;
  selectRevision(revisionId: string): Promise<void>;
  create(input: { kind: ModuleKind; name: string; title: string; body: string }): Promise<boolean>;
  fork(name: string): Promise<boolean>;
  trashSelected(): Promise<void>;
  restoreSelected(): Promise<void>;
  saveToLibrary(entries: EntryInput[]): Promise<boolean>;
  saveAndUse(entries: EntryInput[]): Promise<boolean>;
  derive(name: string): Promise<{ id: string; name: string; path: string } | null>;
  bind(moduleId: string, revisionId: string): Promise<void>;
  unbind(bindingId: string): Promise<void>;
  upgrade(bindingId: string, revisionId: string): Promise<void>;
  importLegacy(): Promise<void>;
  loadAssembly(): Promise<void>;
  loadImportInfo(): Promise<void>;
  clearNotice(): void;
}

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) return `${error.why}。${error.fix}`;
  return error instanceof Error ? error.message : "操作没有完成";
}

function selected(modules: ModuleInfo[], moduleId: string): ModuleInfo | undefined {
  return modules.find((item) => item.module_id === moduleId);
}

const initial = {
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
  selectedRevisionId: null as string | null,
  revisions: [] as RevisionSummary[],
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
      ...initial,
      open: true,
      workspaceId,
    });
    try {
      if (get().presets.length === 0) {
        const presets = await fetchPresets();
        set({ presets });
      }
    } catch (error) {
      set({ error: describe(error) });
    }
    await Promise.all([get().refresh(), get().loadAssembly(), get().loadImportInfo()]);
  },

  closeDrawer() {
    set({ open: false, selectedModuleId: null, detail: null, bodies: {}, error: null });
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
    if (!workspaceId) return;
    set({ loading: true, error: null });
    try {
      const page = await listModules({
        kind: kind === "all" ? null : kind,
        q: query,
        limit: 20,
        includeDeleted,
      });
      if (get().workspaceId !== workspaceId) return;
      set({ modules: page.items, cursor: page.next_cursor, total: page.total, loading: false });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ loading: false, error: describe(error) });
    }
  },

  async loadMore() {
    const { workspaceId, kind, query, cursor, modules, loadingMore, includeDeleted } = get();
    if (!workspaceId || !cursor || loadingMore) return;
    set({ loadingMore: true });
    try {
      const page = await listModules({
        kind: kind === "all" ? null : kind,
        q: query,
        cursor,
        limit: 20,
        includeDeleted,
      });
      if (get().workspaceId !== workspaceId) return;
      set({
        modules: [...modules, ...page.items],
        cursor: page.next_cursor,
        total: page.total,
        loadingMore: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ loadingMore: false, error: describe(error) });
    }
  },

  async selectModule(moduleId, revisionId) {
    const { workspaceId, modules } = get();
    if (!workspaceId) return;
    const info = selected(modules, moduleId);
    const revision = revisionId ?? info?.head ?? null;
    if (!revision) {
      set({ selectedModuleId: moduleId, selectedRevisionId: null, detail: null, bodies: {} });
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
      const records = await Promise.all(
        detail.entries.map((entry) => fetchRecord(moduleId, revision, entry.entry_id)),
      );
      if (get().workspaceId !== workspaceId) return;
      const bodies: Record<string, string> = {};
      for (const record of records) bodies[record.entry_id] = entryBody(record.text);
      set({
        detail,
        bodies,
        revisions: history.items,
        selectedRevisionId: revision,
        detailLoading: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ detailLoading: false, error: describe(error) });
    }
  },

  async selectRevision(revisionId) {
    const moduleId = get().selectedModuleId;
    if (!moduleId) return;
    await get().selectModule(moduleId, revisionId);
  },

  async create(input) {
    const { workspaceId } = get();
    if (!workspaceId) return false;
    set({ busy: true, error: null, notice: null });
    try {
      const created = await createModule({
        kind: input.kind,
        name: input.name,
        entries: [
          {
            entry_id: "first",
            title: input.title || input.name,
            body: input.body,
          },
        ],
        request_key: requestKey("create"),
      });
      set({ busy: false, notice: "已新建模块并发布第一版。" });
      await get().refresh();
      await get().selectModule(created.module.module_id, created.module.head);
      return true;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  async saveToLibrary(entries) {
    const { workspaceId, selectedModuleId, detail, selectedRevisionId, modules } = get();
    if (!workspaceId || !selectedModuleId || !detail) return false;
    const info = selected(modules, selectedModuleId);
    set({ busy: true, error: null, notice: null });
    try {
      const revision = await publishRevision(selectedModuleId, {
        base_revision: selectedRevisionId,
        expected_head: info?.head ?? null,
        entries,
        request_key: requestKey("publish"),
      });
      set({ busy: false, notice: "已保存到模块库；其他作品保持原版本。" });
      await get().refresh();
      await get().selectModule(selectedModuleId, revision.revision_id);
      return true;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  async saveAndUse(entries) {
    const { workspaceId, selectedModuleId, detail, selectedRevisionId, assembly, modules } = get();
    if (!workspaceId || !selectedModuleId || !detail) return false;
    const info = selected(modules, selectedModuleId);
    set({ busy: true, error: null, notice: null });
    try {
      const result = await postModuleUpdate(workspaceId, {
        module_id: selectedModuleId,
        base_revision: selectedRevisionId,
        expected_head: info?.head ?? null,
        entries,
        expected_digest: assembly?.digest ?? null,
        request_key: requestKey("save-and-use"),
      });
      if (!result.published) {
        set({
          busy: false,
          error: result.error ? `${result.error.why}。${result.error.fix}` : "发布没有完成",
        });
        return false;
      }
      await get().loadAssembly();
      await get().refresh();
      if (result.binding_updated) {
        set({ busy: false, notice: "已发布并用于本作品。" });
        await get().selectModule(selectedModuleId, result.module?.revision_id ?? null);
        return true;
      }
      set({
        busy: false,
        notice: "已发布新版本，但本作品绑定没有更新。可以只重试绑定。",
        error: result.error ? `${result.error.why}。${result.error.fix}` : null,
      });
      return false;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  async derive(name) {
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
        expected_digest: assembly.digest,
        request_key: requestKey("derive"),
      });
      set({ busy: false, notice: `已用当前组合新建作品：${result.workspace.name}。` });
      return result.workspace;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return null;
    }
  },

  async bind(moduleId, revisionId) {
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
      set({ assembly: next, busy: false, notice: "已装入本作品。" });
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async unbind(bindingId) {
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
      set({ assembly: next, busy: false, notice: "已卸下；后续查询不再包含这个模块。" });
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async upgrade(bindingId, revisionId) {
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
      set({ assembly: next, busy: false, notice: "已切换本作品使用的版本。" });
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async fork(name) {
    const { workspaceId, selectedModuleId, selectedRevisionId } = get();
    if (!workspaceId || !selectedModuleId || !selectedRevisionId) return false;
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
      set({ busy: false, notice: "已另存变体；原模块与已绑定作品不受影响。" });
      await get().refresh();
      await get().selectModule(created.module.module_id, created.module.head);
      return true;
    } catch (error) {
      set({ busy: false, error: describe(error) });
      return false;
    }
  },

  async trashSelected() {
    const { selectedModuleId, modules } = get();
    const info = selectedModuleId ? selected(modules, selectedModuleId) : undefined;
    if (!selectedModuleId || !info) return;
    set({ busy: true, error: null, notice: null });
    try {
      await trashModule(selectedModuleId, {
        expected_head: info.head,
        request_key: requestKey("trash"),
      });
      set({ busy: false, notice: "已删除；已绑定这个版本的作品仍可读取。" });
      await get().refresh();
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async restoreSelected() {
    const { selectedModuleId, modules } = get();
    const info = selectedModuleId ? selected(modules, selectedModuleId) : undefined;
    if (!selectedModuleId || !info) return;
    set({ busy: true, error: null, notice: null });
    try {
      await restoreModule(selectedModuleId, {
        expected_head: info.head,
        request_key: requestKey("restore"),
      });
      set({ busy: false, notice: "已恢复，可以重新装入作品。" });
      await get().refresh();
    } catch (error) {
      set({ busy: false, error: describe(error) });
    }
  },

  async importLegacy() {
    const { workspaceId, importInfo } = get();
    if (!workspaceId) return;
    set({ busy: true, error: null, notice: null });
    try {
      const result = await importLegacyMaterials(workspaceId, {
        expected_digest: importInfo?.digest ?? null,
        request_key: requestKey("import"),
      });
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
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ busy: false, error: describe(error) });
    }
  },

  async loadAssembly() {
    const { workspaceId } = get();
    if (!workspaceId) return;
    try {
      const assembly = await fetchAssembly(workspaceId);
      if (get().workspaceId !== workspaceId) return;
      set({ assembly });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ error: describe(error) });
    }
  },

  async loadImportInfo() {
    const { workspaceId } = get();
    if (!workspaceId) return;
    try {
      const importInfo = await fetchImportInfo(workspaceId);
      if (get().workspaceId !== workspaceId) return;
      set({ importInfo });
    } catch (error) {
      if (get().workspaceId !== workspaceId) return;
      set({ error: describe(error) });
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

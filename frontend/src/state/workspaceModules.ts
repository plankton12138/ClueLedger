import { create } from "zustand";
import { createModule, entryBody, fetchCompleteRecord, fetchRevision, requestKey, saveWorkspaceModule, type AssemblyBinding, type AssemblyModuleRef, type EntryInput } from "../api/moduleLibrary";
import { errorMessage } from "../api/errors";
import { bindingLabel, useModuleLibraryStore } from "./moduleLibrary";
import { documentEntries, documentKey, type ModuleDocument } from "./moduleContent";

export interface WorkspaceModuleDraft { name: string; entries: EntryInput[]; base: AssemblyModuleRef; baseline: string; sequence: number }
export function workspaceDraftKey(workspaceId: string, binding: AssemblyBinding): string {
  return JSON.stringify([workspaceId, binding.id]);
}
export function workspaceModuleDirty(draft?: WorkspaceModuleDraft): boolean {
  return Boolean(draft && JSON.stringify([draft.name, draft.entries]) !== draft.baseline);
}
interface WorkspaceModulesStore {
  selected: { workspaceId: string; bindingId: string; anchor?: string } | null;
  documents: Record<string, ModuleDocument>;
  loading: Record<string, boolean>;
  errors: Record<string, string>;
  drafts: Record<string, WorkspaceModuleDraft>;
  busy: string | null;
  message: { workspaceId: string; text: string; error: boolean } | null;
  select(workspaceId: string, bindingId: string, anchor?: string): void;
  close(): void;
  load(binding: AssemblyBinding): Promise<void>;
  begin(workspaceId: string, binding: AssemblyBinding): void;
  edit(key: string, patch: Partial<Pick<WorkspaceModuleDraft, "name" | "entries">>): void;
  discard(key: string): void;
  save(workspaceId: string, binding: AssemblyBinding): Promise<boolean>;
  saveToLibrary(workspaceId: string, binding: AssemblyBinding, draft?: WorkspaceModuleDraft): Promise<void>;
  clearMessage(): void;
}
const pending = new Map<string, Promise<void>>();
const saveCommands = new Map<string, Parameters<typeof saveWorkspaceModule>[2]>();
const copyCommands = new Map<string, { input: Parameters<typeof createModule>[0]; done: boolean }>();
export const useWorkspaceModulesStore = create<WorkspaceModulesStore>()((set,get) => ({
  selected: null, documents: {}, loading: {}, errors: {}, drafts: {}, busy: null, message: null,
  select(workspaceId,bindingId,anchor) { set({ selected: { workspaceId,bindingId,anchor }, message: null }); },
  close() { set({ selected: null }); },
  async load(binding) {
    const key = documentKey(binding.module);
    if (get().documents[key]) return;
    if (pending.has(key)) return pending.get(key);
    const task = (async () => {
      set(state => ({ loading: { ...state.loading, [key]: true }, errors: { ...state.errors, [key]: "" } }));
      try {
        const ref = binding.module;
        const detail = await fetchRevision(ref.module_id,ref.revision_id);
        if (detail.library_id !== ref.library_id) throw new Error("[LIBRARY_MISMATCH] 模块内容：来源库已变化。建议：重新连接原模块库后读取。");
        const bodies: Record<string,string> = {};
        // 顺序分段读取，避免展开一份旧模块时同时请求数百条长记录。
        for (const entry of detail.entries) {
          const record = await fetchCompleteRecord(ref.module_id,ref.revision_id,entry.entry_id,entry);
          bodies[entry.entry_id] = entryBody(record.text);
        }
        set(state => ({ documents: { ...state.documents, [key]: { detail,bodies } } }));
      } catch (error) { set(state => ({ errors: { ...state.errors, [key]: errorMessage(error,"模块内容") } })); }
      finally { set(state => ({ loading: { ...state.loading, [key]: false } })); pending.delete(key); }
    })();
    pending.set(key,task);
    return task;
  },
  begin(workspaceId,binding) {
    const key = workspaceDraftKey(workspaceId,binding), document = get().documents[documentKey(binding.module)];
    if (!document || get().drafts[key]) return;
    const entries = documentEntries(document), name = bindingLabel(binding);
    set(state => ({ drafts: { ...state.drafts, [key]: { entries, name, base: { ...binding.module }, baseline: JSON.stringify([name,entries]), sequence: 0 } } }));
  },
  edit(key,patch) { set(state => { const draft = state.drafts[key]; return draft ? { drafts: { ...state.drafts, [key]: { ...draft,...patch, sequence: draft.sequence+1 } } } : {}; }); },
  discard(key) { set(state => { const drafts = { ...state.drafts }; delete drafts[key]; return { drafts }; }); },
  async save(workspaceId,binding) {
    const key = workspaceDraftKey(workspaceId,binding), draft = get().drafts[key];
    const library = useModuleLibraryStore.getState();
    if (!draft || get().busy || library.workspaceId !== workspaceId || !library.assembly) return false;
    const signature = JSON.stringify([key,draft.base,draft.name,draft.entries]);
    const input = saveCommands.get(signature) ?? { module_id: draft.base.module_id, base_revision: draft.base.revision_id,
      name: draft.name, entries: draft.entries, expected_digest: library.assembly.digest, request_key: requestKey("workspace-content") };
    saveCommands.set(signature,input);
    const session = library.session;
    set({ busy: key, message: null });
    try {
      const assembly = await saveWorkspaceModule(workspaceId,binding.id,input);
      const current = useModuleLibraryStore.getState();
      if (current.workspaceId === workspaceId && current.session === session) useModuleLibraryStore.setState({ assembly, assemblyEpoch: current.assemblyEpoch+1 });
      if (get().drafts[key]?.sequence === draft.sequence) get().discard(key);
      saveCommands.delete(signature);
      set({ message: { workspaceId, text: "已保存到当前作品。", error: false } });
      return true;
    } catch(error) {
      // 明确的冲突保留草稿，但重新读取装配后允许使用新的指纹重做。
      if (error && typeof error === "object" && "status" in error) saveCommands.delete(signature);
      set({ message: { workspaceId, text: errorMessage(error,"保存模块"), error: true } });
      return false;
    } finally { set({ busy: null }); }
  },
  async saveToLibrary(workspaceId,binding,draft) {
    const document = get().documents[documentKey(draft?.base ?? binding.module)];
    if (!document || get().busy) return;
    const signature = JSON.stringify([workspaceId,binding.id,draft?.base ?? binding.module,draft?.name ?? bindingLabel(binding),draft?.entries]);
    const command = copyCommands.get(signature) ?? { input: { kind: document.detail.kind, name: draft?.name || bindingLabel(binding), entries: draft?.entries ?? documentEntries(document), request_key: requestKey("copy-to-library") }, done: false };
    copyCommands.set(signature,command);
    if (command.done) { set({ message: { workspaceId, text: "这份内容已保存到模块库。", error: false } }); return; }
    set({ busy: workspaceDraftKey(workspaceId,binding), message: null });
    try {
      await createModule(command.input); command.done = true;
      set({ message: { workspaceId, text: "已另存一份到模块库。", error: false } });
    } catch(error) { set({ message: { workspaceId, text: errorMessage(error,"保存到模块库"), error: true } }); }
    finally { set({ busy: null }); }
  },
  clearMessage() { set({ message: null }); },
}));

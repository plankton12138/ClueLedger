/** 发布和作品应用各自保留命令快照；网络结果未知时使用同键同体重试。 */
import { fetchAssembly, postModuleUpdate, publishRevision, putAssembly, requestKey,
  type AssemblyBinding, type AssemblyModuleRef, type EntryInput } from "../api/moduleLibrary";
import { WorkspaceApiError } from "../api/workspace";
import { moduleDraftKey, useModuleEditorStore, type EntryRow } from "./moduleEditor";
import type { ModuleLibraryStore } from "./moduleLibrary";

export interface Publication {
  id: string;
  signature: string;
  draftKey: string;
  sequence: number;
  rows: EntryRow[];
  workspaceId: string | null;
  moduleId: string;
  input: Parameters<typeof postModuleUpdate>[1];
  originalTargets: AssemblyBinding[];
  running: boolean;
  published: AssemblyModuleRef | null;
  applied: boolean;
  error: string | null;
  applicationInput?: Parameters<typeof putAssembly>[1];
}
type Get = () => ModuleLibraryStore;
type Set = (patch: Partial<ModuleLibraryStore>) => void;
export function publicationError(error: unknown): string {
  if (error instanceof WorkspaceApiError) return `[${error.code}] ${error.where}：${error.why}。建议：${error.fix}。`;
  if (error instanceof Error && error.message.startsWith("[")) return error.message;
  return "[MODULE_REQUEST_FAILED] 模块操作：请求未完成，结果可能尚未返回。建议：保留当前内容并重试同一操作。";
}
function update(get: Get, set: Set, value: Publication): void {
  set({ publications: { ...get().publications, [value.id]: value } });
}
export async function publishEntries(get: Get, set: Set, entries: EntryInput[], useInWorkspace: boolean): Promise<boolean> {
  const state = get();
  const { detail, selectedModuleId: moduleId, selectedRevisionId: revisionId, workspaceId, session, selectionEpoch } = state;
  if (!detail || !moduleId || state.detailLoading || detail.module_id !== moduleId || detail.revision_id !== revisionId || (useInWorkspace && !workspaceId)) return false;
  const key = moduleDraftKey(detail);
  const draft = useModuleEditorStore.getState().drafts[key];
  const signature = JSON.stringify([key, useInWorkspace ? workspaceId : null, entries]);
  let command = Object.values(state.publications).find(item => item.signature === signature);
  if (command?.running) return false;
  if (command?.published) return command.workspaceId ? retryApplication(get, set, command.id) : true;
  command ??= {
    id: requestKey("publish"), signature, draftKey: key, sequence: draft?.sequence ?? 0,
    rows: structuredClone(draft?.rows ?? []), workspaceId: useInWorkspace ? workspaceId : null, moduleId,
    input: { module_id: moduleId, base_revision: revisionId, expected_head: state.modules.find(item => item.module_id === moduleId)?.head ?? (state.selectedInfo?.module_id === moduleId ? state.selectedInfo.head : null),
      entries: structuredClone(entries), expected_digest: state.assembly?.digest ?? null, request_key: "" },
    originalTargets: structuredClone(state.assembly?.bindings.filter(item => item.module.module_id === moduleId) ?? []),
    running: false, published: null, applied: false, error: null,
  };
  command = { ...command, input: { ...command.input, request_key: command.id }, running: true, error: null };
  update(get, set, command);
  set({ busy: true, error: null, notice: null });
  const ownsView = () => get().session === session && get().selectionEpoch === selectionEpoch && get().selectedModuleId === moduleId;
  try {
    let result;
    if (useInWorkspace) result = await postModuleUpdate(command.workspaceId!, command.input);
    else {
      const { module_id: _, expected_digest: __, ...input } = command.input;
      const revision = await publishRevision(moduleId, input);
      result = { published: true, module: { library_id: detail.library_id, module_id: moduleId, revision_id: revision.revision_id }, binding_updated: false };
    }
    if (!result.published || !result.module) {
      const message = result.error ? `[${result.error.code}] ${result.error.where}：${result.error.why}。建议：${result.error.fix}。` : "[MODULE_PUBLISH_FAILED] 模块发布：发布未完成。建议：保留草稿并重新读取版本信息。";
      command = { ...command, running: false, error: message };
      update(get, set, command);
      if (ownsView()) set({ busy: false, error: message });
      return false;
    }
    command = { ...command, published: result.module, applied: result.binding_updated, running: false };
    update(get, set, command);
    useModuleEditorStore.getState().published(key, command.sequence, result.module.revision_id, command.rows);
    const notice = !useInWorkspace ? "已保存到模块库；其他作品保持原版本。" : result.binding_updated ? "已发布并用于本作品。" : "已发布新版本，但本作品绑定没有更新。可以只重试绑定。";
    const error = result.error ? `[${result.error.code}] ${result.error.where}：${result.error.why}。建议：${result.error.fix}。` : null;
    command = { ...command, error }; update(get, set, command);
    const viewingBase = () => get().detail?.library_id === detail.library_id && get().detail?.module_id === moduleId
      && get().selectedRevisionId === revisionId && !get().detailLoading;
    const viewSession = get().session;
    if (viewingBase()) {
      if (ownsView()) set({ busy: false });
      await get().refresh();
      if (get().session === viewSession && viewingBase()) await get().selectModule(moduleId, result.module.revision_id);
      if (get().session === session && get().selectedModuleId === moduleId && get().selectedRevisionId === result.module.revision_id) {
        set({ notice, error });
        if (useInWorkspace) await get().loadAssembly();
      }
    }
    return !useInWorkspace || result.binding_updated;
  } catch (error) {
    const message = publicationError(error);
    command = { ...command, running: false, error: message }; update(get, set, command);
    if (ownsView()) set({ busy: false, error: message });
    return false;
  } finally {
    if (get().session === session) set({ busy: false });
  }
}

export async function retryApplication(get: Get, set: Set, id: string): Promise<boolean> {
  let command = get().publications[id];
  if (!command?.published || !command.workspaceId || command.running) return false;
  if (command.applied) return true;
  const workspaceId = command.workspaceId;
  const session = get().session;
  const ownsView = () => get().workspaceId === workspaceId && get().session === session;
  command = { ...command, running: true, error: null }; update(get, set, command);
  if (ownsView()) set({ busy: true, error: null });
  try {
    if (!command.applicationInput) {
      const latest = await fetchAssembly(workspaceId);
      const targets = latest.bindings.filter(item => item.module.module_id === command.moduleId);
      const identities = (items: AssemblyBinding[]) => JSON.stringify(items.map(item => [item.id, item.module]));
      if (targets.length > 0 && targets.every(item => JSON.stringify(item.module) === JSON.stringify(command.published))) {
        command = { ...command, applied: true, running: false }; update(get, set, command);
        if (ownsView()) set({ assembly: latest, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已将已发布版本用于本作品。" });
        return true;
      }
      if (identities(targets) !== identities(command.originalTargets)) {
        throw new Error("[ASSEMBLY_TARGET_CONFLICT] 作品装配：目标绑定已发生变化。建议：核对当前版本后手动选择要使用的版本。");
      }
      const bindings = latest.bindings.map(item => ({ id: item.id, label: item.label,
        module: item.module.module_id === command.moduleId ? command.published! : item.module }));
      if (!targets.length) bindings.push({ id: "", label: "", module: command.published! });
      command = { ...command, applicationInput: { bindings, links: latest.links, expected_digest: latest.digest, request_key: requestKey("retry-apply") } };
      update(get, set, command);
    }
    const assembly = await putAssembly(workspaceId, command.applicationInput!);
    command = { ...command, applied: true, running: false }; update(get, set, command);
    if (ownsView()) set({ assembly, assemblyEpoch: get().assemblyEpoch + 1, busy: false, notice: "已将已发布版本用于本作品。", error: null });
    return true;
  } catch (error) {
    command = { ...command, running: false, error: publicationError(error), applicationInput: error instanceof WorkspaceApiError ? undefined : command.applicationInput };
    update(get, set, command);
    if (ownsView()) set({ busy: false, error: command.error });
    return false;
  }
}

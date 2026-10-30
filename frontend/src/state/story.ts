/**
 * 构思页的状态（1.0.1 P5）。
 *
 * 只放「当前看哪个方案、选中哪个节点、图与详情」这三件事。所有标识与修订来自服务端；
 * 写入成功后按服务端返回的修订重读，不在本地拼一份「大概是这样」的结构。
 */

import { create } from "zustand";

import {
  adoptStoryVariant,
  compareStoryVariants,
  createStoryVariant,
  listStoryVariants,
  readStoryNode,
  readStoryChange,
  readStoryVariant,
  submitStoryChange,
  undoStoryChange,
  type StoryAdoptionPlan,
  type StoryChangeRecord,
  type StoryComparison,
  type StoryGraph,
  type StoryNodeDetail,
  type StoryVariantSummary,
} from "../api/story";
import { WorkspaceApiError } from "../api/workspace";
import { errorMessage } from "../api/errors";

export function storyRequestKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
}

/** 采用落到对话或结构里的「这一次采用」摘要：冲突时保留服务端给的逐项原因。 */
export interface StoryAdoption {
  plan: StoryAdoptionPlan;
  conflict: boolean;
  /** assist 受理的操作标识；界面可据此跟随正文调整的流式与暂停。 */
  operationId?: string;
}

interface StoryState {
  workspaceId: string | null;
  /** 构思与正文共用同一个纸面：这是当前是否在看结构。 */
  open: boolean;
  /** 带进对话的结构引用：下一轮请求会带上这个方案的这一版与节点。 */
  reference: { variantId: string; revision: string; nodeIds: string[]; title: string } | null;
  variants: StoryVariantSummary[];
  primaryId: string;
  variantId: string | null;
  graph: StoryGraph | null;
  nodeId: string | null;
  detail: StoryNodeDetail | null;
  comparison: StoryComparison | null;
  adoption: StoryAdoption | null;
  lastChange: StoryChangeRecord | null;
  groupId: string | null;
  /** 图里出现过的片段名；折叠时仍保留，片段按钮不会因为当前只剩一段而消失。 */
  groups: string[];
  loading: boolean;
  busy: boolean;
  error: string | null;

  load(workspaceId: string): Promise<void>;
  refresh(workspaceId: string): Promise<void>;
  reset(): void;
  setOpen(open: boolean): void;
  referenceNode(nodeId: string): void;
  clearReference(): void;
  selectVariant(variantId: string): Promise<void>;
  selectNode(nodeId: string | null): Promise<void>;
  fold(groupId: string | null): Promise<void>;
  submit(operations: Record<string, unknown>[], note?: string): Promise<boolean>;
  fork(name: string): Promise<string | null>;
  compareWithPrimary(): Promise<void>;
  adopt(nodeIds?: string[], replace?: boolean, mode?: "exact" | "assist"): Promise<boolean>;
  undo(changeId?: string): Promise<boolean>;
  dismissError(): void;
}

/** 写入后统一按服务端修订重读：图、方案列表与详情都换成本次返回的版本。 */
function groupsOf(graph: StoryGraph): string[] {
  return [...new Set(graph.nodes.map((node) => node.group_id).filter((id): id is string => Boolean(id)))].sort();
}

/** 较长的已分片结构先显示一个片段，作者仍可显式查看全部。 */
async function initialGraph(workspaceId: string, variantId: string) {
  const full = await readStoryVariant(workspaceId, variantId);
  const groups = groupsOf(full);
  const groupId = full.nodes.length > 60 && groups.length > 1 ? groups[0] : null;
  if (!groupId) return { graph: full, groups, groupId };
  const graph = await readStoryVariant(workspaceId, variantId, groupId);
  if (graph.revision !== full.revision) {
    throw new Error("[STORY_REVISION_CONFLICT] 构思：读取期间结构发生了变化。建议：重新打开构思。");
  }
  return { graph, groups, groupId };
}

async function reload(
  workspaceId: string,
  variantId: string,
  nodeId: string | null,
  groupId: string | null,
): Promise<{ graph: StoryGraph; detail: StoryNodeDetail | null }> {
  const graph = await readStoryVariant(workspaceId, variantId, groupId ?? undefined);
  const detail = nodeId ? await readStoryNode(workspaceId, variantId, nodeId) : null;
  return { graph, detail };
}

/** 刷新序号：并发刷新只让最新一次落地，旧快照不能覆盖新结果。 */
let refreshEpoch = 0;

export const useStoryStore = create<StoryState>()((set, get) => ({
  workspaceId: null,
  open: false,
  reference: null,
  variants: [],
  primaryId: "",
  variantId: null,
  graph: null,
  nodeId: null,
  detail: null,
  comparison: null,
  adoption: null,
  lastChange: null,
  groupId: null,
  groups: [],
  loading: false,
  busy: false,
  error: null,

  reset() {
    refreshEpoch += 1;
    set({
      workspaceId: null,
      adoption: null,
      reference: null,
      variants: [],
      primaryId: "",
      variantId: null,
      graph: null,
      nodeId: null,
      detail: null,
      comparison: null,
      lastChange: null,
      groupId: null,
      groups: [],
      loading: false,
      busy: false,
      error: null,
    });
  },

  setOpen(open) {
    set({ open, error: null });
  },

  referenceNode(nodeId) {
    const { graph, nodeId: selected, variantId } = get();
    if (!graph || !variantId) return;
    const node = graph.nodes.find((item) => item.id === (nodeId || selected));
    if (!node) return;
    set({
      reference: {
        variantId,
        revision: graph.revision,
        nodeIds: [node.id],
        title: node.title,
      },
    });
  },

  clearReference() {
    set({ reference: null });
  },

  async adopt(nodeIds = [], replace = false, mode: "exact" | "assist" = "exact") {
    const { workspaceId, variantId, primaryId, graph, variants } = get();
    if (!workspaceId || !variantId || !graph) return false;
    if (variantId === primaryId) {
      set({ error: "[STORY_ADOPTION_INVALID] 采用：当前已经是主方案。建议：切到备选方案再采用。" });
      return false;
    }
    const primary = variants.find((item) => item.id === primaryId);
    if (!primary) {
      set({ error: "[STORY_ADOPTION_INVALID] 采用：找不到主方案。建议：刷新构思后重试。" });
      return false;
    }
    set({ busy: true, error: null, adoption: null });
    try {
      const result = await adoptStoryVariant(workspaceId, {
        source_variant_id: variantId,
        source_revision: graph.revision,
        target_variant_id: primaryId,
        target_revision: primary.revision,
        node_ids: nodeIds,
        mode,
        replace,
        request_key: storyRequestKey("story-adopt"),
      });
      const list = await listStoryVariants(workspaceId);
      if (get().workspaceId !== workspaceId) return false;
      set({
        variants: list.variants,
        adoption: {
          plan: result.plan,
          conflict: false,
          operationId: result.operation_id || undefined,
        },
        lastChange: result.receipt?.change_id
          ? {
              change_id: result.receipt.change_id,
              variant_id: result.receipt.variant_id,
              base_revision: result.receipt.base_revision,
              revision: result.receipt.revision,
              created_at: "",
              operations: [],
              changed: result.receipt.changed,
              undone_by: null,
            }
          : get().lastChange,
        busy: false,
      });
      return true;
    } catch (error) {
      // 冲突时服务端把计划放在 details 里：逐项展示，不吞掉原因。
      const details =
        error instanceof WorkspaceApiError ? error.details : {};
      const plan = details as unknown as StoryAdoptionPlan;
      set({
        busy: false,
        adoption: plan && Array.isArray(plan.conflicts) ? { plan, conflict: true } : null,
        error: errorMessage(error, "采用备选"),
      });
      return false;
    }
  },

  async load(workspaceId) {
    if (get().workspaceId !== workspaceId) {
      set({ workspaceId, variants: [], variantId: null, graph: null, detail: null, nodeId: null });
    }
    set({ loading: true, error: null });
    try {
      const list = await listStoryVariants(workspaceId);
      const variantId =
        get().variantId && list.variants.some((item) => item.id === get().variantId)
          ? get().variantId
          : list.primary_variant_id || list.variants[0]?.id || null;
      const initial = variantId ? await initialGraph(workspaceId, variantId) : null;
      const lastChange = initial?.graph.latest_change_id ? await readStoryChange(workspaceId, initial.graph.latest_change_id) : null;
      // 迟到的响应不能覆盖已经切走的作品。
      if (get().workspaceId !== workspaceId) return;
      set({
        variants: list.variants,
        primaryId: list.primary_variant_id,
        lastChange,
        variantId,
        graph: initial?.graph ?? null,
        groups: initial?.groups ?? [],
        groupId: initial?.groupId ?? null,
        detail: null,
        nodeId: null,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: errorMessage(error, "读取构思") });
    }
  },

  async refresh(workspaceId) {
    const epoch = ++refreshEpoch;
    const before = get();
    if (before.workspaceId !== workspaceId) return;
    const beforeRevision = before.graph?.revision ?? "";
    try {
      const list = await listStoryVariants(workspaceId);
      const variantId = before.variantId && list.variants.some(item => item.id === before.variantId)
        ? before.variantId : list.primary_variant_id || list.variants[0]?.id || null;
      const graph = variantId ? await readStoryVariant(workspaceId, variantId, before.groupId ?? undefined) : null;
      const lastChange = graph?.latest_change_id ? await readStoryChange(workspaceId, graph.latest_change_id) : null;
      const detail = variantId && before.nodeId && graph?.nodes.some(node => node.id === before.nodeId)
        ? await readStoryNode(workspaceId, variantId, before.nodeId) : null;
      if (epoch !== refreshEpoch) return;
      const current = get();
      if (current.workspaceId !== workspaceId || current.variantId !== before.variantId
        || current.nodeId !== before.nodeId || current.groupId !== before.groupId) return;
      // 期间已有别的写入（提交、撤销、切换）落地：旧刷新结果不再覆盖它。
      const currentRevision = current.graph?.revision ?? "";
      if (currentRevision !== beforeRevision && currentRevision !== (graph?.revision ?? "")) return;
      set({ variants: list.variants, primaryId: list.primary_variant_id, variantId, graph, lastChange,
        detail, nodeId: detail ? before.nodeId : null,
        groups: before.groupId ? before.groups : graph ? groupsOf(graph) : [], error: null });
    } catch (cause) {
      if (epoch !== refreshEpoch) return;
      if (get().workspaceId === workspaceId && get().variantId === before.variantId) set({ error: errorMessage(cause, "刷新构思") });
    }
  },

  async selectVariant(variantId) {
    const workspaceId = get().workspaceId;
    if (!workspaceId || variantId === get().variantId) return;
    set({ loading: true, error: null, comparison: null });
    try {
      const { graph, groups, groupId } = await initialGraph(workspaceId, variantId);
      const lastChange = graph.latest_change_id ? await readStoryChange(workspaceId, graph.latest_change_id) : null;
      if (get().workspaceId !== workspaceId) return;
      set({
        variantId,
        lastChange,
        graph,
        groups,
        nodeId: null,
        detail: null,
        groupId,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: errorMessage(error, "切换方案") });
    }
  },

  async selectNode(nodeId) {
    const { workspaceId, variantId } = get();
    if (!workspaceId || !variantId) return;
    if (!nodeId) {
      set({ nodeId: null, detail: null });
      return;
    }
    set({ nodeId });
    try {
      const detail = await readStoryNode(workspaceId, variantId, nodeId);
      if (get().nodeId !== nodeId) return;
      set({ detail });
    } catch (error) {
      set({ error: errorMessage(error, "读取节点") });
    }
  },

  async fold(groupId) {
    const { workspaceId, variantId } = get();
    if (!workspaceId || !variantId) return;
    set({ loading: true, error: null });
    try {
      const { graph, detail } = await reload(workspaceId, variantId, get().nodeId, groupId);
      if (get().workspaceId !== workspaceId) return;
      // 只在看全部时刷新片段列表：片段视图里没有别的片段，用它覆盖会把按钮清空。
      set({
        groupId,
        graph,
        detail,
        groups: groupId === null ? groupsOf(graph) : get().groups,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: errorMessage(error, "折叠片段") });
    }
  },

  async submit(operations, note = "") {
    const { workspaceId, variantId, graph, groupId, nodeId } = get();
    if (!workspaceId || !variantId || !graph) return false;
    set({ busy: true, error: null });
    try {
      const receipt = await submitStoryChange(workspaceId, {
        variant_id: variantId,
        expected_revision: graph.revision,
        request_key: storyRequestKey("story-change"),
        operations,
        note,
      });
      const { graph: nextGraph, detail } = await reload(workspaceId, variantId, nodeId, groupId);
      const list = await listStoryVariants(workspaceId);
      if (get().workspaceId !== workspaceId) return false;
      set({
        graph: nextGraph,
        detail,
        groups: groupId === null ? groupsOf(nextGraph) : get().groups,
        variants: list.variants,
        lastChange: {
          change_id: receipt.change_id,
          variant_id: receipt.variant_id,
          base_revision: receipt.base_revision,
          revision: receipt.revision,
          created_at: "",
          operations,
          changed: receipt.changed,
          undone_by: null,
        },
        busy: false,
      });
      return true;
    } catch (error) {
      set({ busy: false, error: errorMessage(error, "保存构思") });
      return false;
    }
  },

  async fork(name) {
    const { workspaceId, variantId, graph } = get();
    if (!workspaceId || !variantId || !graph) return null;
    set({ busy: true, error: null });
    try {
      const receipt = await createStoryVariant(workspaceId, {
        name,
        source_variant_id: variantId,
        source_revision: graph.revision,
        request_key: storyRequestKey("story-variant"),
      });
      await get().load(workspaceId);
      const created = String(receipt.result["variant_id"] ?? "");
      if (created) await get().selectVariant(created);
      return created || null;
    } catch (error) {
      set({ busy: false, error: errorMessage(error, "建立备选") });
      return null;
    } finally {
      set({ busy: false });
    }
  },

  async compareWithPrimary() {
    const { workspaceId, variantId, primaryId } = get();
    if (!workspaceId || !variantId || !primaryId || variantId === primaryId) return;
    set({ error: null });
    try {
      const comparison = await compareStoryVariants(workspaceId, primaryId, variantId);
      set({ comparison });
    } catch (error) {
      set({ error: errorMessage(error, "比较方案") });
    }
  },

  async undo(changeId) {
    const { workspaceId, variantId, groupId, nodeId, lastChange } = get();
    const target = changeId ?? lastChange?.change_id ?? "";
    if (!workspaceId || !variantId || !target) return false;
    set({ busy: true, error: null });
    try {
      await undoStoryChange(workspaceId, target, storyRequestKey("story-undo"));
      const { graph, detail } = await reload(workspaceId, variantId, nodeId, groupId);
      const list = await listStoryVariants(workspaceId);
      if (get().workspaceId !== workspaceId) return false;
      set({
        graph,
        detail,
        groups: groupId === null ? groupsOf(graph) : get().groups,
        variants: list.variants,
        lastChange: null,
        comparison: null,
        busy: false,
      });
      return true;
    } catch (error) {
      set({ busy: false, error: errorMessage(error, "撤销改动") });
      return false;
    }
  },

  dismissError() {
    set({ error: null });
  },
}));

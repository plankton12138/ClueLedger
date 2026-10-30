/**
 * 构思页的状态（1.0.1 P5）。
 *
 * 只放「当前看哪个方案、选中哪个节点、图与详情」这三件事。所有标识与修订来自服务端；
 * 写入成功后按服务端返回的修订重读，不在本地拼一份「大概是这样」的结构。
 */

import { create } from "zustand";

import {
  listStoryVariants,
  readStoryNode,
  readStoryChange,
  readStoryVariant,
  submitStoryChange,
  undoStoryChange,
  type StoryChangeRecord,
  type StoryGraph,
  type StoryNodeDetail,
  type StoryVariantSummary,
} from "../api/story";
import { errorMessage } from "../api/errors";

function storyRequestKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`;
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
  lastChange: StoryChangeRecord | null;
  loading: boolean;
  busy: boolean;
  error: string | null;

  load(workspaceId: string): Promise<void>;
  refresh(workspaceId: string): Promise<void>;
  reset(): void;
  setOpen(open: boolean): void;
  referenceNode(nodeId: string): void;
  clearReference(): void;
  selectNode(nodeId: string | null): Promise<void>;
  submit(operations: Record<string, unknown>[], note?: string): Promise<boolean>;
  undo(changeId?: string): Promise<boolean>;
  dismissError(): void;
}

/** 写入后统一按服务端修订重读：图、方案列表与详情都换成本次返回的版本。 */
async function initialGraph(workspaceId: string, variantId: string) {
  return readStoryVariant(workspaceId, variantId);
}

async function reload(
  workspaceId: string,
  variantId: string,
  nodeId: string | null,
): Promise<{ graph: StoryGraph; detail: StoryNodeDetail | null }> {
  const graph = await readStoryVariant(workspaceId, variantId);
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
  lastChange: null,
  loading: false,
  busy: false,
  error: null,

  reset() {
    refreshEpoch += 1;
    set({
      workspaceId: null,

      reference: null,
      variants: [],
      primaryId: "",
      variantId: null,
      graph: null,
      nodeId: null,
      detail: null,

      lastChange: null,
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

  async load(workspaceId) {
    if (get().workspaceId !== workspaceId) {
      set({ workspaceId, variants: [], variantId: null, graph: null, detail: null, nodeId: null });
    }
    set({ loading: true, error: null });
    try {
      const list = await listStoryVariants(workspaceId);
      const variantId = list.primary_variant_id || null;
      const initial = variantId ? await initialGraph(workspaceId, variantId) : null;
      const lastChange = initial?.latest_change_id ? await readStoryChange(workspaceId, initial.latest_change_id) : null;
      // 迟到的响应不能覆盖已经切走的作品。
      if (get().workspaceId !== workspaceId) return;
      set({
        variants: list.variants,
        primaryId: list.primary_variant_id,
        lastChange,
        variantId,
        graph: initial ?? null,
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
      const variantId = list.primary_variant_id || null;
      const graph = variantId ? await readStoryVariant(workspaceId, variantId) : null;
      const lastChange = graph?.latest_change_id ? await readStoryChange(workspaceId, graph.latest_change_id) : null;
      const detail = variantId && before.nodeId && graph?.nodes.some(node => node.id === before.nodeId)
        ? await readStoryNode(workspaceId, variantId, before.nodeId) : null;
      if (epoch !== refreshEpoch) return;
      const current = get();
      if (current.workspaceId !== workspaceId || current.variantId !== before.variantId
        || current.nodeId !== before.nodeId) return;
      // 期间已有别的写入（提交、撤销、切换）落地：旧刷新结果不再覆盖它。
      const currentRevision = current.graph?.revision ?? "";
      if (currentRevision !== beforeRevision && currentRevision !== (graph?.revision ?? "")) return;
      set({ variants: list.variants, primaryId: list.primary_variant_id, variantId, graph, lastChange,
        detail, nodeId: detail ? before.nodeId : null, error: null });
    } catch (cause) {
      if (epoch !== refreshEpoch) return;
      if (get().workspaceId === workspaceId && get().variantId === before.variantId) set({ error: errorMessage(cause, "刷新构思") });
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

  async submit(operations, note = "") {
    const { workspaceId, variantId, graph, nodeId } = get();
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
      const { graph: nextGraph, detail } = await reload(workspaceId, variantId, nodeId);
      const list = await listStoryVariants(workspaceId);
      if (get().workspaceId !== workspaceId) return false;
      set({
        graph: nextGraph,
        detail,
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

  async undo(changeId) {
    const { workspaceId, variantId, nodeId, lastChange } = get();
    const target = changeId ?? lastChange?.change_id ?? "";
    if (!workspaceId || !variantId || !target) return false;
    set({ busy: true, error: null });
    try {
      await undoStoryChange(workspaceId, target, storyRequestKey("story-undo"));
      const { graph, detail } = await reload(workspaceId, variantId, nodeId);
      const list = await listStoryVariants(workspaceId);
      if (get().workspaceId !== workspaceId) return false;
      set({
        graph,
        detail,
        variants: list.variants,
        lastChange: null,

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

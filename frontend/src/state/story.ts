/**
 * 构思、时间轴与三状态的作品状态。
 *
 * 只放「当前看哪个方案、选中哪个节点或线索、服务端返回的投影」。所有标识与修订来自
 * 服务端；写入成功后按服务端返回的修订重读，不在本地拼一份「大概是这样」的结构。
 */

import { create } from "zustand";

import {
  listStoryVariants,
  readClueRelations,
  readStoryAttention,
  readStoryChange,
  readStoryTimeline,
  readStoryVariant,
  undoStoryChange,
  type ClueRelations,
  type StoryAttention,
  type StoryChangeRecord,
  type StoryGraph,
  type StoryTimeline,
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
  variants: StoryVariantSummary[];
  primaryId: string;
  variantId: string | null;
  graph: StoryGraph | null;
  attention: StoryAttention | null;
  timeline: StoryTimeline | null;
  relations: ClueRelations | null;
  nodeId: string | null;
  clueId: string | null;
  lastChange: StoryChangeRecord | null;
  /** 结构写入后的失效序号：详情与本章相关据此重读，不依赖页面是否停在构思。 */
  invalidation: number;
  loading: boolean;
  busy: boolean;
  error: string | null;

  load(workspaceId: string): Promise<void>;
  invalidate(): void;
  refresh(workspaceId: string): Promise<void>;
  reset(): void;
  setOpen(open: boolean): void;
  selectNode(nodeId: string | null): void;
  selectClue(clueId: string | null): void;
  loadRelations(workspaceId: string, focusClueId: string | null): Promise<void>;
  undo(changeId?: string): Promise<boolean>;
  dismissError(): void;
}

async function readProjections(workspaceId: string, variantId: string) {
  const [graph, attention, timeline] = await Promise.all([
    readStoryVariant(workspaceId, variantId),
    readStoryAttention(workspaceId, variantId).catch(() => null),
    readStoryTimeline(workspaceId, variantId).catch(() => null),
  ]);
  return { graph, attention, timeline };
}

/** 刷新序号：并发刷新只让最新一次落地，旧快照不能覆盖新结果。 */
let refreshEpoch = 0;
let loadEpoch = 0;

export const useStoryStore = create<StoryState>()((set, get) => ({
  workspaceId: null,
  open: false,
  variants: [],
  primaryId: "",
  variantId: null,
  graph: null,
  attention: null,
  timeline: null,
  relations: null,
  nodeId: null,
  clueId: null,
  lastChange: null,
  invalidation: 0,
  loading: false,
  busy: false,
  error: null,

  invalidate() {
    set(state => ({ invalidation: state.invalidation + 1 }));
  },

  reset() {
    refreshEpoch += 1;
    loadEpoch += 1;
    set({
      workspaceId: null,

      variants: [],
      primaryId: "",
      variantId: null,
      graph: null,
      attention: null,
      timeline: null,
      relations: null,
      nodeId: null,
      clueId: null,

      lastChange: null,
      loading: false,
      busy: false,
      error: null,
    });
  },

  setOpen(open) {
    set({ open, error: null });
  },

  async load(workspaceId) {
    const epoch = ++loadEpoch;
    ++refreshEpoch;
    if (get().workspaceId !== workspaceId) {
      set({
        workspaceId,
        variants: [],
        variantId: null,
        graph: null,
        attention: null,
        timeline: null,
        relations: null,
        nodeId: null,
        clueId: null,
      });
    }
    set({ loading: true, error: null });
    try {
      const list = await listStoryVariants(workspaceId);
      const variantId = list.primary_variant_id || null;
      const projections = variantId
        ? await readProjections(workspaceId, variantId)
        : { graph: null, attention: null, timeline: null };
      const lastChange = projections.graph?.latest_change_id
        ? await readStoryChange(workspaceId, projections.graph.latest_change_id)
        : null;
      // 迟到的响应不能覆盖已经切走的作品。
      if (get().workspaceId !== workspaceId || epoch !== loadEpoch) return;
      set({
        variants: list.variants,
        primaryId: list.primary_variant_id,
        lastChange,
        variantId,
        graph: projections.graph,
        attention: projections.attention,
        timeline: projections.timeline,
        relations: null,
        // 选中的节点与线索由路由决定；重新读取同一作品不能清掉刚做的选择。
        loading: false,
      });
    } catch (error) {
      if (get().workspaceId !== workspaceId || epoch !== loadEpoch) return;
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
      const projections = variantId
        ? await readProjections(workspaceId, variantId)
        : { graph: null, attention: null, timeline: null };
      const lastChange = projections.graph?.latest_change_id
        ? await readStoryChange(workspaceId, projections.graph.latest_change_id)
        : null;
      if (epoch !== refreshEpoch) return;
      const current = get();
      if (
        current.workspaceId !== workspaceId ||
        current.variantId !== before.variantId ||
        current.nodeId !== before.nodeId
      )
        return;
      // 期间已有别的写入（提交、撤销、切换）落地：旧刷新结果不再覆盖它。
      const currentRevision = current.graph?.revision ?? "";
      if (currentRevision !== beforeRevision && currentRevision !== (projections.graph?.revision ?? ""))
        return;
      set({
        variants: list.variants,
        primaryId: list.primary_variant_id,
        variantId,
        graph: projections.graph,
        attention: projections.attention,
        timeline: projections.timeline,
        lastChange,
        nodeId:
          before.nodeId && projections.graph?.nodes.some(node => node.id === before.nodeId)
            ? before.nodeId
            : null,
        error: null,
      });
    } catch (cause) {
      if (epoch !== refreshEpoch) return;
      if (get().workspaceId === workspaceId && get().variantId === before.variantId)
        set({ error: errorMessage(cause, "刷新构思") });
    }
  },

  selectNode(nodeId) {
    set({ nodeId });
  },

  selectClue(clueId) {
    set({ clueId });
  },

  async loadRelations(workspaceId, focusClueId) {
    const variantId = get().variantId;
    if (!variantId) return;
    try {
      const relations = await readClueRelations(workspaceId, variantId, focusClueId);
      if (get().workspaceId !== workspaceId || get().variantId !== variantId) return;
      set({ relations });
    } catch (cause) {
      if (get().workspaceId === workspaceId)
        set({ error: errorMessage(cause, "读取线索关系") });
    }
  },

  async undo(changeId) {
    const { workspaceId, variantId, lastChange } = get();
    const target = changeId ?? lastChange?.change_id ?? "";
    if (!workspaceId || !variantId || !target) return false;
    set({ busy: true, error: null });
    try {
      await undoStoryChange(workspaceId, target, storyRequestKey("story-undo"));
      const [list, projections] = await Promise.all([
        listStoryVariants(workspaceId),
        readProjections(workspaceId, variantId),
      ]);
      if (get().workspaceId !== workspaceId) return false;
      set({
        graph: projections.graph,
        attention: projections.attention,
        timeline: projections.timeline,
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

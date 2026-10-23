/**
 * 故事资料的数据状态（0.7）。
 *
 * 按「作品 + 资料域 + 截止章 + 查询」缓存。迟到的响应只写回它自己的键，不覆盖作者
 * 刚切换的时点或资料页；切作品时整份清空，避免把 A 的旧结果写进 B。
 */

import { create } from "zustand";

import {
  fetchMaterials,
  type MaterialDomain,
  type StoryMaterials,
} from "../api/storyMaterials";
import { WorkspaceApiError } from "../api/workspace";

interface StoryMaterialsStore {
  workspaceId: string | null;
  cache: Record<string, StoryMaterials>;
  loading: Record<string, boolean>;
  error: string | null;

  load(
    workspaceId: string,
    domain: MaterialDomain,
    options?: { asOfChapter?: number | null; query?: string; refresh?: boolean },
  ): Promise<void>;
  reset(workspaceId: string | null): void;
  clearError(): void;
}

export function materialsKey(
  domain: MaterialDomain,
  asOfChapter: number | null | undefined,
  query = "",
): string {
  return `${domain}|${asOfChapter ?? ""}|${query}`;
}

function describe(error: unknown): string {
  if (error instanceof WorkspaceApiError) return `${error.why}。${error.fix}`;
  return error instanceof Error ? error.message : "资料读取失败";
}

const inFlight = new Map<string, Promise<void>>();

export const useStoryMaterialsStore = create<StoryMaterialsStore>()((set, get) => ({
  workspaceId: null,
  cache: {},
  loading: {},
  error: null,

  async load(workspaceId, domain, options = {}) {
    const { asOfChapter = null, query = "", refresh = false } = options;
    const key = materialsKey(domain, asOfChapter, query);
    const state = get();
    if (state.workspaceId !== workspaceId) {
      set({ workspaceId, cache: {}, loading: {}, error: null });
    } else if (state.cache[key] && !refresh) {
      return;
    }
    const flightKey = `${workspaceId}|${key}`;
    const existing = inFlight.get(flightKey);
    if (existing) return existing;
    const task = (async () => {
      set((current) => ({ loading: { ...current.loading, [key]: true } }));
      try {
        const data = await fetchMaterials(workspaceId, domain, { asOfChapter, query });
        // 迟到的响应：作品已经切换时丢弃，不把旧作品的结果写进当前页。
        if (get().workspaceId !== workspaceId) return;
        set((current) => ({
          cache: { ...current.cache, [key]: data },
          loading: { ...current.loading, [key]: false },
        }));
      } catch (error) {
        if (get().workspaceId !== workspaceId) return;
        set((current) => ({
          loading: { ...current.loading, [key]: false },
          error: describe(error),
        }));
      } finally {
        inFlight.delete(flightKey);
      }
    })();
    inFlight.set(flightKey, task);
    return task;
  },

  reset(workspaceId) {
    inFlight.clear();
    set({ workspaceId, cache: {}, loading: {}, error: null });
  },

  clearError() {
    set({ error: null });
  },
}));

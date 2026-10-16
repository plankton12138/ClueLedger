import { create } from "zustand";
import {
  fetchModelSelection,
  selectModel,
  type ModelSelection,
} from "../api/models";

export const useModelStore = create<{
  current: ModelSelection | null;
  error: string;
  refresh(): Promise<void>;
  select(provider: string, model: string): Promise<void>;
}>()((set) => {
  let generation = 0;
  let selecting = false;
  return {
    current: null,
    error: "",
    refresh: async () => {
      if (selecting) return;
      const epoch = ++generation;
      try {
        const current = await fetchModelSelection();
        if (epoch === generation) set({ current, error: "" });
      } catch (e) {
        if (epoch === generation)
          set({ error: e instanceof Error ? e.message : String(e) });
      }
    },
    select: async (provider, model) => {
      const epoch = ++generation;
      selecting = true;
      try {
        const current = await selectModel(provider, model);
        if (epoch === generation) set({ current, error: "" });
      } finally {
        if (epoch === generation) selecting = false;
      }
    },
  };
});

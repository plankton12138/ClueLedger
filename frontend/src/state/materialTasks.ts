import { errorMessage } from "../api/errors";
/**
 * 资料任务状态（0.7）。
 *
 * 提交范围、观察进度、停止与恢复都在这里，不经过写作会话的 `send()`：写作会话的
 * 发送会清空草稿并切换选中会话，资料整理不能顺手改掉作者正在写的东西。
 *
 * 进度只认服务端事件：`material_target_published` 记一个目标已完成，`run_finished`
 * 之后以任务详情为准（包含未完成项）。请求键在得到受理前一直复用，避免响应丢失时
 * 重复建会话或重复调用模型。
 */

import { create } from "zustand";

import {
  fetchMaterialTask,
  listMaterialTasks,
  startMaterialTask,
  stopMaterialTask,
  type MaterialDomain,
  type MaterialScope,
  type MaterialTask,
} from "../api/storyMaterials";
import { streamEvents, WorkspaceApiError } from "../api/workspace";

interface PendingKey {
  signature: string;
  key: string;
}

interface MaterialTasksStore {
  session: number;
  workspaceId: string | null;
  tasks: Record<string, MaterialTask>;
  /** 按最近开始时间排列的任务 ID。 */
  order: string[];
  /** 每个资料域当前仍在运行的任务。 */
  active: Record<string, string>;
  /** 每次任务结束或列表刷新时递增，页面据此重读资料。 */
  revision: number;
  error: string | null;
  pendingKey: PendingKey | null;

  load(workspaceId: string, domain?: MaterialDomain): Promise<void>;
  start(workspaceId: string, scope: MaterialScope, label: string): Promise<MaterialTask | null>;
  observe(workspaceId: string, conversationId: string, turnId: string): Promise<void>;
  stop(workspaceId: string, turnId: string): Promise<void>;
  reset(workspaceId: string | null): void;
  clearError(): void;
}

function describe(error: unknown): string {
  return errorMessage(error, "资料整理");
}

function newKey(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `k_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`;
}

const generations = new Map<string, number>();

export function scopeSignature(scope: MaterialScope): string {
  return JSON.stringify([
    scope.domain,
    [...scope.chapter_paths],
    [...scope.entity_paths],
    scope.as_of_chapter,
    Object.entries(scope.observed_digests).sort(([a], [b]) => a.localeCompare(b)),
  ]);
}

export const useMaterialTasksStore = create<MaterialTasksStore>()((set, get) => ({
  session: 0, workspaceId: null,
  tasks: {},
  order: [],
  active: {},
  revision: 0,
  error: null,
  pendingKey: null,

  async load(workspaceId, domain) {
    if (get().workspaceId !== workspaceId) {
      set({
        session: get().session + 1, workspaceId,
        tasks: {},
        order: [],
        active: {},
        revision: 0,
        error: null,
        pendingKey: null,
      });
    }
    const session = get().session;
    try {
      const { items } = await listMaterialTasks(workspaceId, domain);
      if (get().workspaceId !== workspaceId || get().session !== session) return;
      set((state) => {
        const tasks = { ...state.tasks };
        const active = { ...state.active };
        for (const task of items) {
          tasks[task.turn_id] = task;
          if (task.status === "accepted" || task.status === "running") {
            active[task.domain] = task.turn_id;
          } else if (active[task.domain] === task.turn_id) {
            delete active[task.domain];
          }
        }
        const order = [
          ...items.map((item) => item.turn_id),
          ...state.order.filter((id) => !items.some((item) => item.turn_id === id)),
        ];
        return { tasks, active, order, revision: state.revision + 1 };
      });
      // 刷新页面后仍在运行的任务要重新接上事件；重复订阅由代次校验收口。
      for (const task of items) {
        if (task.status === "accepted" || task.status === "running") {
          void get().observe(workspaceId, task.conversation_id, task.turn_id);
        }
      }
    } catch (error) {
      if (get().workspaceId !== workspaceId || get().session !== session) return;
      set({ error: describe(error) });
    }
  },

  async start(workspaceId, scope, label) {
    if (get().workspaceId !== workspaceId) return null;
    const session = get().session;
    scope = structuredClone(scope);
    const signature = scopeSignature(scope);
    const pending = get().pendingKey;
    const key = pending?.signature === signature ? pending.key : newKey();
    set({ pendingKey: { signature, key }, error: null });
    try {
      const accepted = await startMaterialTask(workspaceId, { requestKey: key, scope });
      if (get().session !== session || get().workspaceId !== workspaceId) return null;
      set((state) => {
        const total = scope.entity_paths.length
          ? scope.entity_paths.length * scope.chapter_paths.length
          : scope.chapter_paths.length;
        const task: MaterialTask = {
          turn_id: accepted.turn_id,
          conversation_id: accepted.conversation_id,
          domain: accepted.domain,
          label: accepted.label || label,
          scope: accepted.scope,
          status: "accepted",
          model: accepted.model,
          started_at: new Date().toISOString(),
          published: 0,
          done: 0,
          total,
          targets: [],
          unfinished: [],
        };
        return {
          pendingKey: null,
          tasks: { ...state.tasks, [task.turn_id]: task },
          order: [task.turn_id, ...state.order.filter((id) => id !== task.turn_id)],
          active: { ...state.active, [task.domain]: task.turn_id },
        };
      });
      void get().observe(workspaceId, accepted.conversation_id, accepted.turn_id);
      return get().tasks[accepted.turn_id] ?? null;
    } catch (error) {
      if (get().session !== session || get().workspaceId !== workspaceId) return null;
      // 明确拒绝（4xx）说明这次请求不会被受理：换一次操作重新生成键。
      if (error instanceof WorkspaceApiError && error.status >= 400 && error.status < 500) {
        set({ pendingKey: null });
      }
      set({ error: describe(error) });
      return null;
    }
  },

  async observe(workspaceId, conversationId, turnId) {
    const session = get().session;
    const generation = (generations.get(turnId) ?? 0) + 1;
    generations.set(turnId, generation);
    const seen = new Set<string>();
    try {
      for await (const event of streamEvents(workspaceId, conversationId, 0)) {
        if (generations.get(turnId) !== generation) return;
        if (get().workspaceId !== workspaceId || get().session !== session) return;
        if (event.type === "material_target_published") {
          const marker = `${event.path}\u0000${event.entity}`;
          if (seen.has(marker)) continue;
          seen.add(marker);
          set((state) => {
            const task = state.tasks[turnId];
            if (!task) return state;
            return {
              tasks: {
                ...state.tasks,
                [turnId]: {
                  ...task,
                  status: "running",
                  published: task.published + 1,
                  done: Math.min(task.done + 1, task.total),
                },
              },
            };
          });
        } else if (event.type === "run_finished") {
          const detail = await fetchMaterialTask(workspaceId, turnId).catch(() => null);
          if (get().session !== session || generations.get(turnId) !== generation) return;
          set((state) => {
            const task = detail ?? state.tasks[turnId];
            if (!task) return state;
            const active = { ...state.active };
            if (active[task.domain] === turnId) delete active[task.domain];
            return {
              tasks: { ...state.tasks, [turnId]: task },
              active,
              revision: state.revision + 1,
            };
          });
          return;
        }
      }
    } catch (error) {
      if (generations.get(turnId) !== generation || get().session !== session) return;
      // 连接断开不等于任务失败：保留运行状态，页面可重新读取任务列表。
      set({ error: describe(error) });
    }
  },

  async stop(workspaceId, turnId) {
    const session = get().session;
    try {
      await stopMaterialTask(workspaceId, turnId);
      if (get().session !== session || get().workspaceId !== workspaceId) return;
      set((state) => ({
        tasks: state.tasks[turnId]
          ? { ...state.tasks, [turnId]: { ...state.tasks[turnId], status: "stopped" } }
          : state.tasks,
      }));
    } catch (error) {
      if (get().session === session && get().workspaceId === workspaceId) set({ error: describe(error) });
    }
  },

  reset(workspaceId) {
    generations.clear();
    set({
      session: get().session + 1, workspaceId,
      tasks: {},
      order: [],
      active: {},
      revision: 0,
      error: null,
      pendingKey: null,
    });
  },

  clearError() {
    set({ error: null });
  },
}));

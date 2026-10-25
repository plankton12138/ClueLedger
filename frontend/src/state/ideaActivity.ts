/**
 * 灵感使用记录（0.8 I5）。
 *
 * 三件事分开显示：在哪次讨论里被带上、哪次对话里展开过、有没有真的写进正文。
 * 服务端按受理事件回放建索引；没扫完时这一页本身还在长，服务端因此不发游标。
 * 客户端在「还没整理完」的阶段重复读第一页并按标识合并，扫完才按游标往下翻——
 * 否则作者会看到一页跳来跳去、甚至漏掉刚扫出来的记录。
 */

import { create } from "zustand";

import { errorMessage } from "../api/errors";
import { listIdeaActivity, type IdeaActivity } from "../api/ideas";

export interface IdeaActivityState {
  workspaceId: string | null;
  ideaId: string | null;
  items: IdeaActivity[];
  /** 服务端给出的总条数：可能多于已读到的这一页。 */
  total: number;
  cursor: string | null;
  /** false 表示索引还在整理，此时不能断言「没有使用记录」。 */
  complete: boolean;
  loading: boolean;
  error: string | null;
  /** 读取某条灵感的一页记录；换条目时先清空，避免显示上一条的残留。 */
  load(workspaceId: string, ideaId: string): Promise<void>;
  /** 继续读取：整理中就再扫一批，整理完就按游标翻下一页。 */
  more(): Promise<void>;
  reset(): void;
}

/** 只采纳仍然是当前条目的响应：切走或重读之后，迟到的结果不串进来。 */
function isCurrent(state: IdeaActivityState, workspaceId: string, ideaId: string): boolean {
  return state.workspaceId === workspaceId && state.ideaId === ideaId;
}

export const useIdeaActivityStore = create<IdeaActivityState>()((set, get) => ({
  workspaceId: null,
  ideaId: null,
  items: [],
  total: 0,
  cursor: null,
  complete: true,
  loading: false,
  error: null,

  async load(workspaceId, ideaId) {
    const state = get();
    const same = state.workspaceId === workspaceId && state.ideaId === ideaId;
    set({
      workspaceId,
      ideaId,
      items: same ? state.items : [],
      total: same ? state.total : 0,
      cursor: same ? state.cursor : null,
      complete: same ? state.complete : true,
      loading: true,
      error: null,
    });
    try {
      const page = await listIdeaActivity(workspaceId, ideaId);
      if (!isCurrent(get(), workspaceId, ideaId)) return;
      set({
        items: page.items,
        total: page.total,
        cursor: page.next_cursor,
        complete: page.complete,
        loading: false,
      });
    } catch (caught) {
      if (!isCurrent(get(), workspaceId, ideaId)) return;
      set({ error: errorMessage(caught, "读取使用记录"), loading: false });
    }
  },

  async more() {
    const { workspaceId, ideaId, cursor, complete } = get();
    if (!workspaceId || !ideaId || get().loading) return;
    set({ loading: true, error: null });
    try {
      // 整理中：再扫一批，返回的仍是第一页，按标识合并；扫完的那一次换成完整快照。
      const page = await listIdeaActivity(workspaceId, ideaId, {
        cursor: complete ? cursor : null,
      });
      if (!isCurrent(get(), workspaceId, ideaId)) return;
      if (!complete) {
        set({
          items: page.complete
            ? page.items
            : mergeById(get().items, page.items),
          total: page.total,
          cursor: page.next_cursor,
          complete: page.complete,
          loading: false,
        });
        return;
      }
      set({
        items: mergeById(get().items, page.items),
        total: page.total,
        cursor: page.next_cursor,
        loading: false,
      });
    } catch (caught) {
      if (!isCurrent(get(), workspaceId, ideaId)) return;
      set({ error: errorMessage(caught, "读取使用记录"), loading: false });
    }
  },

  reset() {
    set({
      workspaceId: null,
      ideaId: null,
      items: [],
      total: 0,
      cursor: null,
      complete: true,
      loading: false,
      error: null,
    });
  },
}));

function mergeById(current: IdeaActivity[], incoming: IdeaActivity[]): IdeaActivity[] {
  // 后面的结果覆盖前面的同一条：刷新出来的状态（如是否仍可核对）以最新一次为准。
  return [...new Map([...current, ...incoming].map(item => [item.id, item])).values()];
}

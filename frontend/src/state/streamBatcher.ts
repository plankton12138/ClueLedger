/** 前端展示合批：保留每个事件，按时间或事件数更新一次视图；边界和断线前立即提交。 */
import type { WorkspaceEvent } from "../api/workspace";

export const STREAM_RENDER_INTERVAL = 50;
const MAX_PENDING_EVENTS = 512;

export function createStreamBatcher(publish: (events: WorkspaceEvent[]) => void) {
  let pending: WorkspaceEvent[] = [];
  let timer: ReturnType<typeof setTimeout> | null = null;

  function flush(): void {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    if (!pending.length) return;
    const events = pending;
    pending = [];
    publish(events);
  }

  return {
    push(event: WorkspaceEvent): void {
      pending.push(event);
      if (event.type !== "assistant_block_delta" || pending.length >= MAX_PENDING_EVENTS) {
        flush();
      } else if (timer === null) {
        timer = setTimeout(flush, STREAM_RENDER_INTERVAL);
      }
    },
    flush,
  };
}

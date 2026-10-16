import { useEffect, useState } from "react";
import { useV2Store } from "./v2";
import { advanceCursor } from "./v2events";
export type V2ConnStatus = "connecting" | "open" | "closed";
export function useV2Events(): V2ConnStatus {
  const pid = useV2Store((s) => s.currentId);
  const [status, setStatus] = useState<V2ConnStatus>("closed");
  useEffect(() => {
    if (!pid) {
      setStatus("closed");
      return;
    }
    let alive = true,
      socket: WebSocket | null = null,
      cursor = 0,
      attempts = 0;
    let reconnect: ReturnType<typeof setTimeout> | undefined,
      refreshTimer: ReturnType<typeof setTimeout> | undefined;
    const refresh = () => {
      if (!alive || refreshTimer) return;
      refreshTimer = setTimeout(() => {
        refreshTimer = undefined;
        if (!alive) return;
        const s = useV2Store.getState();
        void s.refreshManuscript();
        void s.refreshStory();
        void s.refreshChanges();
        void s.refreshPreferences();
        void s.refreshJobs();
      }, 180);
    };
    const connect = () => {
      if (!alive) return;
      setStatus("connecting");
      const ws = new WebSocket(
        `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/v2/${encodeURIComponent(pid)}`,
      );
      socket = ws;
      ws.onopen = () => {
        if (alive)
          ws.send(JSON.stringify({ type: "subscribe", after_seq: cursor }));
      };
      ws.onmessage = (event) => {
        if (!alive) return;
        try {
          const frame: unknown = JSON.parse(String(event.data));
          if (!frame || typeof frame !== "object" || Array.isArray(frame))
            return;
          const body = frame as Record<string, unknown>;
          if (body.type !== "replayed" && body.type !== "event") return;
          const next = advanceCursor(cursor, body);
          cursor = next.cursor;
          attempts = 0;
          setStatus("open");
          if (next.refresh || body.type === "replayed") refresh();
        } catch {
          /* 非协议帧不能改变仓库投影。 */
        }
      };
      ws.onerror = () => ws.close();
      ws.onclose = () => {
        if (!alive) return;
        setStatus("closed");
        reconnect = setTimeout(
          connect,
          Math.min(15000, 1000 * 2 ** Math.min(attempts++, 4)),
        );
      };
    };
    connect();
    return () => {
      alive = false;
      clearTimeout(reconnect);
      clearTimeout(refreshTimer);
      socket?.close();
    };
  }, [pid]);
  return pid ? status : "closed";
}

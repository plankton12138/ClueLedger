/**
 * 时间线面板（V03-7.6）：按角色/地点分轨展示真实事件，证词/认知单独成轨。
 *
 * 设计依据（产品 §5.8、计划 M12）：时间原文显示、不排序；不确定时间原样保留；
 * 真实事件与证词分开；叙述位置只来自角色获知事件，不用章节先后推断。
 * 这是文字路径，画布不是查看时间线的必要条件。
 */

import type { GraphNodeWire, TimelineGraphWire } from "../../api/graphs";

export default function TimelinePanel({
  timeline,
  nodesById,
}: {
  timeline: TimelineGraphWire;
  nodesById: Record<string, GraphNodeWire>;
}) {
  const eventsById = Object.fromEntries(
    timeline.events.map((event) => [event.event_id, event]),
  );
  const title = (id: string) => nodesById[id]?.title ?? id;

  return (
    <div className="min-h-0 flex-1 overflow-auto px-3 py-2 text-[11px]">
      <p className="text-[10px] text-sub">{timeline.ordering.note}</p>

      {timeline.tracks.length === 0 ? (
        <p className="mt-2 text-xs text-sub">
          还没有可分轨的事件。先在账本登记时间线事件及参与者/地点。
        </p>
      ) : (
        timeline.tracks.map((track) => (
          <section key={track.key} className="mt-3">
            <h4 className="text-xs font-medium">
              {track.label}{" "}
              <span className="text-[10px] text-sub">
                {track.kind === "character" ? "角色轨" : "地点轨"} · {track.event_ids.length} 个事件
              </span>
            </h4>
            <ul className="mt-1 space-y-1">
              {track.event_ids.map((eventId) => {
                const event = eventsById[eventId];
                if (!event) return null;
                return (
                  <li key={eventId} className="rounded border border-line px-2 py-1">
                    <div>{event.time_text || "时间未定"}</div>
                    <div className="text-[10px] text-sub">
                      {event.place_id ? `地点：${title(event.place_id)}` : "地点未记录"}
                      {" · "}
                      {event.participants.length > 0
                        ? `参与：${event.participants.map(title).join("、")}`
                        : "参与者未记录"}
                    </div>
                    {(event.before_state || event.after_state) && (
                      <div className="text-[10px] text-sub">
                        {event.before_state && `之前：${event.before_state}`}
                        {event.before_state && event.after_state && " · "}
                        {event.after_state && `之后：${event.after_state}`}
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}

      <section className="mt-3">
        <h4 className="text-xs font-medium">证词 / 认知</h4>
        <p className="text-[10px] text-sub">{timeline.testimony_note}</p>
        {timeline.testimonies.length === 0 ? (
          <p className="mt-1 text-[10px] text-sub">
            还没有证词或角色认知记录；未记录不等于「不知道」。
          </p>
        ) : (
          timeline.testimonies.map((track) => (
            <div key={track.character_id} className="mt-2">
              <div className="text-[11px]">{track.label}</div>
              <ul className="mt-1 space-y-1">
                {track.items.map((item) => (
                  <li
                    key={item.knowledge_id}
                    className="rounded border border-dashed border-line px-2 py-1"
                  >
                    <div>
                      <span className="text-amber-700">{item.mode_label}</span>
                      {item.note ? `：${item.note}` : ""}
                    </div>
                    <div className="text-[10px] text-sub">
                      {item.claimed_event_time
                        ? `所指事件时间：${item.claimed_event_time}（真实事件，非证词时间）`
                        : "未关联事件"}
                      {" · "}
                      {item.position === null
                        ? "获知节拍未记录"
                        : `叙述位置 #${item.position + 1}`}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))
        )}
      </section>

      {timeline.gaps.length > 0 && (
        <section className="mt-3 border-t border-line pt-2">
          <h4 className="text-xs font-medium">缺口（只表示尚未记录）</h4>
          <ul className="mt-1 space-y-0.5 text-[10px] text-amber-700">
            {timeline.gaps.slice(0, 8).map((gap) => (
              <li key={`${gap.code}:${gap.where}`}>
                {gap.why}——{gap.fix}
              </li>
            ))}
            {timeline.gaps.length > 8 && <li>另有 {timeline.gaps.length - 8} 项缺口</li>}
          </ul>
        </section>
      )}
    </div>
  );
}

/**
 * 只读故事时间轴。
 *
 * 横轴是故事内发生时间，纵向按披露章节分组。卡片只有「选中」一个动作：没有拖动、
 * 缩放或重排，编辑在详情里进行。没有数值时间的节点仍然出现在所属分组里。
 */

import { AlertCircle, Circle, CircleDot } from "lucide-react";

import type { StoryStatus, StoryTimeline, TimelineNode } from "../../api/story";
import { timeLabel, timePlacement } from "./timelinePlacement";

const STATUS_LABEL: Record<StoryStatus, string> = {
  ready: "就绪",
  draft: "草稿",
  attention: "需核对",
};

function StatusDot({ status }: { status: StoryStatus }) {
  const Icon = status === "attention" ? AlertCircle : status === "draft" ? Circle : CircleDot;
  return (
    <span className={`timeline-status ${status}`} title={STATUS_LABEL[status]}>
      <Icon size={12} strokeWidth={2} aria-hidden="true" />
      <span className="sr-only">{STATUS_LABEL[status]}</span>
    </span>
  );
}

function Row({
  node,
  domain,
  origin,
  selected,
  onSelect,
}: {
  node: TimelineNode;
  domain: StoryTimeline["domain"];
  origin: string;
  selected: boolean;
  onSelect: (nodeId: string) => void;
}) {
  const label = timeLabel(node.start_minute, node.end_minute, node.approximate, origin);
  let placement;
  try {
    placement = timePlacement(
      node.start_minute === null || node.end_minute === null
        ? null
        : { start_minute: node.start_minute, end_minute: node.end_minute },
      domain,
    );
  } catch {
    placement = { kind: "unplaced" as const };
  }
  return (
    <button
      type="button"
      className="story-timeline-row"
      aria-pressed={selected}
      aria-label={`${node.title}，${label}，${STATUS_LABEL[node.status]}`}
      draggable={false}
      onClick={() => onSelect(node.node_id)}
    >
      <span className="story-timeline-title">
        <StatusDot status={node.status} />
        {node.title}
      </span>
      <span className="story-timeline-axis" aria-hidden="true">
        {placement.kind === "point" && (
          <i className="timeline-point" style={{ left: `${placement.left}%` }} />
        )}
        {placement.kind === "range" && (
          <i
            className="timeline-range"
            style={{ left: `${placement.left}%`, width: `${placement.width}%` }}
          />
        )}
      </span>
      <span className="story-timeline-time">{label}</span>
    </button>
  );
}

export default function StoryTimeline({
  timeline,
  selectedNodeId,
  onSelectNode,
}: {
  timeline: StoryTimeline | null;
  selectedNodeId: string | null;
  onSelectNode: (nodeId: string) => void;
}) {
  if (!timeline || timeline.groups.length === 0) {
    return (
      <div className="story-timeline story-timeline-empty">
        <p>还没有构思节点。可以在对话里建立，或从正文校准。</p>
      </div>
    );
  }
  const origin = timeline.time_origin_label;
  return (
    <div className="story-timeline" role="list" aria-label="故事时间轴">
      <div className="story-timeline-header" aria-hidden="true">
        <span>构思节点</span>
        <span className="story-timeline-axis-label">
          {timeline.domain ? `${origin || "故事时间"}相对分钟` : "尚无数值时间"}
        </span>
        <span>发生时间</span>
      </div>
      {timeline.groups.map(group => (
        <section
          key={`${group.chapter_kind}:${group.chapter_number ?? "none"}`}
          className="story-timeline-group"
          role="listitem"
          aria-label={group.title}
        >
          <h2 className="story-timeline-group-title">
            {group.title}
            {group.chapter_kind === "planned" && <em>计划</em>}
            {group.chapter_kind === "unassigned" && <em>未安排</em>}
          </h2>
          {group.nodes.map(node => (
            <Row
              key={node.node_id}
              node={node}
              domain={timeline.domain}
              origin={origin}
              selected={node.node_id === selectedNodeId}
              onSelect={onSelectNode}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

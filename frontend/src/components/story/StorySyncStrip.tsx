import type { StorySync } from "../../api/story";

export type StorySyncFocus = "aligned" | "pending" | "conflict" | "outline_only" | "unbound" | "waived";

interface Props {
  sync: StorySync;
  focus: StorySyncFocus | null;
  onFocus: (focus: StorySyncFocus | null) => void;
}

export default function StorySyncStrip({ sync, focus, onFocus }: Props) {
  const counts = sync.counts;
  const conflictNodes = new Set(
    sync.nodes.filter((node) => node.state === "diverged").map((node) => node.node_id),
  );
  for (const conflict of sync.conflicts) {
    if (conflict.kind === "missing_presentation" && conflict.node_id) conflictNodes.add(conflict.node_id);
  }
  const metrics: { key: StorySyncFocus; label: string; count: number; tone: string; title: string }[] = [
    {
      key: "aligned",
      label: "已对齐",
      count: counts.aligned,
      tone: "aligned",
      title: "构思契约和正文摘要一致",
    },
    {
      key: "pending",
      label: "待同步",
      count: counts.outline_ahead + counts.prose_ahead + counts.unchecked,
      tone: "pending",
      title: `构思已改 ${counts.outline_ahead}，正文已改 ${counts.prose_ahead}，未核对 ${counts.unchecked}`,
    },
    {
      key: "conflict",
      label: "冲突",
      count: conflictNodes.size,
      tone: "conflict",
      title: "构思和正文需要重新对齐，或有线索呈现句缺失",
    },
    {
      key: "outline_only",
      label: "待写",
      count: counts.outline_only,
      tone: "",
      title: "只有构思，还没有绑定正文",
    },
    {
      key: "unbound",
      label: "未纳入构思",
      count: counts.unbound_prose,
      tone: "unbound",
      title: "有正文，但没有绑定到构思节点",
    },
  ];
  if (counts.waived > 0) {
    metrics.push({
      key: "waived",
      label: "已确认差异",
      count: counts.waived,
      tone: "waived",
      title: "作者确认暂不处理，任一侧再变化会重新提示",
    });
  }
  return (
    <div className="story-sync-strip" aria-label="全书写入状态">
      <span className="story-sync-caption">全书写入状态</span>
      {metrics.map(metric => (
        <button
          key={metric.key}
          type="button"
          className={`story-sync-metric ${metric.tone}`.trim()}
          aria-pressed={focus === metric.key}
          title={metric.title}
          disabled={metric.count === 0}
          onClick={() => onFocus(focus === metric.key ? null : metric.key)}
        >
          <strong>{metric.count}</strong>
          <span>{metric.label}</span>
        </button>
      ))}
    </div>
  );
}

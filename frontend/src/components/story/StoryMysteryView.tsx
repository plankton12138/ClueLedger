import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { errorMessage } from "../../api/errors";
import {
  readStoryMystery,
  type StoryGraph,
  type StoryMystery,
  type StoryMysteryDiagnostic,
} from "../../api/story";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import {
  clueUseNodes,
  knowledgeSubjects,
  orderedMysteryNodes,
  readerReadings,
  timeLabel,
  useTone,
  roleSummary,
} from "./mysteryLayout";

interface Props {
  workspaceId: string;
  variantId: string;
  revision: string;
  graph: StoryGraph;
  selectedClueId: string | null;
  onSelectClue: (clueId: string | null) => void;
  onSelectNode: (nodeId: string) => void;
}

function stanceLabel(stances: string[]): string {
  if (stances.includes("knows") && stances.includes("believes")) return "知道 / 相信";
  return stances.includes("knows") ? "知道" : "相信";
}

export default function StoryMysteryView({
  workspaceId,
  variantId,
  revision,
  graph,
  selectedClueId,
  onSelectClue,
  onSelectNode,
}: Props) {
  const [mystery, setMystery] = useState<StoryMystery | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [collapsedLanes, setCollapsedLanes] = useState<Set<"detective" | "reader" | "clue">>(new Set());
  const selectedClueRef = useRef(selectedClueId);
  selectedClueRef.current = selectedClueId;

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    readStoryMystery(workspaceId, variantId)
      .then((data) => {
        if (cancelled) return;
        setMystery(data);
        const current = selectedClueRef.current;
        if (current && data.clues.some((clue) => clue.clue_id === current)) return;
        const preferred =
          data.clues.find((clue) =>
            clue.roles.some((role) => role === "misdirect" || role === "reinterpret"),
          ) ??
          data.clues.find((clue) => clue.uses.length > 0) ??
          data.clues[0];
        onSelectClue(preferred?.clue_id ?? null);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(errorMessage(cause, "读取悬疑视图"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [workspaceId, variantId, revision, reloadKey, onSelectClue]);

  const ordered = useMemo(
    () => orderedMysteryNodes(graph, mystery),
    [graph, mystery],
  );
  const selectedClue = useMemo(
    () => mystery?.clues.find((clue) => clue.clue_id === selectedClueId) ?? null,
    [mystery, selectedClueId],
  );
  const relatedNodeIds = useMemo(
    () => new Set(selectedClue ? clueUseNodes(selectedClue) : []),
    [selectedClue],
  );
  const stageGroups = useMemo(() => {
    const groups: { title: string; count: number }[] = [];
    for (const item of ordered) {
      const title = item.stageTitle || "未分阶段";
      const last = groups[groups.length - 1];
      if (last && last.title === title) last.count += 1;
      else groups.push({ title, count: 1 });
    }
    return groups;
  }, [ordered]);
  const laneCounts = useMemo(() => {
    const counts = { detective: 0, reader: 0, clue: 0 };
    for (const item of ordered) {
      if (knowledgeSubjects(item.extra).length > 0) counts.detective += 1;
      if (readerReadings(item.extra).length > 0) counts.reader += 1;
      if ((item.extra?.clue_uses ?? []).length > 0) counts.clue += 1;
    }
    return counts;
  }, [ordered]);
  function toggleLane(lane: "detective" | "reader" | "clue") {
    setCollapsedLanes((previous) => {
      const next = new Set(previous);
      if (next.has(lane)) next.delete(lane);
      else next.add(lane);
      return next;
    });
  }

  if (loading && !mystery) {
    return <div className="story-mystery story-mystery-empty">正在读取悬疑视图…</div>;
  }
  if (error && !mystery) {
    return (
      <div className="story-mystery story-mystery-empty">
        <StatusMessage
          tone="error"
          action={
            <Button size="compact" onClick={() => setReloadKey((value) => value + 1)}>
              重新读取
            </Button>
          }
        >
          {error}
        </StatusMessage>
      </div>
    );
  }
  if (!mystery) return null;

  const columnCount = ordered.length;
  const gridStyle = {
    gridTemplateColumns: `112px repeat(${columnCount}, minmax(180px, 1fr))`,
    minWidth: 112 + columnCount * 180,
  };

  return (
    <div className="story-mystery">
      <header className="story-mystery-head">
        <div className="min-w-0">
          <h2>悬疑视图</h2>
          <p>横轴是故事内实际时间；未登记节点按阶段与叙述顺序追加。线索是横轴上的使用点，不是章节标题。横向滚动看时间，向下滚动看侦探/助手、读者与线索层。</p>
        </div>
        <div className="story-mystery-metrics" aria-label="悬疑视图统计">
          <span
            className="story-mystery-metric"
            data-tone={mystery.time_status.state === "complete" ? undefined : "warn"}
          >
            时间 {mystery.time_status.registered}/{mystery.time_status.total}
          </span>
          <span className="story-mystery-metric">线索 {mystery.clues.length}</span>
          <span className="story-mystery-metric">诊断 {mystery.diagnostics.length}</span>
          {loading && <span className="story-mystery-metric">刷新中…</span>}
        </div>
      </header>

      {error && (
        <StatusMessage
          tone="error"
          action={
            <Button size="compact" onClick={() => setReloadKey((value) => value + 1)}>
              重新读取
            </Button>
          }
        >
          {error}
        </StatusMessage>
      )}

      {mystery.diagnostics.length > 0 && (
        <details className="story-mystery-diagnostics">
          <summary>结构诊断 · 全局 · {mystery.diagnostics.length}</summary>
          <ul>
            {mystery.diagnostics.map((item) => (
              <li key={`${item.code}:${item.object_id}`}>
                <DiagnosticItem
                  item={item}
                  canSelect={
                    mystery.clues.some((clue) => clue.clue_id === item.object_id)
                  }
                  onSelect={() => onSelectClue(item.object_id)}
                />
              </li>
            ))}
          </ul>
        </details>
      )}

      {ordered.length === 0 ? (
        <div className="story-mystery-empty">还没有构思结构。</div>
      ) : (
        <div className="story-mystery-scroll" aria-label="实际时间轴">
          <div className="story-mystery-grid" style={gridStyle} role="group" aria-label="时间轴泳道">
            <div className="story-mystery-label story-mystery-label-head">阶段</div>
            {stageGroups.map((group, index) => (
              <div
                className="story-mystery-stage-group"
                style={{ gridColumn: `span ${group.count}` }}
                key={`stage-${group.title}-${index}`}
              >
                {group.title}
              </div>
            ))}

            <div className="story-mystery-label story-mystery-label-head">时间轴</div>
            {ordered.map((item, index) => (
              <div className="story-mystery-headcell" key={`head-${item.node.id}`}>
                <span className="story-mystery-index">{String(index + 1).padStart(2, "0")}</span>
                <strong className="story-mystery-time">{timeLabel(item)}</strong>
              </div>
            ))}

            <div className="story-mystery-label">实际发生</div>
            {ordered.map((item) => (
              <div
                className="story-mystery-cell"
                data-lane="actual"
                data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                key={`actual-${item.node.id}`}
              >
                <button
                  type="button"
                  className="story-mystery-node-link"
                  onClick={() => {
                    if (selectedClue && !relatedNodeIds.has(item.node.id)) onSelectClue(null);
                    onSelectNode(item.node.id);
                  }}
                  title="只看与此节点相关的线索"
                >
                  {item.node.title}
                </button>
                <p className="story-mystery-action">{item.node.action}</p>
              </div>
            ))}

            <LaneLabel
              label="侦探 / 助手"
              count={laneCounts.detective}
              collapsed={collapsedLanes.has("detective")}
              onToggle={() => toggleLane("detective")}
            />
            {ordered.map((item) => {
              const subjects = knowledgeSubjects(item.extra);
              return collapsedLanes.has("detective") ? (
                <div
                  className="story-mystery-cell story-mystery-compact"
                  data-lane="detective"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`detective-compact-${item.node.id}`}
                >
                  {subjects.length > 0 ? `${subjects.length} 条知情` : "—"}
                </div>
              ) : (
                <div
                  className="story-mystery-cell"
                  data-lane="detective"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`detective-${item.node.id}`}
                >
                  {subjects.length === 0 ? (
                    <span className="story-mystery-empty">未登记</span>
                  ) : (
                    subjects.map((subject) => (
                      <div className="story-mystery-knowledge" key={subject.subject_id}>
                        <span className="story-mystery-subject">{subject.subject_id}</span>
                        <span className="story-mystery-stance">{stanceLabel(subject.stances)}</span>
                        {subject.claims.map((claim) => (
                          <p key={claim}>{claim}</p>
                        ))}
                      </div>
                    ))
                  )}
                </div>
              );
            })}

            <LaneLabel
              label="读者"
              count={laneCounts.reader}
              collapsed={collapsedLanes.has("reader")}
              onToggle={() => toggleLane("reader")}
            />
            {ordered.map((item) => {
              const readings = readerReadings(item.extra);
              return collapsedLanes.has("reader") ? (
                <div
                  className="story-mystery-cell story-mystery-compact"
                  data-lane="reader"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`reader-compact-${item.node.id}`}
                >
                  {readings.length > 0 ? `${readings.length} 条读法` : "—"}
                </div>
              ) : (
                <div
                  className="story-mystery-cell"
                  data-lane="reader"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`reader-${item.node.id}`}
                >
                  {readings.length === 0 ? (
                    <span className="story-mystery-empty">未登记</span>
                  ) : (
                    readings.map((use) => (
                      <button
                        type="button"
                        className="story-mystery-chip"
                        data-tone={useTone(use.roles)}
                        data-selected={selectedClueId === use.clue_id ? "true" : undefined}
                        onClick={() => onSelectClue(use.clue_id)}
                        key={`${item.node.id}-${use.clue_id}-${use.roles.join("-")}`}
                      >
                        <strong>{use.title}</strong>
                        <span>{use.expected_interpretation}</span>
                      </button>
                    ))
                  )}
                </div>
              );
            })}

            <LaneLabel
              label="线索"
              count={laneCounts.clue}
              collapsed={collapsedLanes.has("clue")}
              onToggle={() => toggleLane("clue")}
            />
            {ordered.map((item) => {
              const uses = item.extra?.clue_uses ?? [];
              return collapsedLanes.has("clue") ? (
                <div
                  className="story-mystery-cell story-mystery-compact"
                  data-lane="clue"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`clue-compact-${item.node.id}`}
                >
                  {uses.length > 0 ? `${uses.length} 个使用点` : "—"}
                </div>
              ) : (
                <div
                  className="story-mystery-cell"
                  data-lane="clue"
                  data-related={relatedNodeIds.has(item.node.id) ? "true" : undefined}
                  key={`clue-${item.node.id}`}
                >
                  {uses.length === 0 ? (
                    <span className="story-mystery-empty">没有使用线索</span>
                  ) : (
                    uses.map((use) => (
                      <button
                        type="button"
                        className="story-mystery-chip"
                        data-tone={useTone(use.roles)}
                        data-selected={selectedClueId === use.clue_id ? "true" : undefined}
                        onClick={() => onSelectClue(use.clue_id)}
                        key={`${item.node.id}-${use.clue_id}`}
                      >
                        <strong>{use.title}</strong>
                        <span>{roleSummary(use.roles)}</span>
                      </button>
                    ))
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

    </div>
  );
}

function LaneLabel({
  label,
  count,
  collapsed,
  onToggle,
}: {
  label: string;
  count: number;
  collapsed: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="story-mystery-label">
      <button
        type="button"
        className="story-mystery-lane-toggle"
        aria-expanded={!collapsed}
        title={collapsed ? "展开这一层" : "收起这一层"}
        onClick={onToggle}
      >
        {collapsed ? <ChevronRight size={14} aria-hidden="true" /> : <ChevronDown size={14} aria-hidden="true" />}
        <span>{label}</span>
      </button>
      <span className="story-mystery-lane-count">{count} 列</span>
    </div>
  );
}

function DiagnosticItem({
  item,
  canSelect,
  onSelect,
}: {
  item: StoryMysteryDiagnostic;
  canSelect: boolean;
  onSelect: () => void;
}) {
  return (
    <div className="story-mystery-diagnostic" data-code={item.code}>
      <div>
        <strong>{item.message}</strong>
        <p>{item.suggestion}</p>
      </div>
      {canSelect && (
        <Button size="compact" variant="quiet" onClick={onSelect}>
          查看线索
        </Button>
      )}
    </div>
  );
}

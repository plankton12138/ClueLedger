/**
 * 线索页：左侧全书线索列表，右侧选中线索的一跳关系。
 *
 * 图只是投影：没有关系时显示列表，不放假连线；预算外的关系继续在列表里可达。
 * 打开页面不调用模型，也不推断新关系。
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowRight, ArrowUpRight, RefreshCw } from "lucide-react";

import { fetchClues, fetchStoryClues, type ClueSummary } from "../../api/clues";
import type { ClueKind, ClueRelationKind, ClueRelations } from "../../api/story";
import { errorMessage } from "../../api/errors";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";

const KIND_LABEL: Record<ClueKind, string> = { truth: "真相线索", mislead: "误导线索" };
const RELATION_LABEL: Record<ClueRelationKind, string> = {
  supports: "支撑",
  contradicts: "矛盾",
  co_misdirects: "共同误导",
};

function Graph({
  relations,
  onSelect,
}: {
  relations: ClueRelations;
  onSelect: (clueId: string) => void;
}) {
  const nodes = relations.nodes;
  const size = 320;
  const center = size / 2;
  const radius = center - 46;
  const focus = relations.focus_clue_id;
  const positions = useMemo(() => {
    const map = new Map<string, { x: number; y: number }>();
    const others = nodes.filter(node => node.clue_id !== focus);
    if (focus) map.set(focus, { x: center, y: center });
    others.forEach((node, index) => {
      const angle = (Math.PI * 2 * index) / Math.max(1, others.length) - Math.PI / 2;
      map.set(node.clue_id, {
        x: center + Math.cos(angle) * radius,
        y: center + Math.sin(angle) * radius,
      });
    });
    return map;
  }, [nodes, focus, center, radius]);

  if (nodes.length === 0) {
    return <p className="clue-graph-empty">还没有登记关系。选中一条线索后可以在详情里添加。</p>;
  }
  return (
    <svg className="clue-graph" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="选中线索的一跳关系">
      <defs>
        <marker id="clue-arrow" markerWidth="8" markerHeight="8" refX="16" refY="4" orient="auto">
          <path d="M0,0 L8,4 L0,8 Z" fill="currentColor" />
        </marker>
      </defs>
      {relations.edges.map(edge => {
        const from = positions.get(edge.from_clue_id);
        const to = positions.get(edge.to_clue_id);
        if (!from || !to) return null;
        return (
          <line
            key={edge.relation_id}
            x1={from.x}
            y1={from.y}
            x2={to.x}
            y2={to.y}
            className={`clue-edge ${edge.kind}`}
            markerEnd={edge.kind === "supports" ? "url(#clue-arrow)" : undefined}
          />
        );
      })}
      {nodes.map(node => {
        const position = positions.get(node.clue_id);
        if (!position) return null;
        return (
          <g
            key={node.clue_id}
            className={`clue-node ${node.clue_id === focus ? "focus" : ""} ${node.kind ?? "unknown"}`}
            transform={`translate(${position.x},${position.y})`}
            role="button"
            tabIndex={0}
            onClick={() => onSelect(node.clue_id)}
            onKeyDown={event => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onSelect(node.clue_id);
              }
            }}
          >
            <circle r={30} />
            <text textAnchor="middle" dy="4">
              {node.title.length > 6 ? `${node.title.slice(0, 6)}…` : node.title}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

export default function ClueSurface({
  workspaceId,
  selectedClueId,
  onSelectClue,
}: {
  workspaceId: string;
  selectedClueId: string | null;
  onSelectClue: (clueId: string | null) => void;
}) {
  const variantId = useStoryStore(state => state.variantId);
  const relations = useStoryStore(state => state.relations);
  const loadRelations = useStoryStore(state => state.loadRelations);
  const [clues, setClues] = useState<ClueSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [query, setQuery] = useState("");
  const [showGraph, setShowGraph] = useState(false);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      if (variantId) {
        const list = await fetchStoryClues(workspaceId, variantId, null, null, "", 50);
        setClues(list.items);
        await loadRelations(workspaceId, selectedClueId);
      } else {
        // 没有构思时读取账本线索，不为了展示而建立主方案。
        const list = await fetchClues(workspaceId, null, null, "", 50);
        setClues(list.items);
      }
    } catch (cause) {
      setError(errorMessage(cause, "读取线索"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    // 只在作品或方案变化时重读列表；选中变化单独刷新关系。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, variantId]);

  useEffect(() => {
    if (!variantId) return;
    void loadRelations(workspaceId, selectedClueId);
  }, [workspaceId, variantId, selectedClueId, loadRelations]);

  const visible = clues.filter(row =>
    query.trim() ? row.title.toLowerCase().includes(query.trim().toLowerCase()) : true,
  );

  return (
    <div className="clue-surface" data-mode="clues">
      <header className="clue-toolbar">
        <div>
          <h1>全书线索</h1>
          <p>
            {variantId
              ? "名称、用途与正文出处在这里查看；关系图按需打开。"
              : "还没有构思；这里显示正文里已经登记的线索，不自动建立方案。"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="compact"
            aria-pressed={showGraph}
            disabled={!selectedClueId || !variantId}
            disabledReason="先建立构思并选择一条线索"
            onClick={() => setShowGraph(value => !value)}
          >
            {showGraph ? "返回线索列表" : "查看关系图"}
          </Button>
          <Button size="compact" disabled={loading} onClick={() => void load()}>
            <RefreshCw size={15} aria-hidden="true" />重新读取
          </Button>
        </div>
      </header>
      {error && (
        <p className="clue-error" role="alert">
          {error}
        </p>
      )}
      <div className="clue-body" data-graph={showGraph ? "on" : "off"}>
        <section className="clue-list" aria-label="线索列表">
          <input
            className="clue-search"
            type="search"
            placeholder="按标题查找"
            value={query}
            onChange={event => setQuery(event.target.value)}
          />
          {visible.length === 0 ? (
            <p className="clue-empty">还没有线索。可以在正文里选段标记为真相或误导。</p>
          ) : (
            <ul>
              {visible.map(row => (
                <li key={row.clue_id}>
                  <button
                    type="button"
                    className={`clue-list-item ${row.clue_id === selectedClueId ? "selected" : ""}`}
                    aria-pressed={row.clue_id === selectedClueId}
                    onClick={() => onSelectClue(row.clue_id)}
                  >
                    <span className="clue-list-title">{row.title}</span>
                    <span className={`clue-kind ${row.plan?.kind ?? "unknown"}`}>
                      {row.plan?.kind ? KIND_LABEL[row.plan.kind] : "用途未定"}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
        {showGraph && <section className="clue-relations" aria-label="线索关系">
          {selectedClueId ? (
            <>
              <Graph relations={relations ?? emptyRelations(variantId ?? "")} onSelect={id => onSelectClue(id)} />
              {relations && relations.remaining_count > 0 && (
                <p className="clue-graph-note">还有 {relations.remaining_count} 条相连线索没有画在图上，仍可在左侧列表选择。</p>
              )}
              {relations && relations.edges.length > 0 ? (
                <ul className="clue-relation-list" aria-label="关系列表">
                  {relations.edges.map(edge => {
                    const Icon =
                      edge.kind !== "supports"
                        ? ArrowRight
                        : edge.from_clue_id === selectedClueId
                          ? ArrowUpRight
                          : ArrowDownLeft;
                    const other =
                      edge.from_clue_id === selectedClueId ? edge.to_clue_id : edge.from_clue_id;
                    const otherTitle =
                      relations.nodes.find(node => node.clue_id === other)?.title ?? other;
                    return (
                      <li key={edge.relation_id}>
                        <button type="button" className="clue-relation-item" onClick={() => onSelectClue(other)}>
                          <Icon size={14} aria-hidden="true" />
                          <span>{RELATION_LABEL[edge.kind]}</span>
                          <span className="clue-relation-target">{otherTitle}</span>
                          <span className="clue-relation-reason">{edge.reason}</span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              ) : (
                <p className="clue-empty">这条线索还没有登记关系。</p>
              )}
            </>
          ) : (
            <p className="clue-empty">选择一条线索，查看它与其他线索的关系。</p>
          )}
        </section>}
      </div>
    </div>
  );
}

function emptyRelations(variantId: string): ClueRelations {
  return {
    variant_id: variantId,
    variant_revision: "",
    view_revision: "",
    focus_clue_id: null,
    nodes: [],
    edges: [],
    remaining_count: 0,
  };
}

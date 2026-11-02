/**
 * 共享只读详情区：构思节点、线索与「讨论调整」共用同一块辅助区域。
 *
 * 页面只负责查看：没有字段输入、保存、分类切换或关系表单。调整统一通过对话完成，
 * 点击「讨论调整」只准备本轮焦点并打开输入区，不发送固定核对文本，也不调用模型。
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowRight, ArrowUpRight } from "lucide-react";

import {
  readStoryNode,
  readStoryProseMarks,
  type ClueKind,
  type ClueRelationKind,
  type StoryAttentionNode,
  type StoryNodeFull,
} from "../../api/story";
import {
  fetchClueDetail,
  fetchStoryClueDetail,
  fetchStoryClues,
  type ClueDetail,
} from "../../api/clues";
import { errorMessage } from "../../api/errors";
import { useDiscussionFocusStore } from "../../state/discussionContext";
import { useStoryStore } from "../../state/story";
import ObjectReadDetail, {
  type ReadDetailOccurrence,
  type ReadDetailSection,
} from "./ObjectReadDetail";

const KIND_LABEL: Record<ClueKind, string> = { truth: "真相线索", mislead: "误导线索" };
const RELATION_LABEL: Record<ClueRelationKind, string> = {
  supports: "支撑",
  contradicts: "矛盾",
  co_misdirects: "共同误导",
};
const REASON_LABEL: Record<string, string> = {
  review_finding: "有未解决的核对问题",
  anchor_missing: "关联原句在正文里找不到了",
  anchor_ambiguous: "关联原句在正文里出现多次",
  anchor_unavailable: "关联正文暂时读不到",
  not_checked: "这段关联还没有核对过",
  plan_changed: "构思在上次核对后改过",
  prose_changed: "正文在上次核对后改过",
};
const STATUS_LABEL: Record<string, string> = {
  ready: "就绪",
  draft: "草稿",
  attention: "需核对",
};

function statusReasons(attention: StoryAttentionNode | undefined): string[] {
  return (attention?.reasons ?? []).map(reason => REASON_LABEL[reason] ?? reason);
}

function NodeDetail({
  workspaceId,
  node,
  attention,
  onClose,
  onLocateProse,
}: {
  workspaceId: string;
  node: StoryNodeFull;
  attention: StoryAttentionNode | undefined;
  onClose: () => void;
  onLocateProse: (target: { path: string; chapter?: number }) => void;
}) {
  const variantId = useStoryStore(state => state.variantId);
  const graphRevision = useStoryStore(state => state.graph?.revision ?? "");
  const invalidation = useStoryStore(state => state.invalidation);
  const [occurrences, setOccurrences] = useState<ReadDetailOccurrence[]>([]);
  const [clueTitles, setClueTitles] = useState<Map<string, string>>(new Map());
  const [warning, setWarning] = useState<string | null>(null);
  const prepare = useDiscussionFocusStore(state => state.prepare);
  const paths = useMemo(
    () => [...new Set(attention?.chapter_paths ?? [])].slice(0, 8),
    [attention?.chapter_paths],
  );

  useEffect(() => {
    let active = true;
    setOccurrences([]);
    setWarning(null);
    if (!variantId) return;
    void Promise.all([
      paths.length > 0
        ? Promise.all(
            paths.map(path =>
              readStoryProseMarks(workspaceId, variantId, path)
                .then(view => ({ path, view }))
                .catch(() => ({ path, view: null })),
            ),
          )
        : Promise.resolve([]),
      fetchStoryClues(workspaceId, variantId, null, null, "", 50)
        .then(list => new Map(list.items.map(row => [row.clue_id, row.title])))
        .catch(() => new Map<string, string>()),
    ])
      .then(([views, titles]) => {
        if (!active) return;
        setClueTitles(titles);
        const rows: ReadDetailOccurrence[] = [];
        const failed: string[] = [];
        for (const item of views) {
          if (!item.view) {
            failed.push(item.path);
            continue;
          }
          for (const mark of item.view.marks.filter(mark => mark.node_id === node.id)) {
            rows.push({
              key: `${item.path}:${mark.start}:${mark.end}`,
              path: item.path,
              quote: mark.quote,
              state: mark.state,
            });
          }
        }
        setOccurrences(rows);
        if (failed.length > 0) {
          setWarning(`有 ${failed.length} 章的出处暂时读不到，未显示引文。`);
        }
      })
      .catch(cause => {
        if (active) setWarning(errorMessage(cause, "读取出处"));
      });
    return () => {
      active = false;
    };
  }, [workspaceId, variantId, node.id, paths, invalidation]);

  const sections: ReadDetailSection[] = [
    { key: "action", title: "构思内容", text: node.action },
    { key: "change", title: "关键变化", text: node.change ?? "" },
    {
      key: "time",
      title: "时间",
      text: [
        node.time_note,
        node.event_span
          ? `相对分钟 ${node.event_span.start_minute}—${node.event_span.end_minute}${node.event_span.approximate ? "（约）" : ""}`
          : "",
      ]
        .filter(Boolean)
        .join("；"),
    },
    {
      key: "planned",
      title: "计划章节",
      text: node.planned_chapter_number ? `第 ${node.planned_chapter_number} 章` : "",
    },
    {
      key: "clues",
      title: "用到的线索",
      text: node.clue_uses
        .map(use => {
          const title = clueTitles.get(use.clue_id) ?? use.clue_id;
          return `${title}：${use.presentation}`;
        })
        .join("\n"),
    },
  ];

  function discuss() {
    if (!variantId) return;
    prepare({
      focus: {
        kind: "object",
        object_kind: "node",
        object_id: node.id,
        variant_id: variantId,
        story_revision: graphRevision || null,
      },
      label: `构思：${node.title}`,
    });
  }

  function locate(row: ReadDetailOccurrence) {
    onLocateProse({ path: row.path });
  }

  return (
    <ObjectReadDetail
      title={node.title}
      kindLabel="构思节点"
      statusLabel={attention ? STATUS_LABEL[attention.status] : undefined}
      reasons={statusReasons(attention)}
      sections={sections}
      occurrences={occurrences}
      notice={warning ?? (occurrences.length === 0 ? "还没有登记正文出处；正文里的关联才会列在这里。" : undefined)}
      onLocate={locate}
      onDiscuss={discuss}
      onClose={onClose}
    />
  );
}

function ClueDetailPanel({
  workspaceId,
  clueId,
  onClose,
  onLocate,
}: {
  workspaceId: string;
  clueId: string;
  onClose: () => void;
  onLocate: (occurrence: { path: string; evidence_id?: string; chapter?: number }) => void;
}) {
  const variantId = useStoryStore(state => state.variantId);
  const graphRevision = useStoryStore(state => state.graph?.revision ?? "");
  const invalidation = useStoryStore(state => state.invalidation);
  const [detail, setDetail] = useState<ClueDetail | null>(null);
  const [titles, setTitles] = useState<Map<string, string>>(new Map());
  const [error, setError] = useState<string | null>(null);
  const prepare = useDiscussionFocusStore(state => state.prepare);

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError(null);
    const load = variantId
      ? Promise.all([
          fetchStoryClueDetail(workspaceId, variantId, clueId, null),
          fetchStoryClues(workspaceId, variantId, null, null, "", 50).catch(() => null),
        ]).then(([row, list]) => ({
          row,
          titles: new Map(list?.items.map(item => [item.clue_id, item.title]) ?? []),
        }))
      : // 没有构思时仍可阅读账本线索；不自动建立主方案。
        fetchClueDetail(workspaceId, clueId, null).then(row => ({
          row,
          titles: new Map<string, string>(),
        }));
    void load
      .then(({ row, titles: next }) => {
        if (!active) return;
        setDetail(row);
        setTitles(next);
      })
      .catch(cause => {
        if (active) setError(errorMessage(cause, "读取线索"));
      });
    return () => {
      active = false;
    };
  }, [workspaceId, variantId, clueId, invalidation]);

  function discuss() {
    if (!detail) return;
    prepare({
      focus: {
        kind: "object",
        object_kind: "clue",
        object_id: detail.clue_id,
        variant_id: variantId,
        story_revision: variantId ? graphRevision || detail.story_revision || null : null,
        clue_revision: detail.clue_revision,
        view_revision: detail.view_revision,
      },
      label: `线索：${detail.title}`,
    });
  }

  if (error) {
    return (
      <div className="workspace-detail">
        <p className="workspace-detail-error" role="alert">{error}</p>
      </div>
    );
  }
  if (!detail) {
    return (
      <div className="workspace-detail">
        <p className="workspace-detail-hint">正在读取线索…</p>
      </div>
    );
  }

  const sections: ReadDetailSection[] = [
    { key: "intention", title: "作者意图", text: detail.plan?.intention ?? "" },
    { key: "truth", title: "作者真相", text: detail.plan?.author_truth ?? "" },
  ];
  const FRESHNESS_STATE: Record<string, string> = {
    valid: "exact",
    stale: "missing",
    source_missing: "missing",
    unavailable: "unavailable",
    none: "unavailable",
  };
  const occurrences: ReadDetailOccurrence[] = detail.occurrences.length > 0
    ? detail.occurrences.map((row, index) => ({
        key: `${row.path}:${row.start ?? "x"}:${row.end ?? "x"}:${index}`,
        path: row.path,
        quote: row.quote,
        chapter: row.chapter,
        state: row.state,
        evidenceId: row.evidence_ids[0],
      }))
    : detail.evidence.map((row, index) => ({
        key: `${row.path}:${row.start}:${row.end}:${index}`,
        path: row.path,
        quote: row.quote,
        chapter: row.chapter,
        state: FRESHNESS_STATE[row.freshness] ?? "unavailable",
        evidenceId: row.evidence_id,
      }));
  const relations = detail.relations;

  return (
    <ObjectReadDetail
      title={detail.title}
      kindLabel={detail.plan?.kind ? KIND_LABEL[detail.plan.kind] : "用途未定"}
      sections={sections}
      occurrences={occurrences}
      notice={detail.warnings.length > 0 ? detail.warnings.join("；") : undefined}
      extra={
        relations.length > 0 ? (
          <section className="workspace-detail-section" aria-label="相关线索">
            <h3>相关线索</h3>
            <ul className="workspace-detail-list">
              {relations.map(row => {
                const other = row.from_clue_id === detail.clue_id ? row.to_clue_id : row.from_clue_id;
                const Icon =
                  row.direction === "outgoing"
                    ? ArrowUpRight
                    : row.direction === "incoming"
                      ? ArrowDownLeft
                      : ArrowRight;
                return (
                  <li key={row.relation_id}>
                    <span className="workspace-detail-relation">
                      <Icon size={14} aria-hidden="true" />
                      {RELATION_LABEL[row.kind]}
                      {row.kind === "supports" && row.direction === "outgoing" ? " → " : " ↔ "}
                      {titles.get(other) ?? other}
                    </span>
                    <span className="workspace-detail-meta">{row.reason}</span>
                  </li>
                );
              })}
            </ul>
          </section>
        ) : undefined
      }
      onLocate={row =>
        onLocate({ path: row.path, evidence_id: row.evidenceId, chapter: row.chapter ?? undefined })}
      onDiscuss={discuss}
      onClose={onClose}
    />
  );
}

export default function WorkspaceDetail({
  workspaceId,
  mode,
  nodeId,
  clueId,
  onClose,
  onLocateProse,
}: {
  workspaceId: string;
  mode: "prose" | "story" | "clues";
  nodeId: string | null;
  clueId: string | null;
  onClose: () => void;
  onLocateProse: (target: { path: string; evidence_id?: string; chapter?: number }) => void;
}) {
  const variantId = useStoryStore(state => state.variantId);
  const invalidation = useStoryStore(state => state.invalidation);
  const attention = useStoryStore(state =>
    state.attention?.nodes.find(node => node.node_id === nodeId),
  );
  const [node, setNode] = useState<StoryNodeFull | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setNode(null);
    setError(null);
    if (mode === "clues" || !variantId || !nodeId) return;
    void readStoryNode(workspaceId, variantId, nodeId)
      .then(row => {
        if (active) setNode(row);
      })
      .catch(cause => {
        if (active) setError(errorMessage(cause, "读取节点"));
      });
    return () => {
      active = false;
    };
  }, [workspaceId, variantId, nodeId, mode, invalidation]);

  if (clueId && (mode === "clues" || mode === "prose")) {
    return (
      <ClueDetailPanel
        workspaceId={workspaceId}
        clueId={clueId}
        onClose={onClose}
        onLocate={onLocateProse}
      />
    );
  }
  if (nodeId && (mode === "story" || mode === "prose")) {
    if (error) return <div className="workspace-detail"><p className="workspace-detail-error">{error}</p></div>;
    if (!node) return <div className="workspace-detail"><p className="workspace-detail-hint">正在读取节点…</p></div>;
    return (
      <NodeDetail
        workspaceId={workspaceId}
        node={node}
        attention={attention}
        onClose={onClose}
        onLocateProse={onLocateProse}
      />
    );
  }
  return (
    <div className="workspace-detail workspace-detail-empty">
      <p>选择本章相关项或正文里的标记，在这里查看详情。</p>
    </div>
  );
}

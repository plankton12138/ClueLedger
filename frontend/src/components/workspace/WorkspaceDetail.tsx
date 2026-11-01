/**
 * 共享详情区：构思节点、线索与「讨论调整」共用同一块辅助区域。
 *
 * 同一时刻只显示一个对象；页面选择与辅助区模式分开，所以打开讨论不会丢掉所选线索。
 * 所有写入按当前修订整份提交，失败时保留输入。
 */

import { useEffect, useMemo, useState } from "react";
import { ArrowDownLeft, ArrowRight, ArrowUpRight, Locate, MessageSquare, X } from "lucide-react";

import {
  readStoryNode,
  type ClueKind,
  type ClueRelationKind,
  type StoryAttentionNode,
  type StoryNodeFull,
} from "../../api/story";
import {
  fetchStoryClueDetail,
  fetchStoryClues,
  type ClueDetail,
  type ClueSummary,
} from "../../api/clues";
import { createConversationWithText } from "../../api/workspace";
import { errorMessage } from "../../api/errors";
import { useStoryStore } from "../../state/story";
import { useWorkspaceStore } from "../../state/workspace";
import { useConversationsStore } from "../../state/conversations";
import { useLayoutStore } from "../../state/layout";
import { navigate } from "../../state/navigation";
import { Button } from "../ui/Button";

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

function requestKey(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(16).slice(2, 8)}`;
}

function NodeDetail({
  workspaceId,
  node,
  attention,
  onClose,
}: {
  workspaceId: string;
  node: StoryNodeFull;
  attention: StoryAttentionNode | undefined;
  onClose: () => void;
}) {
  const submit = useStoryStore(state => state.submit);
  const acknowledge = useStoryStore(state => state.acknowledge);
  const busy = useStoryStore(state => state.busy);
  const variantId = useStoryStore(state => state.variantId);
  const [title, setTitle] = useState(node.title);
  const [action, setAction] = useState(node.action);
  const [timeNote, setTimeNote] = useState(node.time_note);
  const [planned, setPlanned] = useState(node.planned_chapter_number?.toString() ?? "");
  const [start, setStart] = useState(node.event_span?.start_minute?.toString() ?? "");
  const [end, setEnd] = useState(node.event_span?.end_minute?.toString() ?? "");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setTitle(node.title);
    setAction(node.action);
    setTimeNote(node.time_note);
    setPlanned(node.planned_chapter_number?.toString() ?? "");
    setStart(node.event_span?.start_minute?.toString() ?? "");
    setEnd(node.event_span?.end_minute?.toString() ?? "");
    setError(null);
  }, [node.id, node.title, node.action, node.time_note]);

  async function save() {
    setError(null);
    // clue_uses 等字段必须原样带回；clue_ids 是图投影，不进入写入模型。
    const { clue_ids: _clueIds, ...rest } = node;
    const payload = {
      ...rest,
      title: title.trim(),
      action: action.trim(),
      time_note: timeNote,
      planned_chapter_number: planned.trim() ? Number(planned) : null,
      event_span: start.trim() && end.trim()
        ? { start_minute: Number(start), end_minute: Number(end), approximate: node.event_span?.approximate ?? false }
        : null,
    };
    if (!payload.title || !payload.action) {
      setError("标题和构思内容不能为空");
      return;
    }
    const ok = await submit([{ op: "update_node", node_id: node.id, node: payload }], "详情区修改节点");
    if (!ok) setError(useStoryStore.getState().error ?? "保存失败");
  }

  async function discuss() {
    if (!variantId || !attention) return;
    setError(null);
    const paths = attention.chapter_paths;
    const conversation = useConversationsStore.getState();
    if (conversation.workspaceId !== workspaceId) await conversation.load(workspaceId);
    try {
      await createConversationWithText(workspaceId, {
        text: "请核对当前构思与关联正文，说明差异和依据，并提出调整建议。先进行讨论。",
        request_key: `reconcile:${attention.fingerprint}`,
        purpose: "review",
        review_paths: paths,
        context: {
          selected_path: paths[0] ?? null,
          reference_paths: paths,
          observed_digests: {},
          story: {
            variant_id: variantId,
            revision: useStoryStore.getState().graph?.revision ?? "",
            node_ids: [node.id],
            purpose: "review",
          },
          story_reconcile: {
            variant_id: variantId,
            variant_revision: useStoryStore.getState().graph?.revision ?? "",
            node_id: node.id,
            binding_ids: attention.binding_ids,
            warning_fingerprint: attention.fingerprint,
          },
        },
      });
      await useConversationsStore.getState().load(workspaceId);
      useLayoutStore.getState().revealChat();
    } catch (cause) {
      setError(errorMessage(cause, "开启核对讨论"));
    }
  }

  function locate() {
    const path = attention?.chapter_paths[0];
    if (!path) return;
    navigate({ kind: "writing", workspaceId });
    void useWorkspaceStore.getState().openPath(path);
  }

  return (
    <div className="workspace-detail" aria-label="构思节点详情">
      <header className="workspace-detail-head">
        <div>
          <h2>{node.title}</h2>
          <p className={`workspace-detail-status ${attention?.status ?? "draft"}`}>
            {attention?.status === "ready" ? "就绪" : attention?.status === "attention" ? "需核对" : "草稿"}
            {attention?.reasons.length ? ` · ${attention.reasons.map(reason => REASON_LABEL[reason] ?? reason).join("、")}` : ""}
          </p>
        </div>
        <Button size="compact" aria-label="关闭详情" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </Button>
      </header>

      <label className="workspace-detail-field">
        标题
        <input value={title} onChange={event => setTitle(event.target.value)} />
      </label>
      <label className="workspace-detail-field">
        构思内容
        <textarea rows={4} value={action} onChange={event => setAction(event.target.value)} />
      </label>
      <label className="workspace-detail-field">
        时间说明
        <input value={timeNote} onChange={event => setTimeNote(event.target.value)} placeholder="例如：案发前两天、晚餐后" />
      </label>
      <div className="workspace-detail-row">
        <label className="workspace-detail-field">
          计划章节
          <input inputMode="numeric" value={planned} onChange={event => setPlanned(event.target.value)} />
        </label>
        <label className="workspace-detail-field">
          开始（相对分钟）
          <input inputMode="numeric" value={start} onChange={event => setStart(event.target.value)} />
        </label>
        <label className="workspace-detail-field">
          结束
          <input inputMode="numeric" value={end} onChange={event => setEnd(event.target.value)} />
        </label>
      </div>
      <p className="workspace-detail-hint">相对分钟以故事时间基准为零点；只有文字说明时留空。</p>
      {error && <p className="workspace-detail-error" role="alert">{error}</p>}

      <div className="workspace-detail-actions">
        <Button size="compact" variant="primary" disabled={busy} onClick={() => void save()}>
          保存修改
        </Button>
        {attention?.chapter_paths.length ? (
          <Button size="compact" onClick={locate}>
            <Locate size={15} aria-hidden="true" />定位正文
          </Button>
        ) : null}
        {attention?.status === "attention" && attention.reminder_visible ? (
          <Button
            size="compact"
            disabled={busy}
            onClick={() => void acknowledge(node.id, attention.fingerprint)}
          >
            稍后处理
          </Button>
        ) : null}
        <Button
          size="compact"
          variant="quiet"
          disabled={busy || !attention || attention.chapter_paths.length === 0}
          disabledReason="先给节点关联正文"
          onClick={() => void discuss()}
        >
          <MessageSquare size={15} aria-hidden="true" />讨论调整
        </Button>
      </div>
    </div>
  );
}

function RelationForm({
  clue,
  clues,
  onDone,
}: {
  clue: ClueDetail;
  clues: ClueSummary[];
  onDone: () => void;
}) {
  const submit = useStoryStore(state => state.submit);
  const busy = useStoryStore(state => state.busy);
  const [target, setTarget] = useState("");
  const [kind, setKind] = useState<ClueRelationKind>("supports");
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const options = clues.filter(row => row.clue_id !== clue.clue_id);

  async function save() {
    setError(null);
    if (!target || !reason.trim()) {
      setError("选择另一条线索并写明依据");
      return;
    }
    const ok = await submit(
      [
        {
          op: "upsert_clue_relation",
          relation: {
            id: requestKey("relation"),
            from_clue_id: kind === "supports" ? clue.clue_id : target,
            to_clue_id: kind === "supports" ? target : clue.clue_id,
            kind,
            reason: reason.trim(),
            basis_source_ids: [],
          },
        },
      ],
      "详情区登记线索关系",
    );
    if (!ok) {
      setError(useStoryStore.getState().error ?? "保存失败");
      return;
    }
    setReason("");
    setTarget("");
    onDone();
  }

  return (
    <div className="workspace-detail-relation-form">
      <label className="workspace-detail-field">
        关系类型
        <select value={kind} onChange={event => setKind(event.target.value as ClueRelationKind)}>
          <option value="supports">支撑（当前线索 → 目标线索）</option>
          <option value="contradicts">矛盾（不能同时成立）</option>
          <option value="co_misdirects">共同误导（强化同一个误解）</option>
        </select>
      </label>
      <label className="workspace-detail-field">
        目标线索
        <select value={target} onChange={event => setTarget(event.target.value)}>
          <option value="">选择一条线索</option>
          {options.map(row => (
            <option key={row.clue_id} value={row.clue_id}>
              {row.title}
            </option>
          ))}
        </select>
      </label>
      <label className="workspace-detail-field">
        说明与必要前提
        <textarea rows={2} value={reason} onChange={event => setReason(event.target.value)} />
      </label>
      {error && <p className="workspace-detail-error" role="alert">{error}</p>}
      <Button size="compact" variant="primary" disabled={busy} onClick={() => void save()}>
        登记关系
      </Button>
    </div>
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
  const submit = useStoryStore(state => state.submit);
  const busy = useStoryStore(state => state.busy);
  const [detail, setDetail] = useState<ClueDetail | null>(null);
  const [clues, setClues] = useState<ClueSummary[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showRelation, setShowRelation] = useState(false);
  const [truth, setTruth] = useState("");
  const [intention, setIntention] = useState("");

  useEffect(() => {
    let active = true;
    setDetail(null);
    setError(null);
    if (!variantId) return;
    void Promise.all([
      fetchStoryClueDetail(workspaceId, variantId, clueId, null),
      fetchStoryClues(workspaceId, variantId, null, null, "", 50),
    ])
      .then(([row, list]) => {
        if (!active) return;
        setDetail(row);
        setClues(list.items);
        setTruth(row.plan?.author_truth ?? "");
        setIntention(row.plan?.intention ?? "");
      })
      .catch(cause => {
        if (active) setError(errorMessage(cause, "读取线索"));
      });
    return () => {
      active = false;
    };
  }, [workspaceId, variantId, clueId]);

  const titleOf = useMemo(() => {
    const map = new Map(clues.map(row => [row.clue_id, row.title]));
    return (id: string) => map.get(id) ?? id;
  }, [clues]);

  async function savePlan(kind: ClueKind | null) {
    if (!detail) return;
    setError(null);
    const ok = await submit(
      [
        {
          op: "upsert_clue_plan",
          clue_id: detail.clue_id,
          plan: {
            kind,
            author_truth: truth.trim() ? truth : null,
            intention,
            resolution_intent: detail.plan?.resolution_intent ?? "undecided",
          },
        },
      ],
      "详情区修改线索用途",
    );
    if (!ok) {
      setError(useStoryStore.getState().error ?? "保存失败");
      return;
    }
    const next = await fetchStoryClueDetail(workspaceId, variantId ?? "", clueId, null).catch(() => null);
    if (next) setDetail(next);
  }

  async function referenceClue(row: ClueDetail) {
    const store = useConversationsStore.getState();
    if (store.workspaceId !== workspaceId) await store.load(workspaceId);
    const added = useConversationsStore.getState().addClueReference(
      {
        workspace_lineage: row.workspace_lineage,
        clue_id: row.clue_id,
        clue_revision: row.clue_revision,
        browse_as_of_chapter: row.as_of_chapter,
      },
      row.title,
    );
    if (added) useLayoutStore.getState().revealChat();
  }

  async function removeRelation(relationId: string) {
    const ok = await submit(
      [{ op: "remove_clue_relation", relation_id: relationId }],
      "详情区删除线索关系",
    );
    if (!ok) {
      setError(useStoryStore.getState().error ?? "删除失败");
      return;
    }
    const next = await fetchStoryClueDetail(workspaceId, variantId ?? "", clueId, null).catch(() => null);
    if (next) setDetail(next);
  }

  return (
    <div className="workspace-detail" aria-label="线索详情">
      <header className="workspace-detail-head">
        <div>
          <h2>{detail?.title ?? "线索"}</h2>
          <p className="workspace-detail-status">{detail?.plan?.kind ? KIND_LABEL[detail.plan.kind] : "用途未定"}</p>
        </div>
        <Button size="compact" aria-label="关闭详情" onClick={onClose}>
          <X size={15} aria-hidden="true" />
        </Button>
      </header>
      {error && <p className="workspace-detail-error" role="alert">{error}</p>}
      {!detail ? (
        <p className="workspace-detail-hint">正在读取线索…</p>
      ) : (
        <>
          <div className="workspace-detail-kinds" role="group" aria-label="线索用途">
            {(["truth", "mislead"] as ClueKind[]).map(kind => (
              <Button
                key={kind}
                size="compact"
                variant={detail.plan?.kind === kind ? "primary" : "secondary"}
                aria-pressed={detail.plan?.kind === kind}
                disabled={busy}
                onClick={() => void savePlan(kind)}
              >
                {KIND_LABEL[kind]}
              </Button>
            ))}
          </div>
          <label className="workspace-detail-field">
            作者事实
            <textarea rows={2} value={truth} onChange={event => setTruth(event.target.value)} />
          </label>
          <label className="workspace-detail-field">
            读者可能的理解
            <textarea rows={2} value={intention} onChange={event => setIntention(event.target.value)} />
          </label>
          <div className="workspace-detail-actions">
            <Button size="compact" disabled={busy} onClick={() => void savePlan(detail.plan?.kind ?? null)}>
              保存说明
            </Button>
            <Button size="compact" variant="quiet" onClick={() => void referenceClue(detail)}>
              带入对话
            </Button>
          </div>

          <section className="workspace-detail-section">
            <h3>正文出处</h3>
            {detail.occurrences.length === 0 ? (
              <p className="workspace-detail-hint">还没有正文出处。可以在正文里选段标记。</p>
            ) : (
              <ul className="workspace-detail-list">
                {detail.occurrences.map(row => (
                  <li key={`${row.path}:${row.start ?? "x"}:${row.end ?? "x"}`}>
                    <span className="workspace-detail-quote">{row.quote}</span>
                    <span className="workspace-detail-meta">
                      第 {row.chapter} 章 · {row.state === "exact" || row.state === "relocated" ? "可定位" : row.state === "missing" ? "原句已不在正文" : row.state === "ambiguous" ? "原句有多处匹配" : "暂不可读"}
                    </span>
                    {row.state === "exact" || row.state === "relocated" ? (
                      <Button
                        size="compact"
                        variant="quiet"
                        onClick={() =>
                          onLocate({
                            path: row.path,
                            chapter: row.chapter,
                            evidence_id: row.evidence_ids[0],
                          })
                        }
                      >
                        <Locate size={14} aria-hidden="true" />定位原文
                      </Button>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className="workspace-detail-section">
            <h3>相关线索</h3>
            {detail.relations.length === 0 ? (
              <p className="workspace-detail-hint">还没有登记关系。</p>
            ) : (
              <ul className="workspace-detail-list">
                {detail.relations.map(row => {
                  const other = row.from_clue_id === detail.clue_id ? row.to_clue_id : row.from_clue_id;
                  const Icon = row.direction === "outgoing" ? ArrowUpRight : row.direction === "incoming" ? ArrowDownLeft : ArrowRight;
                  return (
                    <li key={row.relation_id}>
                      <span className="workspace-detail-relation">
                        <Icon size={14} aria-hidden="true" />
                        {RELATION_LABEL[row.kind]}
                        {row.kind === "supports" && row.direction === "outgoing" ? " → " : " ↔ "}
                        {titleOf(other)}
                      </span>
                      <span className="workspace-detail-meta">{row.reason}</span>
                      <Button size="compact" variant="quiet" disabled={busy} onClick={() => void removeRelation(row.relation_id)}>
                        删除
                      </Button>
                    </li>
                  );
                })}
              </ul>
            )}
            {showRelation ? (
              <RelationForm
                clue={detail}
                clues={clues}
                onDone={() => {
                  setShowRelation(false);
                  void fetchStoryClueDetail(workspaceId, variantId ?? "", clueId, null)
                    .then(next => setDetail(next))
                    .catch(() => undefined);
                }}
              />
            ) : (
              <Button size="compact" onClick={() => setShowRelation(true)}>
                添加关系
              </Button>
            )}
          </section>
        </>
      )}
    </div>
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
  const attention = useStoryStore(state =>
    state.attention?.nodes.find(node => node.node_id === nodeId),
  );
  const [node, setNode] = useState<StoryNodeFull | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setNode(null);
    setError(null);
    if (mode !== "story" || !variantId || !nodeId) return;
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
  }, [workspaceId, variantId, nodeId, mode]);

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
  if (mode === "story" && nodeId) {
    if (error) return <div className="workspace-detail"><p className="workspace-detail-error">{error}</p></div>;
    if (!node) return <div className="workspace-detail"><p className="workspace-detail-hint">正在读取节点…</p></div>;
    return <NodeDetail workspaceId={workspaceId} node={node} attention={attention} onClose={onClose} />;
  }
  return (
    <div className="workspace-detail workspace-detail-empty">
      <p>选择时间轴上的节点或正文里的线索，在这里查看详情。</p>
      {mode === "clues" ? null : <p className="workspace-detail-hint">也可以从正文选段登记线索。</p>}
    </div>
  );
}

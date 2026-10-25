/** 短内容直接阅读、引用与原处修改；搜索和回收只在主动展开后出现。 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { draftDirty, referencePreview, useIdeasStore, useIdeaScope } from "../../state/ideas";
import { NEW_DRAFT, useConversationsStore } from "../../state/conversations";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import { Tabs } from "../ui/Tabs";
import IdeaEditor, { type IdeaActions } from "./IdeaEditor";

function when(value: string): string {
  if (!value) return "";
  const date = new Date(value), now = new Date();
  return date.toDateString() === now.toDateString()
    ? date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })
    : date.toLocaleDateString("zh-CN", { month: "numeric", day: "numeric" });
}

export default function IdeaList({ expanded, onExpandList, actionBusy, ...actions }: IdeaActions & {
  expanded: boolean;
  onExpandList: () => void;
  actionBusy: boolean;
}) {
  const scope = useIdeaScope();
  const { items, deletedItems, total, deletedTotal, loading, loadingMore, error, reading, busy, workspaceId } = useIdeasStore();
  const cursor = useIdeasStore(state => scope.showDeleted ? state.deletedCursor : state.cursor);
  const references = useConversationsStore(state => state.draftIdeas[state.selectedId ?? NEW_DRAFT]);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [moreId, setMoreId] = useState<string | null>(null);
  const container = useRef<HTMLElement>(null);
  const store = useIdeasStore.getState();
  const rows = expanded && scope.showDeleted ? deletedItems : items;
  const recent = rows.slice(0, 3);
  // 有未保存修改时，该记录即使不在最近三条里也保留入口。
  const shown = expanded ? [...rows] : rows.filter(row => recent.includes(row) || row.idea_id === chosenId || (scope.editing[row.idea_id] && draftDirty(scope.editing[row.idea_id])));
  // 删除、恢复历史或修改内容后可能不再匹配当前筛选，原处操作保留到作者主动收起。
  if (scope.selected?.idea_id === chosenId && !shown.some(item => item.idea_id === chosenId)) {
    const selected = scope.selected;
    shown.unshift({ ...selected, excerpt: referencePreview(selected.body, 120), excerpt_truncated: Array.from(selected.body).length > 120, body_chars: Array.from(selected.body).length });
  }

  useEffect(() => { setChosenId(null); setMoreId(null); }, [workspaceId]);
  useEffect(() => { if (expanded && scope.activeId) setChosenId(scope.activeId); }, [expanded, scope.activeId]);
  useLayoutEffect(() => {
    if (chosenId && scope.selected?.idea_id === chosenId) {
      container.current?.querySelector<HTMLElement>(`[data-idea-id="${CSS.escape(chosenId)}"]`)?.scrollIntoView({ block: "nearest" });
    }
  }, [chosenId, scope.selected?.idea_id]);

  function choose(id: string, more = false) {
    setChosenId(id); setMoreId(more ? id : null);
    void store.select(id);
  }

  const list = <div className="idea-list-scroll">
    {loading && !rows.length && <StatusMessage>正在读取灵感…</StatusMessage>}
    {!loading && !shown.length && !error && <EmptyState>{scope.showDeleted && expanded ? "没有已删除的灵感。" : "想到一句，就记下来。"}</EmptyState>}
    <ul>
      {shown.map(item => {
        const draft = scope.editing[item.idea_id];
        const unsaved = draft ? draftDirty(draft) : false;
        const reference = references?.find(ref => ref.idea_id === item.idea_id);
        const referenceState = reference ? reference.revision_id === item.head_revision_id && !unsaved ? "added" : "update" : "add";
        return <li key={item.idea_id} data-idea-id={item.idea_id} className="idea-note">
          {chosenId === item.idea_id && scope.activeId === item.idea_id ? reading || !scope.selected ? <StatusMessage>正在读取原文…</StatusMessage> :
            <IdeaEditor {...actions} referenceState={referenceState} actionBusy={actionBusy} showMore={moreId === item.idea_id} onClose={() => setChosenId(null)} />
            : <>
              <button type="button" className="idea-note-text" onClick={() => choose(item.idea_id)}>
                {item.body_chars === 0 ? "（没有正文）" : item.excerpt}
                {item.excerpt_truncated && <span className="idea-excerpt-more">展开</span>}
              </button>
              <div className="idea-note-footer">
                <time dateTime={item.created_at}>{when(item.created_at)}{unsaved ? " · 未保存" : ""}</time>
                {!item.deleted && <Button variant="quiet" size="compact" className="idea-add-reference" disabled={busy || actionBusy || referenceState === "added"}
                  aria-pressed={referenceState === "added"} onClick={() => void actions.onTakeToChat(item.idea_id)}>
                  {referenceState === "added" ? "✓ 已加入" : referenceState === "update" ? "更新引用" : "加入对话"}
                </Button>}
                <details className="idea-options"><summary aria-label={`管理灵感：${referencePreview(item.excerpt, 24)}`}><MoreHorizontal size={16} aria-hidden="true" /></summary>
                  <div className="idea-options-menu">
                    <Button variant="quiet" size="compact" onClick={() => choose(item.idea_id)}>{item.deleted ? "查看与恢复" : "修改"}</Button>
                    <Button variant="quiet" size="compact" onClick={() => choose(item.idea_id, true)}>历史与更多</Button>
                  </div>
                </details>
              </div>
            </>}
        </li>;
      })}
    </ul>
    {!expanded && (total > 3 || cursor) && <Button variant="quiet" size="compact" className="idea-list-more" onClick={onExpandList}>全部灵感</Button>}
    {expanded && cursor && <Button variant="quiet" size="compact" busy={loadingMore} onClick={() => void store.loadMore()}>加载更多灵感</Button>}
  </div>;

  return <aside ref={container} className="idea-list" aria-label="灵感列表">
    {expanded ? <>
      <div className="idea-list-tools"><Field label="搜索灵感"><input aria-label="搜索灵感" placeholder="找一句记过的话…" value={scope.query} onChange={event => void store.setQuery(event.target.value)} /></Field></div>
      <Tabs label="灵感范围" value={scope.showDeleted ? "deleted" : "active"} onValueChange={value => void store.setShowDeleted(value === "deleted")}
        items={[{value:"active",label:`灵感（${total}）`,content:scope.showDeleted?null:list},{value:"deleted",label:`最近删除（${deletedTotal}）`,content:scope.showDeleted?list:null}]} />
    </> : list}
  </aside>;
}

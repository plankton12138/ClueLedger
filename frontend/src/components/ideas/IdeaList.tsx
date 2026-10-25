import { useLayoutEffect, useRef } from "react";
import { useIdeasStore } from "../../state/ideas";
import { usePagePositions } from "../../state/pagePositions";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { ListItem } from "../ui/ListItem";
import { StatusMessage, EmptyState } from "../ui/StatusMessage";
import { Tabs } from "../ui/Tabs";
export default function IdeaList({ onChoose }: { onChoose: (id?: string) => void }) {
  const store = useIdeasStore();
  const scroll = useRef<HTMLDivElement>(null);
  const positionKey = `${store.workspaceId}:ideas:${store.showDeleted ? "deleted" : "active"}:${store.query}`;
  useLayoutEffect(() => { if (scroll.current) scroll.current.scrollTop = usePagePositions.getState().offsets[positionKey] ?? 0; }, [positionKey, store.items, store.deletedItems]);
  const rows = store.showDeleted ? store.deletedItems : store.items;
  const list = <div className="idea-list-scroll" ref={scroll} onScroll={event => { if (event.currentTarget.clientHeight && rows.length) usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop); }}>
    {Object.entries(store.entryEditors[store.workspaceId ?? ""] ?? {}).filter(([, editor]) => editor.newDraft && editor.dirty).map(([key, editor]) =>
      <ListItem key={key} selected={store.activeKey === key} summary="未保存" onClick={() => { store.selectDraft(key); onChoose(); }}>{editor.draft.title || "未保存的灵感"}</ListItem>)}
    {store.loading && <StatusMessage>正在读取灵感…</StatusMessage>}
    {!store.loading && !rows.length && !store.error && <EmptyState>{store.showDeleted ? "没有已删除的灵感。" : "没有匹配的灵感。"}</EmptyState>}
    <ul>{rows.map(item => <li key={item.idea_id}><ListItem selected={item.idea_id === store.selectedId} summary={<>{item.excerpt}<time>{new Date(item.updated_at).toLocaleString("zh-CN", { hour12: false })}</time></>}
      onClick={() => { void store.select(item.idea_id); onChoose(item.idea_id); }}>{item.title || "（无标题）"}</ListItem></li>)}</ul>
    {(store.showDeleted ? store.deletedCursor : store.cursor) && <Button variant="quiet" busy={store.loadingMore} onClick={() => void store.loadMore()}>加载更多灵感</Button>}
  </div>;
  return <aside className="idea-list module" aria-label="灵感列表">
    <div className="idea-list-tools"><Field label="搜索灵感"><input aria-label="搜索灵感" value={store.query} onChange={event => void store.setQuery(event.target.value)} /></Field>
      <Button variant="primary" size="compact" onClick={() => { store.startNew(); onChoose(); }}>新建</Button></div>
    <Tabs label="灵感范围" value={store.showDeleted ? "deleted" : "active"} onValueChange={value => void store.setShowDeleted(value === "deleted")}
      items={[{ value: "active", label: `灵感（${store.total}）`, content: !store.showDeleted ? list : null }, { value: "deleted", label: `最近删除（${store.deletedTotal}）`, content: store.showDeleted ? list : null }]} />
  </aside>;
}

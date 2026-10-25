import { useState } from "react";
import { useIdeasStore } from "../../state/ideas";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { AppDialog } from "../ui/AppDialog";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import IdeaHistory from "./IdeaHistory";
export default function IdeaEditor({ onTakeToChat, onOpenSource }: { onTakeToChat: (id: string, revision: string, title: string) => void; onOpenSource?: (path: string) => void }) {
  const store = useIdeasStore();
  const [actions, setActions] = useState(false);
  const selected = store.selected;
  if (!selected && store.reading) return <StatusMessage>正在读取灵感…</StatusMessage>;
  if (!selected && !store.newDraft) return <EmptyState>选择一条灵感，或新建记录。</EmptyState>;
  return <div className="idea-editor">
    <div className="idea-editor-heading"><Field label="灵感标题"><input aria-label="灵感标题" value={store.draft.title} onChange={event => store.setDraft({ title: event.target.value })} /></Field>
      <Button variant="primary" disabled={!store.dirty || !store.draft.body.trim()} busy={store.busy} disabledReason="先填写需要保存的内容" onClick={() => void store.save()}>保存</Button>
      <Button variant="quiet" disabled={!store.dirty || store.busy} onClick={store.cancel}>放弃修改</Button>
    </div>
    <div className="idea-editor-meta"><Field label="灵感标签"><input aria-label="灵感标签" value={store.draft.tags} onChange={event => store.setDraft({ tags: event.target.value })} /></Field>
      {selected && !selected.deleted && <><Button disabled={store.dirty || store.busy} disabledReason="先保存当前修改" onClick={() => onTakeToChat(selected.idea_id, selected.revision_id, selected.title)}>带到对话</Button>
        <AppDialog title="条目操作" open={actions} onOpenChange={setActions} trigger={<Button variant="quiet" disabled={store.busy || store.dirty}>条目操作</Button>}>
          <Button variant="danger" disabled={store.busy || store.dirty} onClick={() => void store.trashSelected().then(() => { if (!useIdeasStore.getState().error) setActions(false); })}>删除</Button>
        </AppDialog></>}
      {selected?.deleted && <Button disabled={store.dirty || store.busy} onClick={() => void store.restoreSelected()} title="恢复最近一次未删除的内容">恢复</Button>}
    </div>
    <label className="idea-body-label" htmlFor="idea-body">灵感正文</label>
    <textarea id="idea-body" aria-label="灵感正文" className="ui-input idea-body" value={store.draft.body} onChange={event => store.setDraft({ body: event.target.value })}
      onKeyDown={event => { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") { event.preventDefault(); if (store.dirty && !store.busy) void store.save(); } }} />
    <p className="ui-field-hint">{store.dirty ? "有未保存修改。" : selected ? `已保存 · ${new Date(selected.updated_at).toLocaleString("zh-CN", { hour12: false })}` : "新建草稿"}{store.dirty && selected ? " 保存后可带到对话。" : ""}</p>
    <IdeaHistory />
    {selected && (selected.origin || selected.uses.length > 0) && <details className="idea-sources"><summary>来源与使用位置</summary>
      {selected.origin && <pre>{JSON.stringify(selected.origin, null, 2)}</pre>}
      <ul>{selected.uses.map((use, index) => <li key={String(use.id ?? index)}>
        {typeof use.target_path === "string" && use.target_path ? <Button variant="quiet" onClick={() => onOpenSource?.(use.target_path as string)}>{use.target_path}</Button> : <span>关联条目</span>}
        {typeof use.quote === "string" && <p>{use.quote}</p>}
      </li>)}</ul>
    </details>}
  </div>;
}

import { useIdeasStore } from "../../state/ideas";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
const OPERATIONS: Record<string, string> = { create: "新建", edit: "修改", trash: "删除", restore: "恢复", update: "修改", delete: "删除" };
export default function IdeaHistory() {
  const store = useIdeasStore();
  if (!store.selected) return null;
  const preview = store.preview;
  return <details className="idea-history"><summary>历史</summary>
    <div className="idea-history-content"><ul>{store.revisions.map(item => <li key={item.id}>
      <span>{OPERATIONS[item.operation] ?? "修订"} · {new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}{item.id === store.selected?.revision_id ? " · 当前" : ""}</span>
      <Button size="compact" variant="quiet" onClick={() => void store.previewRevision(item.id)}>查看内容</Button>
    </li>)}</ul>
      {store.revisionsCursor && <Button size="compact" variant="quiet" busy={store.revisionsLoading} onClick={() => void store.loadMoreRevisions()}>更多历史</Button>}
      {store.previewLoading && <StatusMessage>正在读取历史内容…</StatusMessage>}
      {preview && <section className="idea-history-preview" aria-label="历史内容">
        <header><h3>{preview.title || "（无标题）"}</h3><Button variant="quiet" size="compact" onClick={store.closePreview}>关闭预览</Button></header>
        <p>{preview.deleted ? "这是一条删除修订，恢复后仍为已删除状态。" : "恢复后将以这一版内容创建新修订。"}</p>
        <pre>{preview.body}</pre>
        {preview.revision_id !== store.selected.revision_id && <Button disabled={store.dirty || store.busy} disabledReason="先保存或放弃当前修改" onClick={() => void store.restoreRevision(preview.revision_id)}>
          {preview.deleted ? "恢复为删除状态" : "恢复内容"}
        </Button>}
      </section>}
    </div>
  </details>;
}

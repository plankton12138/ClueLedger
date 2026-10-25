/** 历史：只读预览，恢复通过新增修订完成；列表顺序仍按创建时间。 */
import { useIdeasStore, useIdeaScope } from "../../state/ideas";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

const OPERATIONS: Record<string, string> = {
  create: "新建",
  edit: "修改",
  trash: "删除",
  restore: "恢复",
  update: "修改",
  delete: "删除",
};

export default function IdeaHistory({ onOpenSource }: { onOpenSource?: (path: string) => void }) {
  const scope = useIdeaScope();
  const revisions = scope.revisions;
  const cursor = scope.revisionsCursor;
  const preview = useIdeasStore(state => state.preview);
  const previewLoading = useIdeasStore(state => state.previewLoading);
  const revisionsLoading = useIdeasStore(state => state.revisionsLoading);
  const busy = useIdeasStore(state => state.busy);
  const store = useIdeasStore.getState();
  const selected = scope.selected;
  if (!selected) return null;

  return (
    <details className="idea-history">
      <summary>历史（{revisions.length}）</summary>
      <div className="idea-history-content">
        <ul>
          {revisions.map(item => (
            <li key={item.id}>
              <span>
                {OPERATIONS[item.operation] ?? "修订"} ·{" "}
                {new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}
                {item.id === selected.revision_id ? " · 当前" : ""}
                {` · ${item.chars} 字`}
              </span>
              <Button size="compact" variant="quiet" onClick={() => void store.previewRevision(item.id)}>
                查看内容
              </Button>
            </li>
          ))}
        </ul>
        {cursor && (
          <Button size="compact" variant="quiet" busy={revisionsLoading} onClick={() => void store.loadMoreRevisions()}>
            更多历史
          </Button>
        )}
        {previewLoading && <StatusMessage>正在读取历史内容…</StatusMessage>}
        {preview && (
          <section className="idea-history-preview" aria-label="历史内容">
            <header>
              <h3>{preview.body.split("\n").find(line => line.trim()) ?? "（没有正文）"}</h3>
              <Button variant="quiet" size="compact" onClick={store.closePreview}>
                关闭预览
              </Button>
            </header>
            <p>
              {preview.deleted
                ? "这是一条删除修订，恢复后仍为已删除状态。"
                : "恢复后将以这一版内容创建新修订。"}
            </p>
            <pre>{preview.body}</pre>
            {preview.revision_id !== selected.revision_id && (
              <Button
                disabled={busy}
                onClick={() => void store.restoreRevision(preview.revision_id)}
              >
                {preview.deleted ? "恢复为删除状态" : "恢复内容"}
              </Button>
            )}
          </section>
        )}
        {selected.uses.length > 0 && (
          <section className="idea-history-preview" aria-label="使用位置">
            <h3>使用位置</h3>
            <ul>
              {selected.uses.map((use, index) => (
                <li key={String(use.id ?? index)}>
                  {typeof use.target_path === "string" && use.target_path ? (
                    <Button variant="quiet" onClick={() => onOpenSource?.(use.target_path as string)}>
                      {use.target_path}
                    </Button>
                  ) : (
                    <span>关联条目</span>
                  )}
                  {typeof use.quote === "string" && <p>{use.quote}</p>}
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>
    </details>
  );
}

/**
 * 灵感列表：按创建时间倒序，用原文识别内容。
 *
 * 标题与标签是旧字段，列表不再显示；正文为空的旧异常条目给出可理解的诊断，
 * 不拿内部 ID 充当标题。摘要被截断时明确提供「展开」，先读取完整修订再编辑。
 */
import { useLayoutEffect, useRef } from "react";
import { draftDirty, useIdeasStore, useIdeaScope } from "../../state/ideas";
import { usePagePositions } from "../../state/pagePositions";
import { Button } from "../ui/Button";
import { Field } from "../ui/Field";
import { ListItem } from "../ui/ListItem";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import { Tabs } from "../ui/Tabs";

function when(value: string): string {
  if (!value) return "";
  return new Date(value).toLocaleString("zh-CN", { hour12: false });
}

export default function IdeaList({ onChoose }: { onChoose: (id?: string) => void }) {
  const scope = useIdeaScope();
  const items = useIdeasStore(state => state.items);
  const deletedItems = useIdeasStore(state => state.deletedItems);
  const total = useIdeasStore(state => state.total);
  const deletedTotal = useIdeasStore(state => state.deletedTotal);
  const loading = useIdeasStore(state => state.loading);
  const loadingMore = useIdeasStore(state => state.loadingMore);
  const error = useIdeasStore(state => state.error);
  const cursor = useIdeasStore(state => (scope.showDeleted ? state.deletedCursor : state.cursor));
  const store = useIdeasStore.getState();
  const scroll = useRef<HTMLDivElement>(null);
  const positionKey = `${store.workspaceId}:ideas:${scope.showDeleted ? "deleted" : "active"}:${scope.query}`;
  const rows = scope.showDeleted ? deletedItems : items;

  useLayoutEffect(() => {
    if (scroll.current) {
      scroll.current.scrollTop = usePagePositions.getState().offsets[positionKey] ?? 0;
    }
  }, [positionKey, items, deletedItems]);

  const list = (
    <div
      className="idea-list-scroll"
      ref={scroll}
      onScroll={event => {
        if (event.currentTarget.clientHeight && rows.length) {
          usePagePositions.getState().offset(positionKey, event.currentTarget.scrollTop);
        }
      }}
    >
      {loading && <StatusMessage>正在读取灵感…</StatusMessage>}
      {!loading && !rows.length && !error && (
        <EmptyState>{scope.showDeleted ? "没有已删除的灵感。" : "还没有记录；想到一句就写在上面。"}</EmptyState>
      )}
      <ul>
        {rows.map(item => {
          const draft = scope.editing[item.idea_id];
          const unsaved = draft ? draftDirty(draft) : false;
          const empty = item.body_chars === 0;
          return (
            <li key={item.idea_id}>
              <ListItem
                selected={item.idea_id === scope.activeId}
                summary={
                  <>
                    {empty ? "正文为空，可能是旧版本留下的异常条目。" : item.excerpt}
                    <time>
                      {when(item.created_at)}
                      {unsaved ? " · 未保存" : ""}
                      {item.excerpt_truncated ? ` · 共 ${item.body_chars} 字` : ""}
                    </time>
                  </>
                }
                onClick={() => {
                  void store.select(item.idea_id);
                  onChoose(item.idea_id);
                }}
              >
                {empty ? "（没有正文）" : item.excerpt.split("\n")[0]}
                {item.excerpt_truncated && <span className="idea-excerpt-more">展开</span>}
              </ListItem>
            </li>
          );
        })}
      </ul>
      {cursor && (
        <Button variant="quiet" busy={loadingMore} onClick={() => void store.loadMore()}>
          加载更多灵感
        </Button>
      )}
    </div>
  );

  return (
    <aside className="idea-list module" aria-label="灵感列表">
      <div className="idea-list-tools">
        <Field label="搜索灵感">
          <input
            aria-label="搜索灵感"
            value={scope.query}
            onChange={event => void store.setQuery(event.target.value)}
          />
        </Field>
      </div>
      <Tabs
        label="灵感范围"
        value={scope.showDeleted ? "deleted" : "active"}
        onValueChange={value => void store.setShowDeleted(value === "deleted")}
        items={[
          { value: "active", label: `灵感（${total}）`, content: scope.showDeleted ? null : list },
          { value: "deleted", label: `最近删除（${deletedTotal}）`, content: scope.showDeleted ? list : null },
        ]}
      />
    </aside>
  );
}

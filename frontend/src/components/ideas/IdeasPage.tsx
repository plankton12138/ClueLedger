/**
 * 灵感本页面（0.8 C）。
 *
 * 左侧列表与最近删除，右侧编辑当前灵感。灵感只属于作品，不自动进入写作上下文；
 * 「带到对话」只把当前修订放进下一轮参考，不发送、不改正文。未保存草稿在切换
 * 条目前会阻止操作，避免静默丢弃。
 */

import { useEffect } from "react";

import type { WorkspacePage } from "../../state/navigation";
import { useIdeasStore } from "../../state/ideas";

interface Props {
  workspaceId: string;
  ideaId?: string;
  onNavigate: (page: WorkspacePage) => void;
  onTakeToChat: (ideaId: string, revisionId: string, title: string) => void;
}

export default function IdeasPage({ workspaceId, ideaId, onNavigate, onTakeToChat }: Props) {
  const store = useIdeasStore();

  useEffect(() => {
    void useIdeasStore.getState().load(workspaceId);
  }, [workspaceId]);

  useEffect(() => {
    if (ideaId) void useIdeasStore.getState().select(ideaId);
  }, [ideaId]);

  const rows = store.showDeleted ? store.deletedItems : store.items;
  const selected = store.selected;
  const canSave = store.dirty && store.draft.body.trim().length > 0 && !store.busy;

  return (
    <div className="flex min-h-0 flex-1 overflow-hidden">
      <aside className="toc-pane flex w-80 min-h-0 flex-none flex-col border-r border-[#e2dbc6]">
        <div className="flex flex-none items-center gap-1 px-3 py-2">
          <input
            aria-label="搜索灵感"
            className="well-input min-w-0 flex-1 px-2 py-1 text-[11px]"
            placeholder="搜索灵感…"
            value={store.query}
            onChange={(event) => void useIdeasStore.getState().setQuery(event.target.value)}
          />
          <button
            className="btn btn-gold px-2 py-1 text-[11px]"
            onClick={() => useIdeasStore.getState().startNew()}
          >
            新建
          </button>
        </div>
        <div className="flex flex-none items-center gap-2 px-3 pb-2 text-[11px]">
          <button
            className={!store.showDeleted ? "underline" : "text-mute underline"}
            onClick={() => void useIdeasStore.getState().setShowDeleted(false)}
          >
            灵感（{store.items.length}）
          </button>
          <button
            className={store.showDeleted ? "underline" : "text-mute underline"}
            onClick={() => void useIdeasStore.getState().setShowDeleted(true)}
          >
            最近删除（{store.deletedItems.length}）
          </button>
        </div>
        <ul className="min-h-0 flex-1 overflow-y-auto px-1 pb-2">
          {store.loading && <li className="px-2 py-1 text-[11px] text-mute">正在读取…</li>}
          {!store.loading && rows.length === 0 && (
            <li className="px-2 py-1 text-[11px] text-mute">
              {store.showDeleted ? "没有已删除的灵感。" : "还没有灵感。可以新建，或在对话里让模型记下来。"}
            </li>
          )}
          {rows.map((item) => (
            <li key={item.idea_id}>
              <button
                className={`toc-row w-full px-2 py-1 text-left text-[12px] ${
                  store.selectedId === item.idea_id ? "on" : ""
                }`}
                onClick={() => void useIdeasStore.getState().select(item.idea_id)}
              >
                <span className="block truncate font-bold">{item.title || "（无标题）"}</span>
                <span className="block truncate text-[10px] text-mute">{item.excerpt}</span>
              </button>
            </li>
          ))}
        </ul>
      </aside>

      <main className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        {(store.error || store.notice) && (
          <div
            className={`flex flex-none items-center gap-3 border-b px-4 py-2 text-[11px] ${
              store.error
                ? "border-red-edge bg-red-bg text-red"
                : "border-amber-edge bg-amber-bg text-amber"
            }`}
          >
            <span className="min-w-0 flex-1">{store.error ?? store.notice}</span>
            <button className="underline" onClick={() => useIdeasStore.getState().clearNotice()}>
              知道了
            </button>
          </div>
        )}
        {!store.selected && !store.newDraft ? (
          <div className="flex flex-1 items-center justify-center text-[12px] text-mute">
            选择左侧灵感查看详情，或新建一条。
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-5 py-4">
            <div className="flex flex-none items-center gap-2">
              <input
                aria-label="灵感标题"
                className="well-input min-w-0 flex-1 px-2 py-1 font-serif text-sm"
                placeholder="标题（可留空）"
                value={store.draft.title}
                disabled={store.busy}
                onChange={(event) =>
                  useIdeasStore.getState().setDraft({ title: event.target.value })
                }
              />
              <button
                className="btn btn-gold px-3 py-1 text-[11px]"
                disabled={!canSave}
                onClick={() => void useIdeasStore.getState().save()}
              >
                保存
              </button>
              <button
                className="btn btn-gray px-3 py-1 text-[11px]"
                disabled={!store.dirty || store.busy}
                onClick={() => useIdeasStore.getState().cancel()}
              >
                取消
              </button>
            </div>
            <div className="mt-2 flex flex-none items-center gap-2 text-[11px]">
              <input
                aria-label="灵感标签"
                className="well-input min-w-0 flex-1 px-2 py-1"
                placeholder="标签，用顿号或逗号分隔"
                value={store.draft.tags}
                disabled={store.busy}
                onChange={(event) =>
                  useIdeasStore.getState().setDraft({ tags: event.target.value })
                }
              />
              {selected && !selected.deleted && (
                <>
                  <button
                    className="underline text-red"
                    disabled={store.busy || store.dirty}
                    onClick={() => void useIdeasStore.getState().trashSelected()}
                  >
                    删除
                  </button>
                  <button
                    className="underline"
                    disabled={store.busy || store.dirty}
                    onClick={() =>
                      onTakeToChat(selected.idea_id, selected.revision_id, selected.title)
                    }
                  >
                    带到对话
                  </button>
                </>
              )}
              {selected?.deleted && (
                <button
                  className="underline"
                  disabled={store.busy || store.dirty}
                  onClick={() => void useIdeasStore.getState().restoreSelected()}
                >
                  恢复
                </button>
              )}
            </div>
            <textarea
              aria-label="灵感正文"
              className="mt-2 min-h-0 flex-1 resize-none rounded border border-[#d8d0ba] bg-[#fffdf7] px-3 py-2 text-[13px] leading-7 outline-none"
              style={{ minHeight: "240px" }}
              placeholder="写一条想法、一个疑点，或一句还不想放进正文的对白。"
              value={store.draft.body}
              disabled={store.busy}
              onChange={(event) => useIdeasStore.getState().setDraft({ body: event.target.value })}
              onKeyDown={(event) => {
                if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
                  event.preventDefault();
                  if (canSave) void useIdeasStore.getState().save();
                }
              }}
            />
            {selected && (
              <div className="mt-3 flex-none border-t border-[#e2dbc6] pt-2 text-[11px]">
                <div className="text-mute">
                  当前修订 {selected.revision_id.slice(0, 12)} · 最近更新 {selected.updated_at}
                  {selected.deleted ? " · 已删除" : ""}
                </div>
                <ul className="mt-1 space-y-0.5">
                  {store.revisions.slice(0, 8).map((revision) => (
                    <li key={revision.id} className="flex items-center gap-2">
                      <span className="min-w-0 flex-1 truncate">
                        {revision.operation}｜{revision.title || "（无标题）"}｜{revision.chars} 字符
                      </span>
                      <button
                        className="underline text-[10px]"
                        disabled={store.busy || store.dirty}
                        onClick={() =>
                          void useIdeasStore.getState().restoreRevision(revision.id)
                        }
                      >
                        恢复这一版
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
        <div className="flex flex-none items-center gap-2 border-t border-[#e2dbc6] px-5 py-2 text-[11px]">
          <button
            className="underline"
            onClick={() => onNavigate({ kind: "writing", workspaceId })}
          >
            回到写作
          </button>
        </div>
      </main>
    </div>
  );
}

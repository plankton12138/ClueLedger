/**
 * 条目编辑：只编辑正文，原位保存。
 *
 * 「完成」提交当前修改稿；「放弃修改」回到保存基线；删除、恢复与撤销都走冻结命令。
 * 有未保存修改时不直接删除，先让作者决定保存、放弃还是返回编辑。
 */
import { useState } from "react";
import { runningConversation, useConversationsStore } from "../../state/conversations";
import { draftDirty, useIdeasStore, useIdeaScope } from "../../state/ideas";
import { useModelsStore } from "../../state/models";
import { Button } from "../ui/Button";
import { AppDialog } from "../ui/AppDialog";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";
import { useWorkspaceStore } from "../../state/workspace";
import IdeaActivityList from "./IdeaActivityList";
import IdeaHistory from "./IdeaHistory";

interface Props {
  /** 有脏稿时由上层先保存，再用保存结果的具体修订引用。 */
  onTakeToChat: (id: string) => void;
  /** 一次点击发起只读展开；已有讨论时打开它继续。 */
  onExpand: (id: string) => void;
  /** 打开某次讨论；没有会话时由上层决定回到哪里。 */
  onOpenConversation?: (conversationId: string) => void;
  /** 把这条灵感带进某章的写作要求。 */
  onUseForWriting?: (ideaId: string, path: string, title: string) => void;
  onOpenSource?: (path: string) => void;
}

export default function IdeaEditor({
  onTakeToChat,
  onExpand,
  onOpenConversation,
  onUseForWriting,
  onOpenSource,
}: Props) {
  const scope = useIdeaScope();
  const reading = useIdeasStore(state => state.reading);
  const busy = useIdeasStore(state => state.busy);
  const selected = scope.selected;
  const [confirmDiscard, setConfirmDiscard] = useState(false);
  const [writing, setWriting] = useState(false);
  const chapters = useWorkspaceStore(state => state.catalog.chapters);
  const modelReady = useModelsStore(state => state.selection.available);
  const busyElsewhere = useConversationsStore(runningConversation);
  const store = useIdeasStore.getState();
  const draft = scope.activeId ? scope.editing[scope.activeId] : undefined;
  const dirty = draft ? draftDirty(draft) : false;

  if (!selected) {
    return reading ? <StatusMessage>正在读取灵感…</StatusMessage> : <EmptyState>选择一条灵感查看或修改。</EmptyState>;
  }

  const body = draft?.body ?? selected.body;
  const heading = body.split("\n").find(line => line.trim()) ?? "";

  return (
    <div className="idea-editor">
      <header className="idea-editor-heading">
        <h2 className="idea-editor-title">{heading || "（没有正文）"}</h2>
        {!selected.deleted && (
          <>
            <Button
              variant="primary"
              disabled={!dirty}
              busy={busy}
              disabledReason="没有需要保存的修改"
              onClick={() => void store.saveEntry(selected.idea_id)}
            >
              完成
            </Button>
            <Button variant="quiet" disabled={!dirty || busy} onClick={() => store.discardEntry(selected.idea_id)}>
              放弃修改
            </Button>
          </>
        )}
      </header>

      <label className="idea-body-label" htmlFor="idea-body">
        灵感正文
      </label>
      <textarea
        id="idea-body"
        aria-label="灵感正文"
        className="ui-input idea-body"
        value={body}
        readOnly={selected.deleted}
        onChange={event => store.setEntryBody(selected.idea_id, event.target.value)}
        onKeyDown={event => {
          if (event.nativeEvent.isComposing || event.nativeEvent.keyCode === 229) return;
          if ((event.ctrlKey || event.metaKey) && event.key === "Enter" && dirty && !busy && !selected.deleted) {
            event.preventDefault();
            void store.saveEntry(selected.idea_id);
          }
        }}
      />
      <p className="ui-field-hint">
        {selected.deleted
          ? "这条灵感在最近删除里，恢复后才能修改。"
          : dirty
            ? "有未保存修改。"
            : `已保存 · ${new Date(selected.updated_at).toLocaleString("zh-CN", { hour12: false })}`}
      </p>

      <div className="idea-editor-meta">
        {!selected.deleted && (
          <>
            <Button
              disabled={busy}
              busy={busy}
              onClick={() => onTakeToChat(selected.idea_id)}
            >
              带到对话
            </Button>
            <Button
              disabled={busy || !modelReady || busyElsewhere !== null}
              disabledReason={
                !modelReady ? "先连接模型再展开" : "已有一轮在进行，等它结束或停止它"
              }
              onClick={() => onExpand(selected.idea_id)}
            >
              展开讨论
            </Button>
            {onUseForWriting && (
              <Button
                disabled={busy || !modelReady || busyElsewhere !== null}
                disabledReason={
                  !modelReady
                    ? "先连接模型再用于写作"
                    : "已有一轮在进行，等它结束或停止它"
                }
                onClick={() => setWriting(true)}
              >
                用于写作
              </Button>
            )}
            <Button variant="danger" disabled={busy} onClick={() => (dirty ? setConfirmDiscard(true) : void store.trashEntry(selected.idea_id))}>
              删除
            </Button>
          </>
        )}
        {selected.deleted && (
          <>
            <Button disabled={busy} onClick={() => void store.restoreEntry(selected.idea_id)}>
              恢复
            </Button>
            {scope.lastTrashed === selected.idea_id && (
              <Button variant="quiet" disabled={busy} onClick={() => void store.undoTrash()}>
                撤销删除
              </Button>
            )}
          </>
        )}
      </div>

      <IdeaHistory />
      <IdeaActivityList onOpenConversation={onOpenConversation} onOpenSource={onOpenSource} />

      {selected.origin && (
        <details className="idea-sources">
          <summary>来源</summary>
          <pre>{JSON.stringify(selected.origin, null, 2)}</pre>
        </details>
      )}

      <AppDialog title="把这条灵感用到哪一章" open={writing} onOpenChange={setWriting}>
        <div className="ui-action-list">
          {chapters.length === 0 && <p className="ui-field-hint">这个作品还没有正文章节。</p>}
          {chapters.map(chapter => (
            <Button
              key={chapter.path}
              variant="quiet"
              onClick={() => {
                setWriting(false);
                onUseForWriting?.(selected.idea_id, chapter.path, chapter.title);
              }}
            >
              {chapter.title || chapter.path}
            </Button>
          ))}
        </div>
      </AppDialog>

      <AppDialog
        title="这条灵感有未保存的修改"
        open={confirmDiscard}
        onOpenChange={setConfirmDiscard}
      >
        <div className="ui-action-list">
          <Button onClick={() => setConfirmDiscard(false)}>返回编辑</Button>
          <Button
            onClick={() => {
              setConfirmDiscard(false);
              void store.saveEntry(selected.idea_id).then(result => {
                if (result.status !== "failed") void store.trashEntry(selected.idea_id);
              });
            }}
          >
            保存后删除
          </Button>
          <Button
            variant="danger"
            onClick={() => {
              setConfirmDiscard(false);
              store.discardEntry(selected.idea_id);
              void store.trashEntry(selected.idea_id);
            }}
          >
            放弃修改并删除
          </Button>
        </div>
      </AppDialog>
    </div>
  );
}

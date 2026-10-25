/** 灵感列表、会话草稿与历史。带到对话只引用已保存修订，不自动发送。 */
import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { WorkspacePage } from "../../state/navigation";
import { useIdeasStore } from "../../state/ideas";
import { useLayoutStore } from "../../state/layout";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import IdeaList from "./IdeaList";
import IdeaEditor from "./IdeaEditor";
interface Props {
  workspaceId: string; ideaId?: string; onNavigate: (page: WorkspacePage) => void;
  onTakeToChat: (id: string, revision: string, title: string) => void;
  onOpenSource?: (path: string) => void;
}
export default function IdeasPage({ workspaceId, ideaId, onNavigate, onTakeToChat, onOpenSource }: Props) {
  const store = useIdeasStore();
  const scale = useLayoutStore(state => state.fontScale);
  const [width, setWidth] = useState(1440), [detail, setDetail] = useState(Boolean(ideaId));
  const root = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    if (!root.current) return;
    const observer = new ResizeObserver(entries => { if (entries[0].contentRect.width) setWidth(entries[0].contentRect.width); });
    observer.observe(root.current); return () => observer.disconnect();
  }, []);
  useEffect(() => { void useIdeasStore.getState().load(workspaceId).then(() => { if (ideaId) void useIdeasStore.getState().select(ideaId); }); }, [workspaceId]);
  useEffect(() => { if (ideaId && store.workspaceId === workspaceId) { if (store.selectedId !== ideaId) void useIdeasStore.getState().select(ideaId); setDetail(true); } }, [ideaId, workspaceId]);
  const narrow = width < 900 * Math.max(1, scale / 100);
  return <div className="ideas-page" ref={root} data-narrow={narrow}>
    <div className="idea-list-slot" hidden={narrow && detail}><IdeaList onChoose={id => { setDetail(true); onNavigate({ kind: "ideas", workspaceId, ideaId: id }); }} /></div>
    <section className="idea-detail module" hidden={narrow && !detail} aria-label="灵感详情">
      <header className="idea-page-tools">{narrow && <Button size="compact" onClick={() => setDetail(false)}>返回列表</Button>}
        <Button variant="quiet" size="compact" onClick={() => onNavigate({ kind: "writing", workspaceId })}>回到写作</Button>
      </header>
      {(store.error || store.notice) && <StatusMessage tone={store.error ? "error" : "info"} action={<Button variant="quiet" size="compact" onClick={store.clearNotice}>知道了</Button>}>{store.error ?? store.notice}</StatusMessage>}
      <IdeaEditor onTakeToChat={onTakeToChat} onOpenSource={onOpenSource} />
    </section>
  </div>;
}

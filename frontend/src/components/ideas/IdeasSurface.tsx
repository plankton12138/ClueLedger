/**
 * 灵感悬浮面板（0.8 I2）。
 *
 * 全应用只有这一个容器：宽屏是非模态侧栏，窄屏是近全屏容器，「展开查看」只换更大的区域。
 * 列表、记录与编辑都复用 `components/ideas/` 下的内容组件，切换容器不重建草稿。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { navigate } from "../../state/navigation";
import { useConversationsStore } from "../../state/conversations";
import { useIdeasStore, useIdeaScope } from "../../state/ideas";
import { useIdeasSurfaceStore } from "../../state/ideasSurface";
import { useLayoutStore } from "../../state/layout";
import { useWorkspaceStore } from "../../state/workspace";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";
import IdeaCapture, { type IdeaCaptureHandle } from "./IdeaCapture";
import IdeaEditor from "./IdeaEditor";
import IdeaList from "./IdeaList";

type Presentation = "panel" | "expanded" | "compact";

function usePresentation(): Presentation {
  const expanded = useIdeasSurfaceStore(state => state.expanded);
  const scale = useLayoutStore(state => state.fontScale);
  const [width, setWidth] = useState(() =>
    typeof window === "undefined" ? 1440 : window.innerWidth,
  );
  useEffect(() => {
    const measure = () => setWidth(window.innerWidth);
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  // 窄窗口或字体放大后空间不足时，面板改成近全屏容器并限制背景焦点。
  if (width < 900 * Math.max(1, scale / 100)) return "compact";
  return expanded ? "expanded" : "panel";
}

export default function IdeasSurface() {
  const open = useIdeasSurfaceStore(state => state.open);
  const expanded = useIdeasSurfaceStore(state => state.expanded);
  const hide = useIdeasSurfaceStore(state => state.hide);
  const setExpanded = useIdeasSurfaceStore(state => state.setExpanded);
  const workspaceId = useWorkspaceStore(state => state.currentId);
  const scope = useIdeaScope();
  const error = useIdeasStore(state => state.error);
  const notice = useIdeasStore(state => state.notice);
  const clearNotice = useIdeasStore(state => state.clearNotice);
  const presentation = usePresentation();
  const [detail, setDetail] = useState(false);
  // 顶栏高度随字号与窗口宽度变化：窄屏容器从顶栏下方开始，避免盖住全局操作。
  const [top, setTop] = useState(60);
  const frame = useRef<HTMLDivElement>(null);
  const capture = useRef<IdeaCaptureHandle>(null);
  const activeId = scope.activeId;

  const show = useIdeasSurfaceStore(state => state.show);

  // 应用快捷键：Alt+I 呼出/收起；只在有作品时生效，不拦截系统保留组合。
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (!event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
      if (event.key.toLowerCase() !== "i") return;
      if (!useWorkspaceStore.getState().currentId) return;
      event.preventDefault();
      if (useIdeasSurfaceStore.getState().open) hide();
      else show();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [hide, show]);

  // 打开时载入当前作品的灵感，并处理旧链接带过来的条目。
  useEffect(() => {
    if (!open || !workspaceId) return;
    void useIdeasStore
      .getState()
      .load(workspaceId)
      .then(() => {
        const entry = useIdeasSurfaceStore.getState().consumeEntry();
        if (entry) void useIdeasStore.getState().select(entry);
      });
  }, [open, workspaceId]);

  useEffect(() => {
    if (activeId) setDetail(true);
  }, [activeId]);

  useLayoutEffect(() => {
    if (!open) return;
    const header = document.querySelector<HTMLElement>(".app-header");
    const measure = () => setTop(header?.offsetHeight ?? 60);
    measure();
    if (!header) return;
    const observer = new ResizeObserver(measure);
    observer.observe(header);
    return () => observer.disconnect();
  }, [open]);

  // 打开记录入口时聚焦输入框；从引用打开时聚焦对应记录。
  useLayoutEffect(() => {
    if (!open) return;
    if (detail && activeId) {
      frame.current?.querySelector<HTMLTextAreaElement>("#idea-body")?.focus();
      return;
    }
    capture.current?.focus();
    // detail/activeId 变化时不抢焦点：作者可能正在别处输入。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const close = useCallback(() => {
    setDetail(false);
    hide();
  }, [hide]);

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      // 内部对话框自己处理 Esc；这里只收起面板。
      if ((event.target as HTMLElement).closest('[role="dialog"]:not(.idea-surface)')) return;
      event.stopPropagation();
      close();
      return;
    }
    if (event.key !== "Tab" || presentation !== "compact" || !frame.current) return;
    const focusable = frame.current.querySelectorAll<HTMLElement>(
      'button:not([disabled]), [href], input:not([disabled]), textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])',
    );
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  if (!open || !workspaceId) return null;

  const takeToChat = (ideaId: string, revisionId: string, preview: string) => {
    useConversationsStore.getState().addIdeaReference(ideaId, revisionId, preview);
    close();
    navigate({ kind: "writing", workspaceId });
  };

  return (
    <>
      {presentation === "compact" && (
        <div className="idea-surface-scrim" style={{ top }} onClick={close} />
      )}
      <div
        className="idea-surface"
        ref={frame}
        style={presentation === "compact" ? { top } : undefined}
        data-presentation={presentation}
        role={presentation === "compact" ? "dialog" : "complementary"}
        aria-modal={presentation === "compact" ? true : undefined}
        aria-label="灵感"
        onKeyDown={onKeyDown}
      >
        <header className="idea-surface-tools">
          {detail ? (
            <Button size="compact" variant="quiet" onClick={() => setDetail(false)}>
              返回列表
            </Button>
          ) : (
            <span className="idea-surface-title">灵感</span>
          )}
          <span className="idea-surface-spacer" />
          <Button
            size="compact"
            variant="quiet"
            aria-pressed={expanded}
            onClick={() => setExpanded(!expanded)}
          >
            {expanded ? "收起为侧栏" : "展开查看"}
          </Button>
          <Button size="compact" variant="quiet" onClick={close}>
            关闭
          </Button>
        </header>
        <div className="idea-surface-body">
          <IdeaCapture ref={capture} />
          {(error || notice) && (
            <div className="idea-surface-status">
              <StatusMessage
                tone={error ? "error" : "info"}
                action={
                  <Button variant="quiet" size="compact" onClick={clearNotice}>
                    知道了
                  </Button>
                }
              >
                {error ?? notice}
              </StatusMessage>
            </div>
          )}
          {detail && activeId ? (
            <IdeaEditor onTakeToChat={takeToChat} />
          ) : (
            <IdeaList onChoose={() => setDetail(true)} />
          )}
        </div>
      </div>
    </>
  );
}

/**
 * 灵感悬浮面板（0.8 I2）。
 *
 * 全应用只有这一个容器：宽屏是非模态侧栏，窄屏是近全屏容器，「展开查看」只换更大的区域。
 * 列表、记录与编辑都复用 `components/ideas/` 下的内容组件，切换容器不重建草稿。
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { navigate } from "../../state/navigation";
import { useConversationsStore } from "../../state/conversations";
import { referencePreview, useIdeasStore, useIdeaScope } from "../../state/ideas";
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
  // 面板发起的动作（用于写作、打开讨论、打开正文）失败时的说明：这些错误不在灵感状态里。
  const [actionError, setActionError] = useState<string | null>(null);
  const presentation = usePresentation();
  const [detail, setDetail] = useState(false);
  // 顶栏高度随字号与窗口宽度变化：窄屏容器从顶栏下方开始，避免盖住全局操作。
  const [top, setTop] = useState(60);
  const frame = useRef<HTMLDivElement>(null);
  const capture = useRef<IdeaCaptureHandle>(null);
  const activeId = scope.activeId;

  // 换一条灵感就清掉上一条的失败提示，避免把原因挂在别的条目上。
  useEffect(() => {
    setActionError(null);
  }, [activeId]);

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
    setActionError(null);
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

  const expand = async (ideaId: string) => {
    // 有脏稿先保存：展开绑定保存结果的那一版，不引用还没落盘的草稿。
    const saved = await useIdeasStore.getState().prepareReference(ideaId);
    if (!saved) return;
    const result = await useConversationsStore.getState().expandIdea({
      ideaId: saved.ideaId,
      revisionId: saved.revisionId,
      preview: referencePreview(saved.body),
    });
    // 失败时留在面板里：把原因显示在面板内，不把作者丢到一个看不见错误的地方。
    if (!result) {
      setActionError(useConversationsStore.getState().error ?? "没有建起这次讨论，检查模型连接后重试。");
      return;
    }
    close();
    navigate({ kind: "writing", workspaceId });
  };

  const openConversation = async (conversationId: string) => {
    await useConversationsStore.getState().select(conversationId);
    if (useConversationsStore.getState().selectedId !== conversationId) {
      setActionError("这次讨论的会话打不开，可能已经被删除。记录仍然保留。");
      return;
    }
    close();
    navigate({ kind: "writing", workspaceId });
  };

  const openSource = async (path: string) => {
    await useWorkspaceStore.getState().openPath(path);
    const opened = useWorkspaceStore.getState();
    // 打不开就留在面板里：跳到写作页只会看到一个空正文。
    if (opened.selectedPath !== path) {
      setActionError(`打不开 ${path}：文件可能已经被移动或删除。`);
      return;
    }
    close();
    navigate({ kind: "writing", workspaceId });
  };

  const useForWriting = async (ideaId: string, path: string, title: string) => {
    // 有脏稿先保存：带进写作要求的是保存后的那一版。
    const saved = await useIdeasStore.getState().prepareReference(ideaId);
    if (!saved) return;
    // 先真正打开目标章节：受理时冻结的就是它，作者也能立刻看到改动落在哪里。
    await useWorkspaceStore.getState().openPath(path);
    const opened = useWorkspaceStore.getState();
    if (opened.selectedPath !== path || opened.file?.path !== path) {
      setActionError(`打不开目标章节 ${path}：文件可能已经被移动或删除。`);
      return;
    }
    const result = await useConversationsStore.getState().writeFromIdea({
      ideaId: saved.ideaId,
      revisionId: saved.revisionId,
      preview: referencePreview(saved.body),
      title,
      path,
      digest: opened.file.digest,
    });
    // 受理失败时留在面板里：说明原因，保留当前选择与草稿。
    if (!result) {
      setActionError(
        useConversationsStore.getState().error ?? "没有提交成功：检查模型连接后重试。",
      );
      return;
    }
    close();
    navigate({ kind: "writing", workspaceId });
  };

  const takeToChat = async (ideaId: string) => {
    // 有脏稿先保存：引用绑定保存返回的具体修订，不引用一个还没落盘的版本。
    const saved = await useIdeasStore.getState().prepareReference(ideaId);
    if (!saved) return;
    useConversationsStore.getState().addIdeaReference({
      ideaId: saved.ideaId,
      revisionId: saved.revisionId,
      preview: referencePreview(saved.body),
      chars: Array.from(saved.body).length,
    });
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
          {(error || notice || actionError) && (
            <div className="idea-surface-status">
              <StatusMessage
                tone={error || actionError ? "error" : "info"}
                action={
                  <Button
                    variant="quiet"
                    size="compact"
                    onClick={() => {
                      setActionError(null);
                      clearNotice();
                    }}
                  >
                    知道了
                  </Button>
                }
              >
                {error ?? actionError ?? notice}
              </StatusMessage>
            </div>
          )}
          {detail && activeId ? (
            <IdeaEditor
              onTakeToChat={takeToChat}
              onExpand={expand}
              onOpenConversation={openConversation}
              onOpenSource={openSource}
              onUseForWriting={useForWriting}
            />
          ) : (
            <IdeaList onChoose={() => setDetail(true)} />
          )}
        </div>
      </div>
    </>
  );
}

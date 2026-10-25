/**
 * 宽阅读视图（V05-2C）。
 *
 * 打开的是**同一条**回复：内容来自会话状态，所以生成中打开也会继续更新；关闭后回到
 * 原消息与原滚动位置，不创建第二份消息、也不新建会话。切到别的会话时关闭，
 * 不显示错会话的内容。
 */

import { AppDialog } from "../ui/AppDialog";

import type { ChatMessage } from "../../state/chatMessages";
import { publicText } from "../../state/chatMessages";
import AssistantMessageView from "./AssistantMessageView";
import { copyPlainText } from "./MessageActions";

interface Props {
  message: ChatMessage | null;
  open: boolean;
  copyHint: boolean;
  onClose: () => void;
  onCopyFailed: () => void;
}

export default function ExpandedReplyDialog({
  message,
  open,
  copyHint,
  onClose,
  onCopyFailed,
}: Props) {
  const text = message ? publicText(message) : "";
  return (
    <AppDialog open={open} onOpenChange={next => { if (!next) onClose(); }} title="阅读这条回复" className="reply-dialog">
          <div className="chat-dialog-tools">
              <button
                type="button"
                className="btn btn-gray px-2 py-0.5 text-[11px]"
                onClick={async () => {
                  const result = await copyPlainText(text);
                  if (result === "failed") onCopyFailed();
                }}
                disabled={!text.trim()}
              >
                复制原文
              </button>
          </div>
          {copyHint && (
            <p className="chat-dialog-hint">
              剪贴板不可用，下面是这条回复的原文：手动选择需要的内容复制。
            </p>
          )}
          <div className="chat-dialog-body">
            {message ? (
              <AssistantMessageView message={message} wide showActions={false} />
            ) : (
              <p className="text-xs text-sub">这条回复已经不在这段讨论里了。</p>
            )}
            {copyHint && text.trim() && (
              <pre className="chat-dialog-source" aria-label="回复原文">
                {text}
              </pre>
            )}
          </div>
    </AppDialog>
  );
}

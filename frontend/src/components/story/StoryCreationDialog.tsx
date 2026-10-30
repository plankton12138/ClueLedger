import { useRef, useState, type FormEvent } from "react";

import { useConversationsStore, runningConversation } from "../../state/conversations";
import { useModelsStore } from "../../state/models";
import { EMPTY_CONTEXT } from "../../api/workspace";
import { AppDialog } from "../ui/AppDialog";
import { Button } from "../ui/Button";
import { StatusMessage } from "../ui/StatusMessage";

export interface StoryCreationTarget {
  workspaceId: string;
  conversationId: string | null;
  variantId: string;
  revision: string;
  name: string;
}

export default function StoryCreationDialog({ target, onClose, onAccepted }: {
  target: StoryCreationTarget;
  onClose: () => void;
  onAccepted: () => void;
}) {
  const [text, setText] = useState(target.conversationId
    ? "根据当前对话中已确认的要求和已装入的资料，建立事件节点、线索与连接。未确定的真相保持未定。"
    : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitting = useRef(false);
  const input = useRef<HTMLTextAreaElement>(null);
  const available = useModelsStore(state => state.selection.available);
  const running = useConversationsStore(runningConversation);
  const pending = useConversationsStore(state => state.pendingKey);
  const ready = Boolean(text.trim()) && available && !running && !pending && !busy;

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready || submitting.current) return;
    const chat = useConversationsStore.getState();
    if (chat.workspaceId !== target.workspaceId || chat.selectedId !== target.conversationId) {
      setError("[STORY_TARGET_CHANGED] 构思创建：作品或对话已切换。建议：关闭后重新打开创建表单。");
      return;
    }
    submitting.current = true;
    setBusy(true);
    setError(null);
    let accepted = false;
    try {
      const result = await chat.send(`创建构思：\n${text.trim()}`, {
        ...EMPTY_CONTEXT,
        story: {
          variant_id: target.variantId,
          revision: target.revision,
          node_ids: [],
          purpose: "design",
        },
      }, {
        purpose: "chat",
        storyAction: "create",
        preserveDraft: true,
        onAccepted: () => { accepted = true; onAccepted(); },
      });
      if (!result && !accepted) {
        setError(useConversationsStore.getState().error
          ?? "[STORY_CREATE_FAILED] 构思创建：请求未被受理。建议：核对模型连接后重新提交。");
      }
    } finally {
      submitting.current = false;
      if (!accepted) setBusy(false);
    }
  }

  return <AppDialog open title="创建构思" onOpenChange={open => { if (!open) onClose(); }} initialFocus={() => input.current}>
    <form className="space-y-4" onSubmit={event => void submit(event)}>
      <p className="text-sm">结果保存到{target.name}，创建进度在对话中显示。</p>
      <label className="block space-y-2">
        <span>创作要求</span>
        <textarea ref={input} className="ui-input w-full min-h-40" rows={6}
          value={text} onChange={event => setText(event.target.value)} disabled={busy}
          placeholder="写下谜题、场景、结局，或说明要怎样整理当前对话与资料。" />
      </label>
      {!available && <StatusMessage tone="error" action={<Button onClick={() => useModelsStore.getState().openSettings()}>设置模型</Button>}>
        [MODEL_NOT_CONFIGURED] 构思创建：没有可用模型。建议：先连接并选择模型。
      </StatusMessage>}
      {running && <StatusMessage>当前作品还有任务进行中，结束或暂停后再创建。</StatusMessage>}
      {error && <StatusMessage tone="error">{error}</StatusMessage>}
      <div className="flex justify-end gap-2">
        <Button onClick={onClose}>取消</Button>
        <Button type="submit" variant="primary" disabled={!ready} busy={busy}>
          {busy ? "正在受理…" : "创建构思"}
        </Button>
      </div>
    </form>
  </AppDialog>;
}

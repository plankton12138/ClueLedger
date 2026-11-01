/**
 * 构思面：悬疑视图是唯一的结构展示方式。
 *
 * 时间轴按故事内实际时间排布节点，线索与知情作为泳道呈现；选中的节点只用于
 * 过滤左栏全书线索，不再展开第二份节点详情。没有节点的作品不显示假结构，
 * 只说明可以怎么开始。
 */

import { useEffect, useState } from "react";
import { Undo2 } from "lucide-react";

import StoryDraft from "./StoryDraft";
import StoryBatches from "./StoryBatches";
import StoryCreationDialog, { type StoryCreationTarget } from "./StoryCreationDialog";
import StoryMysteryView from "./StoryMysteryView";
import { useConversationsStore } from "../../state/conversations";
import { useStoryStore } from "../../state/story";
import { Button } from "../ui/Button";
import { EmptyState, StatusMessage } from "../ui/StatusMessage";

export default function StorySurface({
  workspaceId,
  onStart,
  onCalibrate,
}: {
  workspaceId: string;
  /** 创建请求受理后打开对话，显示实际任务进度。 */
  onStart: () => void;
  /** 根据已有正文校准当前构思；不会直接替换正式结构。 */
  onCalibrate: () => void;
}) {
  const state = useStoryStore();
  const load = useStoryStore((store) => store.load);
  const selectNode = useStoryStore((store) => store.selectNode);
  const selectClue = useStoryStore((store) => store.selectClue);
  const undo = useStoryStore((store) => store.undo);
  const [creationTarget, setCreationTarget] = useState<StoryCreationTarget | null>(null);

  useEffect(() => {
    setCreationTarget(null);
    void load(workspaceId);
  }, [load, workspaceId]);

  const graph = state.graph;

  if (state.loading && !graph) {
    return <div className="story-surface story-surface-empty">正在读取构思…</div>;
  }
  if (!graph || graph.nodes.length === 0) {
    return (
      <div className="story-surface story-surface-empty">
        <StoryDraft key={workspaceId} workspaceId={workspaceId} />
        {state.error ? (
          <StatusMessage tone="error" action={<Button onClick={() => void load(workspaceId)}>重新读取</Button>}>
            {state.error}
          </StatusMessage>
        ) : (
          <EmptyState action={<Button variant="primary" onClick={() => setCreationTarget({
            workspaceId,
            conversationId: useConversationsStore.getState().selectedId,
            variantId: state.variantId ?? "",
            revision: graph?.revision ?? "",
            name: "构思",
          })}>创建构思</Button>}>
            还没有构思。根据创作要求或已有对话，建立节点、线索与连接。
          </EmptyState>
        )}
        {creationTarget && <StoryCreationDialog target={creationTarget}
          onClose={() => setCreationTarget(null)}
          onAccepted={() => { setCreationTarget(null); onStart(); }} />}
      </div>
    );
  }

  return (
    <div className="story-surface">
      <header className="story-toolbar">
        <div className="story-actions">
          {state.lastChange && (
            <Button size="compact" disabled={state.busy} onClick={() => void undo()}>
              <Undo2 size={16} strokeWidth={2} aria-hidden="true" />撤销上一次
            </Button>
          )}
          <Button size="compact" variant="quiet" disabled={state.busy} onClick={onCalibrate}>
            从正文校准
          </Button>
        </div>
      </header>

      <StoryDraft key={`story-draft-${workspaceId}`} workspaceId={workspaceId} />
      <StoryBatches key={`story-batches-${workspaceId}`} workspaceId={workspaceId} />

      {state.error && (
        <p className="story-error" role="alert">
          {state.error}
          <button type="button" className="ui-button" onClick={() => state.dismissError()}>
            知道了
          </button>
        </p>
      )}

      <div className="story-body">
        <StoryMysteryView
          workspaceId={workspaceId}
          variantId={graph.variant_id}
          revision={graph.revision}
          graph={graph}
          selectedClueId={state.clueId}
          onSelectClue={selectClue}
          onSelectNode={selectNode}
        />
      </div>
    </div>
  );
}

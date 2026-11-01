/**
 * 构思面：只读故事时间轴 + 详情区的入口。
 *
 * 节点详情、正文出处与「讨论调整」统一放在右侧共享详情区；这里只负责时间轴本身，
 * 不再保留泳道、阶段筛选与多层信息。
 */

import { useEffect, useState } from "react";
import { Undo2 } from "lucide-react";

import StoryDraft from "./StoryDraft";
import StoryBatches from "./StoryBatches";
import StoryCreationDialog, { type StoryCreationTarget } from "./StoryCreationDialog";
import StoryTimeline from "./StoryTimeline";
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
          <StatusMessage
            tone="error"
            action={<Button onClick={() => void load(workspaceId)}>重新读取</Button>}
          >
            {state.error}
          </StatusMessage>
        ) : (
          <EmptyState
            action={
              <Button
                variant="primary"
                onClick={() =>
                  setCreationTarget({
                    workspaceId,
                    conversationId: useConversationsStore.getState().selectedId,
                    variantId: state.variantId ?? "",
                    revision: graph?.revision ?? "",
                    name: "构思",
                  })
                }
              >
                创建构思
              </Button>
            }
          >
            还没有构思。根据创作要求或已有对话，建立节点、线索与连接。
          </EmptyState>
        )}
        {creationTarget && (
          <StoryCreationDialog
            target={creationTarget}
            onClose={() => setCreationTarget(null)}
            onAccepted={() => {
              setCreationTarget(null);
              onStart();
            }}
          />
        )}
      </div>
    );
  }

  return (
    <div className="story-surface">
      <header className="story-toolbar">
        <div className="story-overview-title">
          <h1>故事时间轴</h1>
          <p>横轴是发生时间，纵向按披露章节分组；选中节点在右侧查看与修改。</p>
        </div>
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
        <StoryTimeline
          timeline={state.timeline}
          selectedNodeId={state.nodeId}
          onSelectNode={selectNode}
        />
      </div>
    </div>
  );
}

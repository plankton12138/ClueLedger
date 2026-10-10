/**
 * 空态建议与等待占位的纯函数（P9 T3，D5）：
 * - stageSuggestions：按 story.stage 映射空会话的建议指令（点击即发送）；
 * - shouldShowThinkingPlaceholder：running 且无任何可见输出时显示「正在思考…」。
 * 纯函数无副作用，stage 事实源是 GET /api/{slug}/story（App 已 refetch）。
 */

export type StageGroup = "intake" | "bible" | "writing";

export interface StageSuggestion {
  /** 按钮文案。 */
  label: string;
  /** 点击后实际发送的指令文本。 */
  prompt: string;
}

/** stage → 分组（未知阶段兜底 intake——空态给最通用的起步动作）。 */
export function stageGroup(stage: string): StageGroup {
  if (stage === "bible_drafting" || stage === "bible_confirmed") return "bible";
  if (stage === "writing" || stage === "revising") return "writing";
  return "intake";
}

const SUGGESTIONS: Record<StageGroup, StageSuggestion[]> = {
  intake: [
    { label: "我要投递一个故事构想", prompt: "我要投递一个故事构想" },
    { label: "先聊聊你能做什么", prompt: "先聊聊你能做什么" },
  ],
  bible: [
    { label: "按现有构想继续建账", prompt: "按现有构想继续建账" },
    { label: "我要修改设定", prompt: "我要修改设定" },
  ],
  writing: [
    { label: "写下一章", prompt: "写下一章" },
    { label: "检查一致性", prompt: "检查一致性" },
  ],
};

/** stage → 2 个建议按钮。 */
export function stageSuggestions(stage: string): StageSuggestion[] {
  return SUGGESTIONS[stageGroup(stage)];
}

export interface ThinkingPlaceholderInput {
  running: boolean;
  assistantBuffer: string;
  thinkingBuffer: string;
  /** 是否存在 status === "running" 的工具卡（工具执行中已有「执行中…」，互斥）。 */
  hasRunningTool: boolean;
}

/**
 * 等待首字占位条件：running 且无正文 buffer、无思考 buffer、无运行中工具卡。
 * 与 ToolCard 的「执行中…」条件互斥，防重复提示。
 */
export function shouldShowThinkingPlaceholder(input: ThinkingPlaceholderInput): boolean {
  return (
    input.running &&
    !input.assistantBuffer &&
    !input.thinkingBuffer &&
    !input.hasRunningTool
  );
}

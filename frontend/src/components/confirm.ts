/**
 * 作者确认交互卡解析（P10 T10，D9）：识别 assistant 文本尾部的【确认】标记块。
 * prompt 定协议、前端宽容解析——无标记或格式残缺时返回原文（confirm=null），
 * 调用方原样渲染全文，不静默吞内容。
 *
 * 协议格式（见 prompts.py WORKFLOW_SECTION）：
 *
 *   【确认】
 *   待确认：<一句话，确认什么>
 *   确认后：<会发生什么：推进到 X 阶段 / 落盘哪些条目>
 *   暂不：<不确认时你会做什么>
 *
 * 硬约束仍在 story_stage 工具层，本模块只是 UI 便利层（协议软、工具硬）。
 */

/** 确认块标记（与 src/hastings/prompts.py 的 CONFIRM_MARKER 同一文本锚点）。 */
export const CONFIRM_MARKER = "【确认】";

/** 「确认」按钮发送的定式指令。 */
export const CONFIRM_REPLY = "我确认，请继续推进";

/** 「暂不」按钮发送的定式指令。 */
export const DECLINE_REPLY = "暂不确认，请说明还需补充什么";

export interface ChatConfirm {
  /** 待确认：一句话，确认什么。 */
  pending: string;
  /** 确认后：会发生什么。 */
  after: string;
  /** 暂不：不确认时 agent 会做什么。 */
  decline: string;
}

export interface ParsedConfirm {
  /** 去掉确认块后的正文。 */
  body: string;
  /** 解析成功的确认块；null = 无标记/格式残缺，body 为原始全文。 */
  confirm: ChatConfirm | null;
  /**
   * 标记存在但解析失败：调用方原样渲染 body，并可在尾部提示「确认卡格式异常」
   * ——区分「本条本就没有确认块」与「有确认块但格式坏」，后者不静默吞。
   */
  malformed: boolean;
}

// 字段行：<标签>：<值>（容忍全角/半角冒号）。
const CONFIRM_FIELD_RE = /^(待确认|确认后|暂不)[：:]\s*(.*)$/;

/**
 * 解析消息尾部的确认块。
 *
 * 成功条件：标记块在消息尾部（取最后一个标记）、块内恰含待确认/确认后/暂不
 * 三个字段且值均非空、无多余行、无重复字段——任一不满足即返回原文（confirm=null）。
 * 标记存在但解析失败时 malformed=true，调用方据此在尾部提示格式异常。
 */
export function parseConfirm(text: string): ParsedConfirm {
  const markerIndex = text.lastIndexOf(CONFIRM_MARKER);
  if (markerIndex === -1) return { body: text, confirm: null, malformed: false };

  const fail = (): ParsedConfirm => ({ body: text, confirm: null, malformed: true });

  const blockText = text.slice(markerIndex + CONFIRM_MARKER.length);
  const fields = new Map<string, string>();
  for (const rawLine of blockText.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const match = CONFIRM_FIELD_RE.exec(line);
    if (match === null) return fail();
    const [, label, value] = match;
    if (fields.has(label)) return fail();
    fields.set(label, value.trim());
  }

  const pending = fields.get("待确认");
  const after = fields.get("确认后");
  const decline = fields.get("暂不");
  if (!pending || !after || !decline) return fail();

  return {
    body: text.slice(0, markerIndex).trimEnd(),
    confirm: { pending, after, decline },
    malformed: false,
  };
}
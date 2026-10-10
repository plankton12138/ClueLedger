/**
 * 候选方案选项块解析（P9 T4，D3）：识别 assistant 文本尾部的【选项】标记块。
 * prompt 定协议、前端宽容解析——无标记或格式残缺时返回原文（options=null），
 * 调用方原样渲染全文，不静默吞内容。
 *
 * 协议格式（见 prompts.py WORKFLOW_SECTION）：
 *
 *   【选项】
 *   A. 方案甲 —— 一句取舍说明
 *   B. 方案乙 —— 一句取舍说明
 */

/** 选项块标记（与 src/hastings/prompts.py 的 OPTIONS_MARKER 同一文本锚点）。 */
export const OPTIONS_MARKER = "【选项】";

export interface ChatOption {
  /** 选项键，如 "A"。 */
  key: string;
  /** 方案名（短文案）。 */
  label: string;
  /** 取舍说明（一句；缺失时为空串）。 */
  note: string;
}

export interface ParsedOptions {
  /** 去掉选项块后的正文。 */
  body: string;
  /** 解析成功的选项；null = 无标记/格式残缺，body 为原始全文。 */
  options: ChatOption[] | null;
}

// 选项行：A. / A、 / A． 后接内容（容忍键后标点的轻微漂移）。
const OPTION_LINE_RE = /^([A-Z])[.．、]\s*(.+?)\s*$/;

// 方案名 / 取舍说明分隔：协议为 ——，容忍单 em-dash、--、全角连写（—— 先匹配）。
const NOTE_SPLIT_RE = /\s+(?:——|—|--|－－)\s*/;

function parseOptionLine(line: string): ChatOption | null {
  const match = OPTION_LINE_RE.exec(line);
  if (match === null) return null;
  const [, key, rest] = match;
  const separator = NOTE_SPLIT_RE.exec(rest);
  if (separator === null) return { key, label: rest.trim(), note: "" };
  const label = rest.slice(0, separator.index).trim();
  const note = rest.slice((separator.index ?? 0) + separator[0].length).trim();
  return { key, label, note };
}

/**
 * 解析消息尾部的选项块。
 *
 * 成功条件：标记块在消息尾部（取最后一个标记）、块内 ≥2 个合法选项行、
 * 选项键自 A 起连续升序（A、B、C…）——任一不满足即返回原文。
 */
export function parseOptions(text: string): ParsedOptions {
  const markerIndex = text.lastIndexOf(OPTIONS_MARKER);
  if (markerIndex === -1) return { body: text, options: null };

  const blockText = text.slice(markerIndex + OPTIONS_MARKER.length);
  const options: ChatOption[] = [];
  for (const rawLine of blockText.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const option = parseOptionLine(line);
    if (option === null) return { body: text, options: null };
    options.push(option);
  }

  if (options.length < 2) return { body: text, options: null };
  for (let i = 0; i < options.length; i++) {
    if (options[i].key !== String.fromCharCode(65 + i)) {
      return { body: text, options: null };
    }
    if (options[i].label.length === 0) return { body: text, options: null };
  }

  return { body: text.slice(0, markerIndex).trimEnd(), options };
}

/** 点击选项后发送的指令文本：我选 B：<方案名>（带方案名防模型丢上下文）。 */
export function optionReply(option: ChatOption): string {
  return `我选 ${option.key}：${option.label}`;
}

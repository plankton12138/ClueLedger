/**
 * 候选方案选项块解析（P9 T4，D3；P10 T11，D10 多维度升级）：识别 assistant 文本
 * 尾部的【选项】标记块。prompt 定协议、前端宽容解析——无标记或格式残缺时返回原文
 * （groups=null），调用方原样渲染全文，不静默吞内容。
 *
 * 协议格式（见 prompts.py WORKFLOW_SECTION）：
 *
 *   【选项】                      // 无维度名单块（向后兼容）
 *   A. 方案甲 —— 一句取舍说明
 *   B. 方案乙 —— 一句取舍说明
 *
 *   【选项：章纲划分】            // 带维度名块（D10）：一条消息可含多块
 *   A. 三幕式 —— 一句取舍说明
 *   B. 双线并行 —— 一句取舍说明
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

/** 一组选项（同一决策维度）：dimension=null 表示无维度名单块。 */
export interface ParsedOptionsGroup {
  dimension: string | null;
  options: ChatOption[];
}

export interface ParsedOptions {
  /** 去掉选项块后的正文。 */
  body: string;
  /** 解析成功的选项分组（可多组）；null = 无标记/格式残缺，body 为原始全文。 */
  groups: ParsedOptionsGroup[] | null;
  /**
   * 标记存在但解析失败（P10 T6，D5）：调用方原样渲染 body，并可在尾部提示
   * 「格式异常」——区分「本条本就没有选项」与「有选项但格式坏」，后者要给
   * 作者/开发者一个信号（不静默吞）。任一块坏了整条即 malformed（不给半成按钮误导）。
   */
  malformed: boolean;
}

// 选项行：A. / A、 / A． 后接内容（容忍键后标点的轻微漂移）。
const OPTION_LINE_RE = /^([A-Z])[.．、]\s*(.+?)\s*$/;

// 方案名 / 取舍说明分隔：协议为 ——，容忍单 em-dash、--、全角连写（—— 先匹配）。
const NOTE_SPLIT_RE = /\s+(?:——|—|--|－－)\s*/;

// 选项块标记：可选维度名（【选项：<维度>】，容忍全角/半角冒号）。
const OPTIONS_BLOCK_RE = /【选项(?:[：:]\s*([^】]*?))?】/g;

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

/** 解析单个块的选项行：≥2 个合法选项、键自 A 起连续升序，否则 null。 */
function parseBlockOptions(blockText: string): ChatOption[] | null {
  const options: ChatOption[] = [];
  for (const rawLine of blockText.split("\n")) {
    const line = rawLine.trim();
    if (!line) continue;
    const option = parseOptionLine(line);
    if (option === null) return null;
    options.push(option);
  }
  if (options.length < 2) return null;
  for (let i = 0; i < options.length; i++) {
    if (options[i].key !== String.fromCharCode(65 + i)) return null;
    if (options[i].label.length === 0) return null;
  }
  return options;
}

/**
 * 解析消息尾部的选项块（可多块）。
 *
 * 成功条件：标记块在消息尾部、块内每块 ≥2 个合法选项行、各块键各自自 A 起
 * 连续升序，且无块外多余文本——任一不满足即整条返回原文（groups=null）。
 * 标记存在但解析失败时 malformed=true，调用方据此在尾部提示格式异常。
 */
export function parseOptions(text: string): ParsedOptions {
  const matches = [...text.matchAll(OPTIONS_BLOCK_RE)];
  if (matches.length === 0) return { body: text, groups: null, malformed: false };

  const fail = (): ParsedOptions => ({ body: text, groups: null, malformed: true });

  const first = matches[0].index ?? 0;
  const groups: ParsedOptionsGroup[] = [];
  for (let i = 0; i < matches.length; i++) {
    const match = matches[i];
    const dimension = match[1] === undefined ? null : match[1].trim();
    if (dimension !== null && dimension.length === 0) return fail(); // 【选项：】空维度名
    const blockStart = (match.index ?? 0) + match[0].length;
    const blockEnd = i + 1 < matches.length ? (matches[i + 1].index ?? text.length) : text.length;
    const options = parseBlockOptions(text.slice(blockStart, blockEnd));
    if (options === null) return fail();
    groups.push({ dimension, options });
  }

  return { body: text.slice(0, first).trimEnd(), groups, malformed: false };
}

/** 单组即时发送的指令文本：我选 B：<方案名>（带方案名防模型丢上下文）。 */
export function optionReply(option: ChatOption): string {
  return `我选 ${option.key}：${option.label}`;
}

/** 多组一次性提交的合并指令：关于「<维度>」我选 X：<方案名>；关于「…」…。 */
export function multiOptionReply(
  groups: ParsedOptionsGroup[],
  selected: (ChatOption | null)[],
): string {
  return groups
    .map((group, i) => {
      const option = selected[i];
      const key = option?.key ?? "";
      const label = option?.label ?? "";
      return `关于「${group.dimension ?? "此项"}」我选 ${key}：${label}`;
    })
    .join("；");
}
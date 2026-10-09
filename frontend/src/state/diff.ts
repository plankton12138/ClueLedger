/**
 * diff 呈现纯函数（P5 T5，计划 D3/D7）：段内字符级高亮。
 *
 * 服务端只给整段新旧文本（hunk 携带 oldText/newText），定位「变更中段」由
 * 前端前后缀修剪完成——CJK 场景比服务端逐字符 diff 更稳（D3）。纯函数：
 * 「两段文本进、四段切片出」，无 IO 无环境依赖，行为全部可由字符串断言。
 */

export interface InlineDiff {
  /** 新旧公共前缀（未变更，原样渲染）。 */
  prefix: string;
  /** 旧文本的变更中段（删除，红删除线）。 */
  oldMiddle: string;
  /** 新文本的变更中段（新增，绿底）。 */
  newMiddle: string;
  /** 新旧公共后缀（未变更，原样渲染）。 */
  suffix: string;
}

/**
 * 段落内字符级差异：公共前缀 + 公共后缀之间的中段即变更。
 * 中段可能为空串（仅前后缀差异，或两文本完全相同）。
 */
export function inlineDiff(oldText: string, newText: string): InlineDiff {
  let prefix = 0;
  const maxPrefix = Math.min(oldText.length, newText.length);
  while (prefix < maxPrefix && oldText[prefix] === newText[prefix]) prefix += 1;

  let suffix = 0;
  // 后缀不与前缀重叠：两侧都以去掉公共前缀后的长度为上界
  const maxSuffix = Math.min(oldText.length - prefix, newText.length - prefix);
  while (
    suffix < maxSuffix &&
    oldText[oldText.length - 1 - suffix] === newText[newText.length - 1 - suffix]
  ) {
    suffix += 1;
  }

  return {
    prefix: oldText.slice(0, prefix),
    oldMiddle: oldText.slice(prefix, oldText.length - suffix),
    newMiddle: newText.slice(prefix, newText.length - suffix),
    suffix: oldText.slice(oldText.length - suffix),
  };
}
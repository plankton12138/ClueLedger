/**
 * 作者文字是否明确要求写正文。
 *
 * 页面只提供参考位置，不能单独决定这一轮是 writing 还是 design。
 * 服务端仍会做最终判断；这里先避免把明确的“重写前两章”发送成 design。
 */

const PROSE_TARGET =
  /(正文|章节|第[一二三四五六七八九十百两\d]+章|前[一二三四五六七八九十两\d]+章|下一章|这一章|这两章|开头|结尾|场景|这一段|这两段)/;
const PROSE_VERBS = /(写|重写|改写|续写|起草|落笔|补写|改稿|润色)/;
const PROSE_FILE_TARGETS = [
  "这个文件",
  "这两个文件",
  "这份文件",
  "这两份文件",
  "这些文件",
  "该文件",
];
const STRUCTURE_PHRASE =
  /(?:的\s*(构思|结构|设定|大纲|人物小传)|(?:写进|写入|整理进|归入|放进)\s*(构思|结构|设定|大纲))/;
const WRITE_STRUCTURE =
  /(写|重写|改写|修改|调整|整理|建立|创建|补写|补)\s*(?:一个|一份|一段|一下)?\s*(构思|结构|设定|大纲|人物小传)/;

export function looksLikeProseRequest(
  text: string,
  selectedPath: string | null = null,
): boolean {
  const clean = text.trim();
  if (!clean || !PROSE_VERBS.test(clean)) return false;
  const target =
    PROSE_TARGET.test(clean) ||
    (Boolean(selectedPath?.startsWith("正文/")) &&
      PROSE_FILE_TARGETS.some((phrase) => clean.includes(phrase)));
  if (!target) return false;
  return !STRUCTURE_PHRASE.test(clean) && !WRITE_STRUCTURE.test(clean);
}

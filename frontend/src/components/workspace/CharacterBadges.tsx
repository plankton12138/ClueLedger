/**
 * 人物身份标签（0.5 V05-3A）。
 *
 * 标签只来自服务端解析的人物元信息：界面不根据对话或猜测自行加标签。
 * 文字与颜色同时表达身份，屏幕阅读器能读到完整列表；没有标签时不占位。
 */

import type { CharacterRole } from "../../api/workspace";

export const ROLE_LABEL: Record<CharacterRole, string> = {
  detective: "侦探",
  assistant: "助手",
  culprit: "凶手",
  suspect: "嫌疑人",
  victim: "受害者",
};

/**
 * 身份标签的完整类名必须写成字面量：构建时按源码扫描用到的类，
 * `role-${role}` 这类拼接会被当成未使用样式删掉，只剩一个没有颜色的空壳。
 */
export const ROLE_CLASS: Record<CharacterRole, string> = {
  detective: "role-badge role-detective",
  assistant: "role-badge role-assistant",
  culprit: "role-badge role-culprit",
  suspect: "role-badge role-suspect",
  victim: "role-badge role-victim",
};

interface Props {
  roles: CharacterRole[];
  /** 目录里用更小的尺寸，稿纸顶部用常规尺寸。 */
  compact?: boolean;
}

export default function CharacterBadges({ roles, compact = false }: Props) {
  if (roles.length === 0) return null;
  const text = roles.map((role) => ROLE_LABEL[role] ?? role).join("、");
  return (
    <span
      className={compact ? "role-badges role-badges-compact" : "role-badges"}
      aria-label={`人物身份：${text}`}
    >
      {roles.map((role) => (
        <span key={role} className={ROLE_CLASS[role] ?? "role-badge"}>
          {ROLE_LABEL[role] ?? role}
        </span>
      ))}
    </span>
  );
}

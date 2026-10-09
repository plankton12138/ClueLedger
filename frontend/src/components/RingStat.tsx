/**
 * 共享进度环（P7 T2，计划 D3）：从 BiblePanel 的 RecoveryRing 提取参数化——
 * size / rate / label（无障碍）/ color（弧色 Tailwind 类），账本回收率环、
 * 审计三指标环、语料对话占比环三处复用。纯 SVG 零依赖；
 * 几何参数按 size 等比缩放（size=36 时与原实现逐值一致，视觉零回退）。
 */

/** 弧长填充比的钳制：越界 rate（NaN 视为 0）收敛到 [0,1]。 */
export function clampRate(rate: number): number {
  if (Number.isNaN(rate)) return 0;
  return Math.min(1, Math.max(0, rate));
}

/** 环上百分比文案：钳制后四舍五入（0.995 → 100%，不留越界数字）。 */
export function ringPct(rate: number): number {
  return Math.round(clampRate(rate) * 100);
}

/**
 * stroke-dasharray 值（「填充周长 全周长」）：原实现字号级的toFixed(1)
 * 原样保留，保证 size=36 时渲染结果与 RecoveryRing 逐值一致。
 */
export function ringDash(rate: number, radius: number): string {
  const circumference = 2 * Math.PI * radius;
  return `${(circumference * clampRate(rate)).toFixed(1)} ${circumference.toFixed(1)}`;
}

interface RingStatProps {
  /** 填充比 0..1（越界钳制）。 */
  rate: number;
  /** 像素边长（环为正方形）；默认 36 = 原账本统计卡尺寸。 */
  size?: number;
  /** 无障碍标签前缀（aria-label = 「{label} N%」）；默认「进度」。 */
  label?: string;
  /** 填充弧的 Tailwind 色类（currentColor 机制）；默认账本回收绿。 */
  color?: string;
}

export default function RingStat({ rate, size = 36, label = "进度", color = "text-recovered" }: RingStatProps) {
  const pct = ringPct(rate);
  const r = (size * 14) / 36;
  const strokeWidth = (size * 3) / 36;
  const fontSize = (size * 8) / 36;
  const c = size / 2;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-label={`${label} ${pct}%`} role="img">
      <circle cx={c} cy={c} r={r} fill="none" stroke="currentColor" className="text-line" strokeWidth={strokeWidth} />
      <circle
        cx={c}
        cy={c}
        r={r}
        fill="none"
        stroke="currentColor"
        className={color}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={ringDash(rate, r)}
        transform={`rotate(-90 ${c} ${c})`}
      />
      <text x={c} y={c + strokeWidth} textAnchor="middle" fontSize={fontSize} className="fill-paper">
        {pct}%
      </text>
    </svg>
  );
}

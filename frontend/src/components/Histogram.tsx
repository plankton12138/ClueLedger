/**
 * 共享柱状直方图（P7 T2，计划 D3）：纯 SVG 零依赖，语料句长分布专用
 * （桶序列 → 柱高布局 → 渲染），hover 经 <title> 原生提示显数。
 * 布局是纯函数可测；渲染层不做任何数值决策。
 */

export interface HistogramBucket {
  label: string;
  count: number;
}

export interface HistogramBar {
  /** 桶标签（也是 React key / 提示文案的一部分）。 */
  label: string;
  /** 原始计数（0 也渲染柱位，只是高度为零——分布的「空桶」本身是信息）。 */
  count: number;
  /** 柱中心 x（标签锚点）。 */
  cx: number;
  /** 柱形矩形。 */
  x: number;
  y: number;
  w: number;
  h: number;
}

/** 底部预留的标签带高度（与 width 无关的固定值，标签字号 9px 一行）。 */
export const HISTOGRAM_LABEL_BAND = 16;

/**
 * 桶序列 → 柱形几何。最高柱顶到绘图区顶部留 4px 呼吸；全零/空序列时
 * 柱高全为 0（合法空结果，由渲染层显示占位文案，此处不报错）。
 * 负计数按 0 处理（上游统计口径错误不渲染成倒挂柱）。
 */
export function layoutHistogram(
  buckets: HistogramBucket[],
  width: number,
  height: number,
): HistogramBar[] {
  const n = buckets.length;
  if (n === 0) return [];
  const slot = width / n;
  const barW = slot * 0.6;
  const plotH = Math.max(0, height - HISTOGRAM_LABEL_BAND - 4);
  const max = Math.max(0, ...buckets.map((b) => (Number.isFinite(b.count) ? b.count : 0)));
  return buckets.map((b, i) => {
    const count = Number.isFinite(b.count) ? Math.max(0, b.count) : 0;
    const h = max > 0 ? (count / max) * plotH : 0;
    const x = i * slot + (slot - barW) / 2;
    return {
      label: b.label,
      count,
      cx: i * slot + slot / 2,
      x,
      y: height - HISTOGRAM_LABEL_BAND - h,
      w: barW,
      h,
    };
  });
}

interface HistogramProps {
  buckets: HistogramBucket[];
  /** 画布像素尺寸；默认适配语料右栏（约 280 宽）。 */
  width?: number;
  height?: number;
  /** 无障碍标签（role=img 的 aria-label）。 */
  ariaLabel: string;
}

export default function Histogram({ buckets, width = 280, height = 120, ariaLabel }: HistogramProps) {
  const bars = layoutHistogram(buckets, width, height);
  if (bars.length === 0) {
    return (
      <svg width={width} height={height} role="img" aria-label={ariaLabel}>
        <text x={width / 2} y={height / 2} textAnchor="middle" fontSize="11" className="fill-paper" opacity="0.4">
          暂无数据
        </text>
      </svg>
    );
  }
  return (
    <svg width={width} height={height} role="img" aria-label={ariaLabel}>
      {bars.map((b) => (
        <g key={b.label}>
          {/* 命中区比柱体宽（整个 slot），细柱也容易 hover；空桶同样可 hover 查 0。 */}
          <rect x={b.cx - (width / bars.length) / 2} y={0} width={width / bars.length} height={height} fill="transparent">
            <title>{`${b.label}：${b.count}`}</title>
          </rect>
          {b.h > 0 && <rect x={b.x} y={b.y} width={b.w} height={b.h} className="fill-planned" rx="1" />}
          <text x={b.cx} y={height - 4} textAnchor="middle" fontSize="9" className="fill-paper" opacity="0.45">
            {b.label}
          </text>
        </g>
      ))}
    </svg>
  );
}

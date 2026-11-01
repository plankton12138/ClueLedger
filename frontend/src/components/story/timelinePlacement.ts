/** 时间轴位置换算：只把服务端给的相对分钟映射成百分比，不制造时间。 */

export type Placement =
  | { kind: "unplaced" }
  | { kind: "point"; left: number }
  | { kind: "range"; left: number; width: number };

export interface Span {
  start_minute: number;
  end_minute: number;
}

export interface Domain {
  min: number;
  max: number;
}

export function timePlacement(span: Span | null, domain: Domain | null): Placement {
  if (!span || !domain) return { kind: "unplaced" };
  const start = span.start_minute;
  const end = span.end_minute;
  if (
    ![start, end, domain.min, domain.max].every(Number.isFinite) ||
    domain.max <= domain.min ||
    end < start ||
    start < domain.min ||
    end > domain.max
  ) {
    throw new Error("时间投影与坐标范围不一致");
  }
  const unit = 100 / (domain.max - domain.min);
  const left = (start - domain.min) * unit;
  return start === end
    ? { kind: "point", left }
    : { kind: "range", left, width: (end - start) * unit };
}

function offsetOf(minute: number): string {
  if (minute === 0) return "基准时刻";
  return minute < 0 ? `基准前 ${Math.abs(minute)} 分钟` : `基准后 ${minute} 分钟`;
}

/** 作者读得懂的时间说明；不把相对分钟伪装成墙钟时间。 */
export function timeLabel(
  start: number | null,
  end: number | null,
  approximate: boolean,
  origin: string,
): string {
  if (start === null || end === null) return "时间未定";
  const mark = approximate ? "约 " : "";
  const base = origin.trim();
  const suffix = base ? `（${base}）` : "";
  if (start === end) return `${mark}${offsetOf(start)}${suffix}`;
  return `${mark}${offsetOf(start)} 至 ${offsetOf(end)}${suffix}`;
}

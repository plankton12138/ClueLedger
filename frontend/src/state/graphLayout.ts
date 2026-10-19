/**
 * 图谱布局偏好与引用文案（V03-6.4）——纯函数，不接触任何领域写入。
 *
 * 设计依据（产品设计 §5.4/§5.8、计划 M12）：
 * - 拖动节点只调整布局；正式关系与手法的变更必须走账本已有的编辑与确认路径。
 *   因此本模块唯一持久化位置是浏览器 localStorage 的布局偏好——图谱不是第二份
 *   可写事实（I08/I09），长在服务端的账本对象一个字节都不动。
 * - 初始布局确定性（不引入随机或力导向收敛差异），且**位置不表示故事质量或
 *   嫌疑程度**：按 ID 排序均匀落在一个圆上，避免「谁在中间谁重要」的暗示。
 * - 「加入讨论」把选中对象作为可见引用附到主输入框；引用文案由纯函数构建，
 *   本模块不发起请求。
 */

export type GraphViewId = "characters" | "clues";

export interface GraphPoint {
  x: number;
  y: number;
}

/** 节点 ID → 坐标。只存布局偏好，不含任何领域数据。 */
export type GraphLayout = Record<string, GraphPoint>;

const KEY_PREFIX = "hastings.graph.layout";

/** 布局偏好键：按 project + view 隔离，换作品/换视图互不污染。 */
export function graphLayoutKey(projectId: string, viewId: GraphViewId): string {
  return `${KEY_PREFIX}.${projectId}.${viewId}`;
}

const SELECTION_PREFIX = "hastings.graph.selection";

/** 选中对象键：返回正文/讨论再打开图谱时恢复上次查看的节点（产品 §5.4）。 */
export function graphSelectionKey(projectId: string, viewId: GraphViewId): string {
  return `${SELECTION_PREFIX}.${projectId}.${viewId}`;
}

/** 读取上次查看的对象 ID；无记录或存储不可用返回 null。 */
export function readSelection(projectId: string, viewId: GraphViewId): string | null {
  try {
    return localStorage.getItem(graphSelectionKey(projectId, viewId));
  } catch {
    return null;
  }
}

/** 记录/清除上次查看的对象 ID；只是查看位置，不是领域数据。 */
export function writeSelection(
  projectId: string,
  viewId: GraphViewId,
  nodeId: string | null,
): void {
  try {
    const key = graphSelectionKey(projectId, viewId);
    if (nodeId === null) localStorage.removeItem(key);
    else localStorage.setItem(key, nodeId);
  } catch {
    /* 禁用存储时仅本次会话生效。 */
  }
}

/** 读取布局偏好；无记录或存储不可用都回退空布局，不抛错。 */
export function readLayout(projectId: string, viewId: GraphViewId): GraphLayout {
  try {
    const raw = localStorage.getItem(graphLayoutKey(projectId, viewId));
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (parsed === null || typeof parsed !== "object") return {};
    const result: GraphLayout = {};
    for (const [id, point] of Object.entries(parsed as Record<string, unknown>)) {
      if (
        point !== null &&
        typeof point === "object" &&
        typeof (point as GraphPoint).x === "number" &&
        typeof (point as GraphPoint).y === "number"
      ) {
        result[id] = { x: (point as GraphPoint).x, y: (point as GraphPoint).y };
      }
    }
    return result;
  } catch {
    return {};
  }
}

/** 写入布局偏好；存储不可用时只本次会话生效，不抛错。 */
export function writeLayout(
  projectId: string,
  viewId: GraphViewId,
  layout: GraphLayout,
): void {
  try {
    localStorage.setItem(graphLayoutKey(projectId, viewId), JSON.stringify(layout));
  } catch {
    /* 禁用存储时仅本次会话生效；布局丢失不影响领域数据。 */
  }
}

/**
 * 初始布局：节点按 ID 排序均匀落在以原点为心的圆上，确定性且不表达重要性。
 * 单节点落在原点；空集合返回空布局。
 */
export function initialLayout(nodeIds: readonly string[]): GraphLayout {
  const ids = [...new Set(nodeIds)].sort();
  if (ids.length === 0) return {};
  if (ids.length === 1) return { [ids[0]]: { x: 0, y: 0 } };
  // 半径随节点数增长（每节点约 36px 弧长）：真实作品几十个对象时，固定小圆周会把
  // 节点挤成一团；上限只是防极端数据把初始布局撑爆，仍不表达任何重要性。
  const radius = Math.max(120, Math.min(2400, ids.length * 36));
  const result: GraphLayout = {};
  ids.forEach((id, index) => {
    const angle = (2 * Math.PI * index) / ids.length;
    result[id] = {
      x: Math.round(radius * Math.cos(angle)),
      y: Math.round(radius * Math.sin(angle)),
    };
  });
  return result;
}

/** 合并已保存布局与初始布局：拖过的坐标优先，新节点补初始位置，已消失节点丢弃。 */
export function resolveLayout(
  nodeIds: readonly string[],
  saved: GraphLayout,
): GraphLayout {
  const base = initialLayout(nodeIds);
  const result: GraphLayout = {};
  for (const id of nodeIds) {
    result[id] = saved[id] ?? base[id];
  }
  return result;
}

/** 拖动一个节点：返回新布局，原布局不变（纯函数；持久化由调用方写 localStorage）。 */
export function dragNode(
  layout: GraphLayout,
  nodeId: string,
  point: GraphPoint,
): GraphLayout {
  return { ...layout, [nodeId]: { x: point.x, y: point.y } };
}

/**
 * 画布变更的最小形状：只声明本函数关心的字段，其余（`selected`/`dimensions` 等）
 * 由索引签名原样接住，因此可以直接接受 React Flow 的 `NodeChange` 联合类型。
 */
export interface PositionChangeLike {
  id?: string;
  type: string;
  position?: GraphPoint;
  /** 拖动过程中的中间帧为 true；落定帧与键盘移动为 false。 */
  dragging?: boolean;
  [key: string]: unknown;
}

/**
 * 从一批画布变更里取出**落定**位置：只收 `dragging !== true` 的位置变更。
 * 拖动过程中不落盘，拖动结束与键盘移动（方向键每次移动一帧，同样标为落定）都经这里
 * 写入布局偏好——键盘与鼠标因此是同一份偏好，不会出现「键盘挪了、刷新又弹回去」。
 * 没有落定位置时返回 null，调用方据此跳过写入。
 */
export function settledPositions(
  changes: readonly PositionChangeLike[],
): GraphLayout | null {
  const result: GraphLayout = {};
  let found = false;
  for (const change of changes) {
    if (change.type !== "position" || !change.position || !change.id) continue;
    if (change.dragging === true) continue;
    result[change.id] = { x: change.position.x, y: change.position.y };
    found = true;
  }
  return found ? result : null;
}

export interface GraphReferenceInput {
  /** 种类中文标签，如「人物」「线索」「关系」。 */
  kindLabel: string;
  /** 节点标题或边的标签。 */
  title: string;
  /** 节点/边 ID（引用可追回的具体对象）。 */
  id: string;
  /** 账本版本；缺省省略版本段。 */
  revision?: string | null;
  /** 摘要或关系含义，作为引用说明。 */
  detail?: string;
}

/** 构建「加入讨论」的可见引用文案：种类、标题、版本与说明。 */
export function buildDiscussionReference(input: GraphReferenceInput): string {
  const revision = input.revision
    ? `（版本 ${input.revision.slice(0, 8)}）`
    : "";
  const detail = input.detail ? `：${input.detail}` : "";
  return `参考${input.kindLabel}「${input.title}」${revision}${detail}`;
}

/** 把引用附到草稿：空草稿直接落地，非空另起一行，避免粘在同一句里。 */
export function appendReference(draft: string, reference: string): string {
  const trimmed = draft.trimEnd();
  return trimmed === "" ? reference : `${trimmed}\n${reference}`;
}

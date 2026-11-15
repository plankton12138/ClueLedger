# 0.8 前端修复：模块代码示例

日期：2026-10-24；2026-10-25 按布局参考与接口核对修订。状态：**设计参考；H1–H6 已有实际实现与独立验证记录**。所属版本保持0.8，包版本保持 `0.8.0`。
任务、交互与验收以[修复计划](frontend-experience-plan.md)为准；布局原则参考 [dark 原型](../../prototypes/dark/README.md)，
不采用其深色外观或演示功能。

这些示例说明职责与失败处理，不是已提交的实现。当前实现按领域职责接入了这些规则；具体代码以阶段记录和源码为准；
完整应用代码还需要接入现有错误类型、状态更新和资源清理。不要仅复制正常分支，遗漏相邻文字规定的边界。
不要求为示例添加新的依赖；优先复用已安装的组件、状态与测试能力。

2026-10-25：H1 已采用完整读取、草稿归属、命令快照和应用恢复规则，实际实现与验证见[H1记录](notes/H1-frontend-reliability.md)。
本文件继续保留建议模式；示例命名和代码不作为当前实现清单，H2–H6 的实际组件、接口差异与证据见[H6记录](notes/H6-frontend-verification.md)。

| 模块 | 示例 | 主要解决的问题 |
|---|---|---|
| UX-01 | [E1](#e1) | 语义颜色、按钮与选中行 |
| UX-02 | [E2](#e2) | 模块化布局、空间预算、导航与路由分层 |
| UX-03 | [E3](#e3) | 请求乱序、草稿与提交归属 |
| UX-04 | [E4](#e4) | 完整读取、字段保留、部分成功恢复 |
| UX-05 | [E5](#e5) | 中文输入与发送、参考类型 |
| UX-06 | [E6](#e6) | 灵感历史预览与明确恢复 |
| UX-07 | [E7](#e7) | 资料加载、空结果、失败与范围 |
| UX-08 | [E8](#e8) | 弹窗焦点、错误信封 |
| UX-09 | [E9](#e9) | 真实行为与风险回归 |

<a id="e1"></a>

## E1. 语义颜色、按钮和选中行

建议位置：`src/theme.ts`、主题映射、`src/components/ui/Button.tsx`、`src/index.css`。
业务页面选择操作语义，基础组件负责外观与状态。以下颜色为初始候选，落地仍须测量组合和人工核对。

```typescript
// theme.ts 内的具名纯色；主题映射将其暴露为 colors.ui。
export const uiColors = {
  page: "#EEF1F3",
  surface: "#FFFFFF",
  paper: "#FFFCF5",
  text: "#253342",
  mutedText: "#52616B",
  controlEdge: "#7A8792",
  accent: "#245D85",
  accentHover: "#194868",
  onAccent: "#FFFFFF",
  selected: "#E8F0F8",
  onSelected: "#183F63",
  focus: "#0C5792",
  danger: "#B42318",
} as const;
```

```tsx
import { forwardRef, type ButtonHTMLAttributes } from "react";
import clsx from "clsx";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "quiet" | "danger";
  size?: "normal" | "compact";
  pending?: boolean;
  pendingLabel?: string;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button(
    {
      variant = "secondary", size = "normal", pending = false,
      pendingLabel = "处理中…", disabled, className, children,
      type = "button", ...props
    },
    ref,
  ) {
    return (
      <button
        {...props}
        ref={ref}
        type={type}
        disabled={disabled || pending}
        aria-busy={pending || undefined}
        className={clsx("ui-button", className)}
        data-variant={variant}
        data-size={size}
      >
        {pending ? pendingLabel : children}
      </button>
    );
  },
);
```

```css
/* 沿用现有字号处理：这里不再乘 --font-scale。 */
.ui-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 0.375rem;
  min-height: 36px;
  padding: 0.375rem 0.75rem;
  border: 1px solid theme("colors.ui.controlEdge");
  border-radius: 6px;
  background: theme("colors.ui.surface");
  color: theme("colors.ui.text");
  font-size: 14px;
  line-height: 1.4;
}
.ui-button[data-size="compact"] { min-height: 32px; }
.ui-button[data-variant="primary"] {
  background: theme("colors.ui.accent");
  border-color: theme("colors.ui.accent");
  color: theme("colors.ui.onAccent");
}
.ui-button[data-variant="primary"]:hover:not(:disabled) {
  background: theme("colors.ui.accentHover");
}
.ui-button[data-variant="quiet"] { border-color: transparent; }
.ui-button[data-variant="danger"] { color: theme("colors.ui.danger"); }
.ui-button:focus-visible, .ui-list-item:focus-visible {
  outline: 2px solid theme("colors.ui.focus");
  outline-offset: 2px;
}
.ui-list-item { color: theme("colors.ui.text"); }
.ui-list-item:hover, .ui-list-item[aria-current="true"] {
  color: theme("colors.ui.onSelected");
  background: theme("colors.ui.selected");
}
.ui-list-item__summary { color: inherit; }
```

这是状态规则的最小片段，最终还须给 secondary／quiet／danger 补齐悬停、按下、禁用与处理中样式。
禁用采用独立前景／背景色，不对整个控件统一降低透明度；原因由相邻状态或描述提供。
页面导航用 `aria-current="page"`，可展开控件用 `aria-expanded`，不要给所有按钮统一添加选中语义。
需要页签键盘规则时复用现有页签组件，不用一组外观相同的动作按钮替代。

<a id="e2"></a>

## E2. 根据可用空间选择布局

建议位置：`state/layout.ts` 的纯计算函数，真实容器宽度由布局组件观察。
字体变大后所需空间也增加；实际宽度不够时切换组合，不修改用户已保存的桌面宽度。

```typescript
type LayoutPlan =
  | { kind: "three"; sidebar: number; chat: number }
  | { kind: "two"; chat: number }
  | { kind: "reading"; sidebar: number }
  | { kind: "single" };

export function writingLayout(input: {
  width: number;
  fontScale: number;
  preferredSidebar: number;
  preferredChat: number;
  chatVisible: boolean;
}): LayoutPlan {
  const scale = Math.max(1, input.fontScale / 100);
  const clamp = (n: number, min: number, max: number) =>
    Math.min(max, Math.max(min, n));
  const sidebar = clamp(input.preferredSidebar, 180 * scale, 400 * scale);
  const chat = clamp(input.preferredChat, 280 * scale, 560 * scale);
  const widePaper = 560 * scale;
  const compactPaper = 420 * scale;
  const handle = 6;

  if (!input.chatVisible) {
    return input.width >= sidebar + widePaper + handle
      ? { kind: "reading", sidebar }
      : { kind: "single" };
  }
  if (input.width >= sidebar + chat + widePaper + handle * 2) {
    return { kind: "three", sidebar, chat };
  }
  if (input.width >= chat + compactPaper + handle) {
    return { kind: "two", chat };
  }
  return { kind: "single" };
}
```

`single` 由页面级状态选择正文或对话，目录作为可关闭抽屉。切换面板只改变显示，
对话草稿、SSE订阅和任务状态继续归现有会话状态。实际实现可进一步钳制有效侧栏宽度以减少不必要切换。

dark 原型只约束模块如何占据空间，不提供配色。主模块先分配空间，辅助模块在有剩余宽度时出现；
各模块自己承担滚动，页面整体不滚动。以下仅表达布局关系，表面、边界与焦点仍使用主题令牌：

```css
/* --panel / --panel-edge 是 UX-01 拟建立的语义变量，最终名称以主题映射为准。 */
.app-page {
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
  padding: 12px;
}
.app-page--with-aside {
  grid-template-rows: minmax(0, 1fr);
}
@media (min-width: 1100px) {
  .app-page--with-aside {
    grid-template-columns: minmax(0, 1fr) minmax(300px, 32%);
  }
}
.module {
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  border: 1px solid var(--panel-edge);
  border-radius: 8px;
  background: var(--panel);
}
.module__body {
  min-height: 0;
  overflow: auto;
  padding: 16px;
}
```

分栏数量由当前任务而不是固定三栏决定；辅助模块收起后，主模块占满可用宽度。窄屏由页面决定不渲染辅助模块，
再通过抽屉或原位切换提供入口，不能用 CSS 把两个模块纵向堆满却保留相同导航负担。不要为了复刻原型增加
统计卡、评分卡或演示按钮，也不要把每个字段拆成独立卡片。

应用位置需要把库页与作品页区分，不能继续假设所有合法位置都有 `workspaceId`：

```typescript
// 拟新增类型。WorkspacePage 沿用现有三类作品页面。
type AppPage = WorkspacePage | {
  kind: "library";
  moduleId?: string;
  revisionId?: string;
  workspaceContextId?: string;
};

// 在壳层的作品同步逻辑前区分应用页。
function workspaceToSynchronize(page: AppPage | null): string | null {
  return page && page.kind !== "library" ? page.workspaceId : null;
}
```

库页候选 hash 为 `#/library` 与 `#/library/modules/{moduleId}/revisions/{revisionId}`；
只有 module 时可解析其最新 head，但“当前作品使用版本”必须按绑定精确解析。
作品上下文不代表新的授权，仍核对登记状态；完整 parser／serializer 必须成对实现并验证无效URI与旧链接。

<a id="e3"></a>

## E3. 请求代次与草稿提交快照

建议位置：库选择状态、灵感状态及各自编辑状态；基础工具只负责身份与请求先后。
同一个资源的A→B→A也要区分两次A请求，单独比较ID不足以解决乱序。

```typescript
export class SelectionRequests {
  private epoch = 0;
  private key: string | null = null;

  begin(resourceKey: string) {
    this.key = resourceKey;
    return { key: resourceKey, epoch: ++this.epoch };
  }

  invalidate() {
    this.key = null;
    ++this.epoch;
  }

  accepts(ticket: { key: string; epoch: number }) {
    return ticket.key === this.key && ticket.epoch === this.epoch;
  }
}
```

每个状态实例拥有自己的代次管理器。选中详情时建立包含库、模块和版本的key；
成功、失败、`finally`更新loading都先核对ticket。关闭或更换作品上下文使旧选择失效。
取消网络请求可以节省工作，但不能代替身份核对。

```typescript
type DraftAddress =
  | { kind: "idea"; workspaceId: string; ideaId: string }
  | { kind: "module"; libraryId: string; moduleId: string; baseRevision: string };

export function draftKey(address: DraftAddress): string {
  return address.kind === "idea"
    ? JSON.stringify(["idea", address.workspaceId, address.ideaId])
    : JSON.stringify([
        "module", address.libraryId, address.moduleId, address.baseRevision,
      ]);
}

type SubmittedDraft<T> = {
  draftKey: string;
  editSequence: number;
  requestKey: string;
  payload: T;
  applyWorkspaceId: string | null;
};

export function canClearSubmittedDraft(
  current: { key: string; editSequence: number },
  submitted: SubmittedDraft<unknown>,
): boolean {
  return current.key === submitted.draftKey
    && current.editSequence === submitted.editSequence;
}
```

提交时复制payload并只生成一次请求键；网络失败后显式重试使用相同对象。完成操作不读取
“现在选中的作品”作为目标。新版本发布成功后原草稿基础版本的迁移需要原子更新缓存键和当前指针；
若提交后又有编辑，则保留新输入，并以已发布的提交快照作为新基线。冲突失败不自动重建基线。
跨资源保存可更新原草稿记录与通知，不能改写当前编辑器。

<a id="e4"></a>

## E4. 模块完整读取、字段编辑与应用恢复

### E4.1 消费已有的区间读取接口

建议先修改 `api/moduleLibrary.ts` 的现有函数，新增选项是客户端适配，服务端已支持start/end：

```typescript
export function fetchRecord(
  moduleId: string,
  revisionId: string,
  entryId: string,
  options: { start?: number; end?: number; signal?: AbortSignal } = {},
): Promise<EntryRecord> {
  const suffix = query({ start: options.start ?? 0, end: options.end });
  return request<EntryRecord>(
    `${LIBRARY}/modules/${moduleId}/revisions/${revisionId}/records/${entryId}${suffix}`,
    { signal: options.signal },
  );
}
```

以下完整读取函数接收按精确模块／版本构造的读取器和manifest digest。
区间由服务端按Unicode码点计数，不能用JavaScript的UTF-16 `text.length`判断一致性。

```typescript
export async function readCompleteRecord(
  expected: { entryId: string; digest: string },
  readChunk: (start: number) => Promise<EntryRecord>,
): Promise<string> {
  let cursor = 0;
  let total: number | null = null;
  const chunks: string[] = [];

  function invalid(): never {
    throw new Error(
      "[RECORD_READ_INCOMPLETE] 模块原文：读取区间不完整。建议：重新读取后再编辑。",
    );
  }

  for (;;) {
    const part = await readChunk(cursor);
    if (part.entry_id !== expected.entryId || part.digest !== expected.digest) invalid();
    if (total === null) total = part.length;
    if (!Number.isInteger(total) || total < 0 || part.length !== total) invalid();
    if (part.start !== cursor || !Number.isInteger(part.end)) invalid();
    if (part.end < cursor || part.end > total) invalid();
    if (Array.from(part.text).length !== part.end - part.start) invalid();
    if (part.truncated !== (part.end < total)) invalid();
    chunks.push(part.text);

    if (!part.truncated) {
      if (part.end !== total || part.next_cursor !== null) invalid();
      return chunks.join("");
    }
    if (part.end <= cursor || part.next_cursor !== part.end) invalid();
    cursor = part.end;
  }
}
```

整份拼接后再解析一次头部，保留manifest元数据；所有条目完整加载后才允许发布整版。
实际读取受同一选择ticket与AbortSignal约束，条目读取采用有界并发，不对全库展开请求。
往返按后端既有头部与正文序列化规则核对内容，不要求新旧revision的原始文件digest相等。

### E4.2 字段表单使用后端契约

建议位置：`src/hastings/library/presets.py`、`api/moduleLibrary.ts` 与 `components/library/EntryFields.tsx`。
现有预设对象已经有 `allowed_fields`，只需在既有响应中输出，前端不再维护第二份字段表：

```typescript
export interface ModuleSpec {
  kind: ModuleKind;
  label: string;
  source_kinds: string[];
  organize_domain: string | null;
  allowed_fields: string[];
}
```

普通表单只处理这份字段清单中的标量字段。2026-10-25 核对并由作者确认：服务端也允许标量列表，
列表在高级文本区编辑并沿用既有校验。未知字段名、嵌套对象及含非标量的列表会被拒绝；界面保留输入、
显示诊断并阻止发布，不自行猜测字段或把失败解析成空对象。

```typescript
export function patchFields(
  original: Record<string, unknown>,
  changes: Record<string, unknown>,
  removed: readonly string[] = [],
): Record<string, unknown> {
  const next = { ...original, ...changes };
  for (const key of removed) delete next[key];
  return next;
}

type ParsedChapter =
  | { ok: true; value: number | null }
  | { ok: false; message: string };

export function chapterInput(text: string): ParsedChapter {
  const value = text.trim();
  if (value === "") return { ok: true, value: null };
  const number = /^\d+$/.test(value) ? Number(value) : Number.NaN;
  return Number.isSafeInteger(number)
    ? { ok: true, value: number }
    : {
        ok: false,
        message: "[CHAPTER_INPUT_INVALID] 计划章节：需要非负整数。建议：修改章号或留空。",
      };
}
```

例中null是“作者清空输入”的前端命令，适配器据对应字段契约删除key，不直接写入后端。
章号0必须保留，不能用 `value || undefined`丢掉。各字段的更细合法性以现有服务校验为准；
解析失败不得用空对象替换，也不得把未知字段当成可发布内容。没有普通控件的允许字段可以放明确展开的高级文本区，
但其内容仍须通过服务端契约。

### E4.3 已发布的版本只重试应用

建议位置：`state/workspaceAssembly.ts`，与库发布状态分开。
`module-updates`返回 `published=true` 时立即记录其完整 `module` 引用，即使binding_updated为false。

```typescript
type ApplyPublishedIntent = {
  workspaceId: string;
  published: AssemblyModuleRef;
  // 首次提交时看到的目标绑定；null表示原本未装入。
  observed: AssemblyBinding | null;
  requestKey: string;
};

function sameVersion(a: AssemblyModuleRef, b: AssemblyModuleRef): boolean {
  return a.library_id === b.library_id
    && a.module_id === b.module_id
    && a.revision_id === b.revision_id;
}

export async function applyPublished(intent: ApplyPublishedIntent): Promise<AssemblyView> {
  const current = await fetchAssembly(intent.workspaceId);
  const target = current.bindings.find(
    (binding) => binding.module.module_id === intent.published.module_id,
  );
  if (target && sameVersion(target.module, intent.published)) return current;

  const unchanged = intent.observed === null
    ? target === undefined
    : target?.id === intent.observed.id
      && sameVersion(target.module, intent.observed.module);
  if (!unchanged || (
    current.library_id !== null
    && current.library_id !== intent.published.library_id
  )) {
    throw new Error(
      "[ASSEMBLY_STALE_CONFLICT] 作品模块：使用版本已变化。建议：查看当前版本后重新选择。",
    );
  }

  const bindings = current.bindings.map((binding) => ({
    id: binding.id,
    label: binding.label,
    module: binding.id === target?.id ? intent.published : binding.module,
  }));
  const input = {
    bindings: target ? bindings : [...bindings, { id: undefined, label: "", module: intent.published }],
    links: current.links,
    expected_digest: current.digest,
    request_key: intent.requestKey,
  };
  return putAssembly(intent.workspaceId, input);
}
```

这里没有再次调用publish。原请求成功与否不确定时，已构建的assembly请求必须保存为同键同体的命令再重试；
不能重读后改变payload却继续用旧key。上例展示首次构建与提交，实际命令层负责保留input、网络重试和结果归属。
确定收到版本冲突后，由作者查看新状态并发起新命令；忙冲突可以在任务结束后重试。

<a id="e5"></a>

## E5. 发送操作与参考展示

建议位置：`components/workspace/ConversationPanel.tsx`中提取的ChatComposer。
输入法确认和模型提交是不同操作；发送入口在窄窗口仍保持可见。

```tsx
import type { KeyboardEvent } from "react";

function composerKeyDown(
  event: KeyboardEvent<HTMLTextAreaElement>,
  canSend: boolean,
  submit: () => void,
): void {
  if (event.nativeEvent.isComposing || event.keyCode === 229) return;
  if (event.key === "Enter" && !event.shiftKey && canSend) {
    event.preventDefault();
    submit();
  }
}

/** 发送条件只有一个入口：草稿、运行状态和模型可用性共同决定。 */
export function canSubmit(input: {
  draft: string;
  running: boolean;
  pending: boolean;
  otherRunning: boolean;
  modelAvailable: boolean;
}): boolean {
  return input.modelAvailable
    && input.draft.trim().length > 0
    && !input.running
    && !input.pending
    && !input.otherRunning;
}
```

模型不可用时保留草稿并禁用发送，就近提供连接入口，不能只在提示条里说明而让按钮保持可点击。
撤销属于结果卡上的独立危险操作：先展示将回退的提交与受影响文件，确认后才调用 `onRevert`；
执行期间禁用重复点击，失败保留原结果与错误信息。

展示层可以统一参考标签，但传输仍按真实接口适配：

```typescript
type ComposerReference =
  | { kind: "file"; path: string; title: string }
  | { kind: "idea"; ideaId: string; revisionId: string; title: string };

export function contextReferences(rows: readonly ComposerReference[]) {
  return {
    reference_paths: rows.flatMap((row) => row.kind === "file" ? [row.path] : []),
    idea_references: rows.flatMap((row) => row.kind === "idea"
      ? [{ idea_id: row.ideaId, revision_id: row.revisionId }]
      : []),
  };
}
```

完整ContextInput继续由既有发送逻辑补齐selected_path与observed_digests；去重、会话归属与受理确认
沿用现有状态。这里不引入record草稿参考；实际读取模块条目的结果按现有事件进入本轮材料。
输入区、结果卡与执行过程分别提取组件，但不能让布局挂载决定任务是否存在。

<a id="e6"></a>

## E6. 灵感历史必须先可查看，再明确恢复目标

建议位置：`components/ideas/IdeaHistory.tsx`和`state/ideas.ts`。历史预览与当前编辑草稿使用不同状态，
点击历史不会把未保存正文替换成历史正文。

```typescript
type RevisionPreview = Awaited<ReturnType<typeof getIdeaRevision>>;

export function revisionAction(
  currentRevision: string,
  preview: RevisionPreview,
  dirty: boolean,
  busy: boolean,
): { label: string; disabled: boolean } {
  return {
    label: preview.deleted ? "恢复为已删除状态" : "恢复这一版内容",
    disabled: dirty || busy || preview.revision_id === currentRevision,
  };
}

export function ideaTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium", timeStyle: "short",
  }).format(date);
}
```

界面时间使用用户设备本地时区，原始时间留在详情；本次计划与验证记录按Asia/Shanghai标注。
历史分页继续使用listIdeaRevisions的next_cursor。恢复命令携带当前digest和所选target_revision，
服务端冲突保留当前草稿和预览。新建临时条目也需要稳定草稿ID，避免所有未保存条目共用一个槽位。

<a id="e7"></a>

## E7. 资料读取状态与操作范围

建议位置：资料页容器与各列表。读取状态支持有旧数据的后台刷新，失败不能把原内容变成“没有资料”。
下面是页面局部状态，不建立额外的通用资源框架。

```typescript
type ResourceView<T> =
  | { state: "loading" }
  | { state: "failed"; error: string }
  | { state: "ready"; data: T; refreshing: boolean; refreshError?: string };

type BrowserFilter = { chapter: number | null; order: "chapter" | "story" };
type OrganizeSelection = {
  domain: MaterialDomain;
  chapterPaths: string[];
  entityPaths: string[];
  asOfChapter: number;
  observedDigests: Record<string, string>;
};

export function freezeSelection(value: OrganizeSelection): OrganizeSelection {
  return {
    ...value,
    chapterPaths: [...value.chapterPaths],
    entityPaths: [...value.entityPaths],
    observedDigests: { ...value.observedDigests },
  };
}
```

BrowserFilter只决定查询；范围选择面板确认后用freezeSelection创建独立提交快照，
不再从之后改变的URL读取任务范围。`ready`下根据真实entries判断空结果，后台重读时保持可读内容，
就近显示更新状态或重试；首次loading不同时渲染“没有角色小传”。
任务条、依据有效性与整理覆盖是不同事实，不能合成一个“全部完成”徽章。

<a id="e8"></a>

## E8. 共用弹窗外壳与错误信封

建议位置：`components/ui/AppDialog.tsx`，复用已有的 `@radix-ui/react-dialog`。
业务模块负责保存与关闭草稿，外壳负责焦点、Esc和层级，不在外壳中自动保存或停止任务。

```tsx
import { useId, type ReactElement, type ReactNode } from "react";
import * as Dialog from "@radix-ui/react-dialog";

export function AppDialog(props: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  trigger: ReactElement;
  title: string;
  description?: string;
  children: ReactNode;
}) {
  const descriptionId = useId();
  return (
    <Dialog.Root open={props.open} onOpenChange={props.onOpenChange}>
      <Dialog.Trigger asChild>{props.trigger}</Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="ui-dialog-overlay" />
        <Dialog.Content
          className="ui-dialog"
          aria-describedby={props.description ? descriptionId : undefined}
        >
          <header>
            <Dialog.Title>{props.title}</Dialog.Title>
            <Dialog.Close asChild><Button variant="quiet">关闭</Button></Dialog.Close>
          </header>
          {props.description && (
            <Dialog.Description id={descriptionId}>{props.description}</Dialog.Description>
          )}
          {props.children}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
```

实际壳层还有多入口打开浮层的情况，必须记录真实触发元素并在其仍连接时恢复焦点；
没有触发元素则回到当前页面稳定工具入口。大编辑器使用完整管理页；需要侧面板时复用同一焦点规则。
关闭模型连接表单清空临时Key，不能套用普通内容草稿的保存规则。

错误适配保留服务端结构。当前WorkspaceApiError尚未保留where，需补字段并更新所有构造位置，
不把下面的拟新增格式函数误当作当前异常已经具备该字段：

```typescript
type ErrorEnvelope = { code: string; where: string; why: string; fix: string };

export function errorMessage(error: ErrorEnvelope): string {
  const sentence = (value: string) => value.trim().replace(/[。.!！]+$/, "");
  return `[${error.code}] ${error.where}：${sentence(error.why)}。建议：${sentence(error.fix)}。`;
}
```

网络和未知异常由调用边界转换为有位置与建议的信封。界面根据code分支，
不根据中文message推断冲突；原堆栈只留诊断，不进入作者主要流程。

<a id="e9"></a>

## E9. 验证真实行为

新浏览器文件必须被当前workspace项目选择：

```typescript
// 作为 playwright.config.ts 中 workspace 项目的 testMatch，保留原文件。
const workspaceTestMatch = [
  "workspace.spec.ts",
  "chat-rendering.spec.ts",
  "frontend-experience.spec.ts",
];
```

布局与数据保护断言比“没有横向滚动”更能识别已发现问题。以下夹具为拟新增：
`workspace`负责隔离创建作品、章节、库和版本，并提供ID；`publishedCount`读取隔离库的修订数。
夹具不能使用作者最近打开的作品，也不能依赖在线模型生成固定测试内容。

```typescript
import { test, expect } from "./fixtures/frontendExperience";

test("640宽度仍能到达正文并阅读内容", async ({ page, workspace }) => {
  await page.setViewportSize({ width: 640, height: 800 });
  await page.goto(`/#/workspaces/${workspace.id}/writing`);
  await page.getByRole("button", { name: "目录", exact: true }).click();
  await page.getByRole("button", { name: workspace.chapterTitle, exact: true }).click();

  const paper = page.getByRole("region", { name: "正文阅读", exact: true });
  await expect(paper).toBeVisible();
  await expect(paper).toContainText(workspace.openingSentence);
  const box = await paper.boundingBox();
  expect(box).not.toBeNull();
  expect(box!.width).toBeGreaterThanOrEqual(560);
});

test("关闭模块管理再返回仍保留未发布内容", async ({ page, workspace }) => {
  await page.goto(workspace.moduleUrl);
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  await page.getByLabel("条目正文", { exact: true }).fill("尚未发布的修改");
  const before = await workspace.publishedCount();
  await page.getByRole("link", { name: "回到写作", exact: true }).click();
  await page.getByRole("link", { name: "模块库", exact: true }).click();
  await page.getByRole("button", { name: workspace.moduleName, exact: true }).click();
  await page.getByRole("button", { name: "编辑", exact: true }).click();

  await expect(page.getByLabel("条目正文", { exact: true })).toHaveValue("尚未发布的修改");
  expect(await workspace.publishedCount()).toBe(before);
});
```

第二例通过界面导航保持同一应用会话。不能用会重载文档的page.goto验证仅会话内草稿，
也不能据该用例声称刷新恢复已支持。最终角色和名称以实施后的可访问界面为准。

另按UI06–UI09、UI16编写以下断言：

- 延迟A详情响应，在B完成后再释放A，检查标题、正文、选中版本及loading仍属于B；再覆盖A→B→A。
- 用16000以上码点、包含emoji与长头部的条目验证完整读取和发布，核对新版本全部正文与字段。
- 分段读取在第二段失败或cursor不前进，确认发布按钮不可用且修订数不增加。
- 发布成功后制造装配冲突，重试应用时计数publish请求为0，检查其他绑定与links保持。
- 保存请求已受理而响应丢失，同键同体重试只产生一次发布；草稿后来修改不被清空。
- 无可用模型时输入并确认草稿仍在、发送不可用；连接入口可达，不能出现“提示未连接但按钮可发送”。
- 点击撤销先看到提交与影响文件；取消不发送回退请求，确认只发送一次，执行中按钮不可重复触发。

视觉走查记录窗口、字号、页面状态、主要操作、对比、裁切与焦点结论，并核对模块是否按职责成组、
主模块是否获得足够空间、字号是否被压缩。截图只是证据之一，不用固定颜色字面量测试证明页面好用。
`640px` 是桌面窄窗口的行为门槛；`390px` 只记录观察结果，除非移动端被单独确认为产品目标。

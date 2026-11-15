# Hastings 0.4 写作台界面浅色化与交互修复开发计划

> 编写日期：2026-10-21。状态：V04-11 与 V04-12 均已实施（2026-10-21，交付记录见 [V04-11 阶段记录](notes/v04-11-layout-interactions.md)、[V04-12 阶段记录](notes/v04-12-ios6-light.md)）。本文保留为目标与验收口径；已实现行为见[写作台界面](modules/desk.md)。
> 已确认：整体浅色化，回到 iOS 6 拟物设计系统；先做交互修复（V04-11），再做视觉统一（V04-12）。
> 现状入口：[写作台界面](modules/desk.md)、[会话管理开发计划](conversation-management-plan.md)。视觉参考：`prototypes/ios6/`（既有视觉稿）。

## 1. 目标与范围

解决两个问题：

1. **风格断裂**。`src/theme.ts` 是 iOS 6 拟物浅色令牌的单一事实来源，`index.css` 保留了全套拟物组件类（金属顶栏、高光按钮、分组面板、内凹输入井），`tailwind.config.js` 注释明确「新页面一律使用具名令牌或组件类，不得再用旧暗色名」。但 0.4 新壳层（`components/workspace/`）绕开了这套系统，手写扁平深蓝内联色，导致金属质感的顶栏下面接着三块扁平深色区域。
2. **三个交互缺陷**：对话栏不能隐藏；目录栏与对话栏宽度写死不能拖拽；稿纸横线背景画在滚动容器自身上，不随内容滚动。

### 1.1 交付范围

- V04-11（交互修复）：对话栏可显隐；目录栏与对话栏可拖宽并记住偏好；稿纸横线随内容滚动。
- V04-12（浅色化）：写作台三栏、对话栏、稿纸、空态与提示条全部接回 iOS 6 浅色设计系统；对话气泡改为 iOS 6 信息风格（我方绿色渐变）；清理不再使用的深色内联色与遗留 CSS。

### 1.2 不进入本阶段

- 不改任何后端接口与状态结构；不改对话功能行为（会话管理属 V04-10）。
- 不做响应式断点重排、不做移动端布局；不做暗色模式切换（浅色是唯一主题）。
- 不动 `components/models/`（模型设置对话框已用浅色组件类）；不动 `components/ui/`。
- 稿纸横线只做装饰级对齐：保证随文字滚动、段落行大致落在线上；不把标题、列表等所有元素的行高钉成线格倍数（排版会僵硬，见 3.4 取舍）。

## 2. 现状核对

改动集中在四个文件，均在前端：

| 文件 | 现状问题 |
|---|---|
| `components/workspace/WorkspaceShell.tsx` | 三栏 `grid-cols-[260px_minmax(0,1fr)_380px]` 宽度写死；目录栏、中栏底写死 `bg-[#12354f]` 深蓝；对话栏无条件渲染 |
| `components/workspace/ConversationPanel.tsx` | 整面板深蓝内联色（`#12354f` 底、`#1b4666` 卡片、`#0e2b41` 输入井、`#5a2230` 错误底），无显隐能力 |
| `components/workspace/Paper.tsx` | `.paper-sheet` 同时是滚动容器（`overflow-auto`）和横线背景承载者——滚动容器自身的背景不随内容滚动，这是横线「钉死」的直接原因 |
| `index.css` | `.chat-resize` 拖拽把手样式是 0.3 遗留，当前无组件引用；0.3 的 `chat-*` 组件类（壳、气泡、输入区）完整保留但新对话栏未使用 |

可复用资产：`theme.ts` 全部浅色令牌；`index.css` 的 `.panel` / `.btn` / `.well-input` / `.chat-shell` / `.chat-bubble` / `.chat-composer` / `.chat-input` 等；`prototypes/ios6/` 视觉稿。浅色化以「替换类名、删除内联色」为主，新增 CSS 很少。

## 3. V04-11：交互修复

### 3.1 布局状态：新建 `state/layout.ts`

布局偏好（对话栏显隐、两个栏宽）是界面状态，不属于作品数据，独立成一个小 store。持久化用手写 localStorage 读写（项目现有 store 均未引入持久化中间件，不为此加依赖）：

```ts
import { create } from "zustand";

const STORAGE_KEY = "hastings.workspace.layout";

export const SIDEBAR = { default: 260, min: 180, max: 400 };
export const CHAT = { default: 380, min: 280, max: 560 };

interface LayoutState {
  sidebarWidth: number;
  chatWidth: number;
  chatVisible: boolean;
  setSidebarWidth: (width: number) => void;
  setChatWidth: (width: number) => void;
  toggleChat: () => void;
}

function load(): Pick<LayoutState, "sidebarWidth" | "chatWidth" | "chatVisible"> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<LayoutState>;
      return {
        sidebarWidth: clamp(parsed.sidebarWidth, SIDEBAR),
        chatWidth: clamp(parsed.chatWidth, CHAT),
        chatVisible: parsed.chatVisible ?? true,
      };
    }
  } catch {
    // 损坏的偏好按默认值处理，不阻断启动
  }
  return { sidebarWidth: SIDEBAR.default, chatWidth: CHAT.default, chatVisible: true };
}

export const useLayoutStore = create<LayoutState>((set, get) => ({
  ...load(),
  setSidebarWidth: (width) => {
    set({ sidebarWidth: clamp(width, SIDEBAR) });
    persist(get());
  },
  setChatWidth: (width) => {
    set({ chatWidth: clamp(width, CHAT) });
    persist(get());
  },
  toggleChat: () => {
    set((state) => ({ chatVisible: !state.chatVisible }));
    persist(get());
  },
}));
```

取舍：拖拽过程中直接写 store 并持久化即可——宽度变化只影响壳层一次重渲染，不需要拖拽中暂存、松手才提交的优化。

### 3.2 栏宽拖拽：`components/workspace/ResizeHandle.tsx`

一个通用把手组件 + pointer 事件。左栏向右拖变宽；右栏向左拖变宽，所以带 `invert`：

```tsx
interface Props {
  value: number;
  min: number;
  max: number;
  invert?: boolean; // 右栏把手：指针左移 = 变宽
  onChange: (width: number) => void;
  onReset: () => void;
}

export default function ResizeHandle({ value, min, max, invert, onChange, onReset }: Props) {
  const startDrag = (event: React.PointerEvent) => {
    event.preventDefault();
    const startX = event.clientX;
    const startValue = value;
    const onMove = (move: PointerEvent) => {
      const delta = (move.clientX - startX) * (invert ? -1 : 1);
      onChange(Math.min(max, Math.max(min, startValue + delta)));
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      className="chat-resize"
      onPointerDown={startDrag}
      onDoubleClick={onReset}
      title="拖动调整宽度，双击恢复默认"
    />
  );
}
```

把手直接复用 `index.css` 遗留的 `.chat-resize`（0.3 已有 hover 反馈），只需把它从「绝对定位」改为布局内普通元素（`flex-none w-1.5 cursor-col-resize`），原类改造成新用途后删除旧的绝对定位规则。

### 3.3 壳层布局改造（`WorkspaceShell.tsx`）

网格改为 flex 行，宽度来自 layout store；对话栏显隐由条件渲染控制：

```tsx
<main className="flex min-h-0 flex-1 overflow-hidden">
  <nav className="flex min-h-0 flex-none flex-col ..." style={{ width: sidebarWidth }}>
    {/* 目录内容不变 */}
  </nav>
  <ResizeHandle
    value={sidebarWidth} min={SIDEBAR.min} max={SIDEBAR.max}
    onChange={setSidebarWidth} onReset={() => setSidebarWidth(SIDEBAR.default)}
  />
  <section className="min-w-0 flex-1 ...">
    {/* 稿纸 / 空态不变 */}
  </section>
  {currentId && chatVisible && (
    <>
      <ResizeHandle
        value={chatWidth} min={CHAT.min} max={CHAT.max} invert
        onChange={setChatWidth} onReset={() => setChatWidth(CHAT.default)}
      />
      <div className="flex-none" style={{ width: chatWidth }}>
        <ConversationPanel />
      </div>
    </>
  )}
</main>
```

顶栏加显隐开关（iOS 6 分段控件语言，选中态表示「对话栏开启」）：

```tsx
<div className="seg text-[11px]">
  <button className={chatVisible ? "on px-2 py-1" : "px-2 py-1"} onClick={toggleChat}>
    对话
  </button>
</div>
```

显隐只是不渲染面板，会话状态在 `state/conversations.ts`，重新打开时上下文、草稿、运行状态都还在。`ConversationPanel` 根节点的 `border-l` 移到壳层把手上，避免隐藏时留下边框。

### 3.4 稿纸横线随内容滚动（`Paper.tsx` + `index.css`）

修复原则：背景跟着内容走，就把背景画在内容层上。`.paper-sheet` 保留米白纸底、边框与投影，横线移到内层内容 div：

```tsx
<div className="paper-sheet min-h-0 flex-1 overflow-auto rounded-sm">
  <div className="paper-lined min-h-full px-8 py-6">
    {/* ReactMarkdown 内容 */}
  </div>
</div>
```

```css
.paper-lined {
  background-image: repeating-linear-gradient(
    0deg,
    transparent 0,
    transparent 31px,
    rgba(180, 170, 140, 0.18) 31px,
    rgba(180, 170, 140, 0.18) 32px
  );
  background-position-y: 26px; /* 让段落文字基线落在格线上，按实际视觉效果微调 */
}
```

内层 div 随内容滚动，横线自然跟着走；`min-h-full` 保证短文时横线铺满整页。

取舍（装饰级对齐）：线周期 32px 与段落 `leading-8` 相同，段落区能对齐；但标题、列表、引用块的高度不是 32px 倍数，经过这些元素后横线与文字会漂移。这是可接受的装饰定位——横线是纸面质感，不是书写格。若未来要严格稿纸格，需要给所有 Markdown 元素统一 32px 倍数行高，本阶段不做。

## 4. V04-12：iOS 6 浅色化

总原则：删除内联深色，改用 `theme.ts` 令牌与 `index.css` 组件类；新样式只在设计系统没有对应物时新增，并落在组件类里，不再写内联 hex。

### 4.1 色彩映射

| 现状（深色内联） | 改为（令牌 / 组件类） |
|---|---|
| 三栏底 `bg-[#12354f]` | 目录栏 `bg-page`（浅灰亚麻底 `#E7EAEB`）；中栏 `workbench` 织物底；对话栏 `.chat-shell`（白渐变面板） |
| 深色分隔 `border-[#355f7f]` | `border-panel-edge` / `border-line` |
| 目录选中 `bg-[#2b5f85]` | `.asset-row.sel` 蓝色高光渐变（`selHigh → selLow`） |
| 目录悬停 `hover:bg-[#1b4666]` | 白底行 + `hover:bg-black/5` 或新增 `.toc-row` 组件类 |
| 对话卡片 `bg-[#1b4666]` | `.chat-card`（白渐变 + 边） |
| 输入井 `bg-[#0e2b41]` | `.chat-input` / `.well-input`（内凹浅色井） |
| 错误底 `bg-[#5a2230]` | `.chat-error` / `bg-red-bg + text-red + border-red-edge` |
| 白字 `text-white` 系 | `text-fg` / `text-sub` / `text-mute` |
| 工具成功 `text-[#9fd2a8]` / 失败 `text-[#f0a8a8]` | `text-green` / `text-red`（浅底上原色对比度不够，必须用语义令牌） |

### 4.2 目录栏（`WorkspaceShell.tsx` 的 nav）

按 iOS 6 分组表样式：浅灰底、白色行、「正文 / 设定集」两组用 `.panel-head` 风格的小标题。选中行蓝色高光渐变 + 白字，缺失标识用 `text-mute`。底部路径栏改为 `.statusbar` 或浅灰小字。搜索框用 `.well-input` 内凹井。

### 4.3 对话栏（`ConversationPanel.tsx`）

根节点换 `.chat-shell`；头部（会话选择、新对话、改名）用 `.chat-head`；消息区 `.chat-scroll`；作者消息与回复改为气泡：

- 作者（我方）：iOS 6 信息绿渐变气泡 + 白字 + `0 -1px` 文字阴影。现有 `.chat-msg.mine .chat-bubble` 是蓝色渐变，改令牌为绿（参考 `.ios-switch.on` 的 `#8cc694 → #4c965e`），气泡带圆角与下侧小尾巴（`:after` 三角形）。
- Agent（对方）：`.chat-bubble` 白灰渐变 + 深色字。
- `tool_started` / `tool_finished` 保持小字行，颜色换 `text-mute` / `text-green` / `text-red`。
- 结果卡（run_finished）用 `.chat-card-result`（绿边浅底）；撤销、补记按钮继续用 `.btn btn-gray`。
- 输入区 `.chat-composer` + `.chat-input` + `.chat-send`（发送用 `.btn-gold` 保持现状语义）。

`index.css` 里 0.3 的 `chat-*` 类与新面板结构不完全一致（ refs 条、意图条、任务卡等新面板没有），复用时只取壳/气泡/输入区/错误条，未复用的类随本阶段确认无引用后删除，不留死样式。

### 4.4 稿纸与空态

- `.paper-sheet` 米白纸面保持不变（它本来就是浅色设计），配合 3.4 的横线修复；可选加左侧红色笔记本边线（`border-left` 或 1px 渐变条），按 `prototypes/ios6` 视觉稿决定。
- 空态（未打开目录、未打开文章）从白字深蓝底改为 `text-sub` 置于织物底上，按钮继续 `.btn-gold`。
- 错误条、通知条已有浅色类（`red-*` / `notice`），把 WorkspaceShell 里手写的 `border-red-edge bg-red-bg` 等换成 `.notice` / `.chat-error` 风格统一。

### 4.5 清理

- 全仓 grep 确认 `#12354f`、`#1b4666`、`#2b5f85`、`#355f7f`、`#0e2b41`、`#5a2230` 在 `components/workspace/` 清零。
- 删除未被复用的 0.3 遗留 CSS（以复用结果为准，逐个确认无引用）。
- `tailwind.config.js` 的「旧暗色令牌兼容映射」段落在注释中标注仅供 `components/ui/` 过渡，本阶段不扩大其使用面。

## 5. 实施顺序与验收

V04-11 → V04-12 顺序实施，各成阶段记录（`notes/v04-11-*.md`、`notes/v04-12-*.md`）。

**V04-11 验收**：

- 新增 `state/layout.test.ts`：默认值、损坏 localStorage 回退默认、min/max 钳制、显隐切换与持久化。
- `npx vitest run`、`npm run build` 通过。
- 浏览器走查（`e2e/walkthrough-v04-workspace.mjs` 扩展或手动）：拖两个把手改宽度、双击复位、刷新后宽度保留；顶栏开关隐藏/显示对话栏，隐藏时稿纸占满，重开后草稿与运行状态仍在；长章节滚动时横线随文字移动、段落行大致对线。

**V04-12 验收**：

- `npm run build` 通过；不新增 vitest 用例（纯样式），走查覆盖：三栏、目录选中/悬停、对话气泡（含工具行与结果卡）、输入区、错误与通知条、空态，对照 `prototypes/ios6/` 视觉稿逐屏核对。
- 走查结论写入阶段记录，注明证据范围。

## 6. 风险与取舍

- **对比度**：深色底上的浅色字换成浅底深字后，所有「白字 + 彩色」组合必须改用语义令牌（绿/红/琥珀），不能保留原浅亮色，否则浅底上对比度不达标。走查时逐项核对。
- **拖拽与滚动冲突**：把手只有 6px 宽，不改正文的滚轮与触摸滚动；`pointerdown` 后 `preventDefault` 避免拖出文本选择。
- **窗口过窄**：栏宽有 min/max，但窗口宽度小于「两栏最小值 + 稿纸可读宽度」时不做响应式重排（本阶段明确不做移动端），允许横向挤压，交给 1.2 的边界声明。
- **横线对齐漂移**：装饰级定位已写入 1.2 与 3.4，验收只查「随滚动 + 段落大致对齐」，不查全元素钉格。

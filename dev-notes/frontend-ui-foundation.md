# 构建笔记：前端基础包与组件库改造（shadcn/ui + Radix）

> 对应计划 [.trae/documents/frontend-ui-foundation_plan.md](../.trae/documents/frontend-ui-foundation_plan.md)。
> 本改造不在 DESIGN.md §8 路线图内（不占用顺延编号）。范围：把手写 UI 原语渐进替换为
> shadcn/ui 模式（Radix 无头原语 + CVA + Tailwind，组件源码复制进 `frontend/src/components/ui/`），
> 保持暗墨+金视觉与 `theme.ts` 单一事实源。后端零改动、`state/`/`api/` 零改动、WS/REST 契约零改动、
> `tau_agent`/`tau_ai` 零改动。
> 关键词：shadcn/ui、Radix、lucide、组件原语迁移、主题桥接、暗墨+金视觉。

## 1. 交付内容

交付链（13 个原子提交，每原语一提交）：`c000016`（P0 地基：Radix 按需 10 包 + CVA/clsx/tailwind-merge + `@/` 别名 + tailwind 语义色桥接 + `lib/utils.ts` + ui/button/badge）→ `71b7fce`（P1-1 Button 全站替换，69 处裸 button 清零）→ `07e1d39`（P1-1 fix：顶栏导航 Button 补压缩折行样式）→ `7690f6b`（P1-2 Badge：Chip/CHIP_CLASS 退役）→ `28b2434`（P1-3 AlertDialog：新会话确认 + 删文章确认）→ `5d5646f`（P1-4 Sheet：账本右抽屉 + 对话左抽屉）→ `c90cd0d`（P1-5 Select：窄屏导航/章节/伏笔筛选）→ `9a9d535`（P1-6 Tabs：五账本 + 角色 list/graph）→ `ef2cc9d`（P1-7 Popover：SearchBox 浮层）→ `1a88768`（P1-8 Tooltip：图标按钮）→ `869c991`（P1-9 表单族：Input/Textarea/Checkbox + LedgerPage 双筛选 Select）→ `fac8215`（P2 lucide：顶栏导航图标迁移）→ `6db32cf`（P2 fix：导航图标化后中间带布局修复，内联门槛提至 xl）。

`ui/` 原语清单：button、badge、alert-dialog、sheet、select、tabs、popover、tooltip、input、textarea、checkbox（scroll-area/switch/label/dialog 未落地，见 §7 留白）。

## 2. 关键决策（落地与偏离）

- **主题桥接不用 CSS 变量双事实源**：shadcn 语义名（primary/background/surface/border/foreground…）在 `tailwind.config.js` 直接映射 theme.ts 色值，Radix portal 到 body 的弹层靠全局 Tailwind 类保持暗色令牌。
- **ScrollArea 跳过**：现有 `overflow-auto` 容器滚动行为正常，替换收益低（计划已预留「可选，末批」），P2 不做。
- **羊皮纸 textarea 保留原生**：ManuscriptPanel 编辑态 textarea 是无边框羊皮纸特化（ChapterPaper 承载），套 Input/Textarea 基类反而要全量覆盖；保留原生并记为特例。
- **SettingsPanel 整文件隔离**：工作区存在 ollama WIP（未提交，涉及 SettingsPanel/providerLogin/LocalModelCard 等）。采用隔离暂存法：备份 WIP → checkout HEAD → 本任务迁移 → 提交 → WIP 回写叠加。SettingsPanel 的 Select/表单替换随 WIP 合入后再做；LocalModelCard 未跟踪不迁。
- **受控弹窗 busy 拦截**：Radix 在 Action 点击后必发 close 请求；`onOpenChange` 里 `if (!next && busy) return`，失败错误才能留在弹窗内展示（ChatHeader 新会话、ArticlesPage 删文章同款）。
- **SearchBox 用 PopoverAnchor 而非 Trigger**：触发是「输入框聚焦展开」而非点击按钮——Anchor 定锚 + 受控 `open`，Radix 只负责外点/Esc/定位，焦点逻辑保留。
- **导航内联门槛 md→xl（偏离计划 §P1-5 的 <md 口径）**：lucide 图标加入后按钮变宽，md~xl 中间带（768–1280）会把按钮压到逐字竖排（inline-flex 下文字匿名项被压到 min-content）。修复组合：内联导航只在 xl+ 渲染；按钮 `min-w-[58px]`（压缩时每行至少 2 字，最多两行）+ `px-1.5` + `gap-1` + 图标 `shrink-0`；1280 实测整体单行（navH 33.5）。
- **Button 紧凑用法约定**：紧凑按钮一律 `h-auto`（覆盖默认 h-9）；「纯文字按钮」ghost 变体加 `hover:bg-transparent`（覆盖 ghost 默认 hover:bg-accent）。两条已写入 button.tsx 注释。

## 3. Agent 设计经验（四透镜）

> 本阶段是纯前端改造，不碰 agent 循环；但四透镜映射到「组件契约」同样成立——
> shadcn/Radix 模式本质上就是把 agent 设计里的一组手法搬到 UI 层复演了一遍。

### 3.1 错误消息是喂给模型的 API → DOM/data-state 是喂给测试与无障碍树的 API

Radix 每个原语都暴露 `data-state="open|closed"`、`aria-*`、`data-[placeholder]` 这类**字段级事实源**。本阶段最大的走查教训：headless Chrome 里动画不推进，`[role=dialog]` 关闭时 DOM 延迟卸载——看「弹窗还在不在」会误判，读 `data-state` 才是真值。对应 agent 设计里的原话：断言/反馈要读结构化字段（loc、data-state），不要读表象（元素存在与否、渲染位置）。三要素同构：`data-state=closed`（哪里错/当前态）+ 动画未结束的解释（为什么）+ 该用哪个字段断言（怎样算对）。

### 3.2 硬约束放工具层，prompt 只负责解释世界 → 不变量封进 ui/ 原语，业务组件只声明语义

焦点陷阱、Esc 关闭、portal 定位、z 序、`min-w-[var(--radix-select-trigger-width)]`——这些「不做就会错」的不变量全部封在 `components/ui/` 里，业务组件只写「open={x} onOpenChange={setX}」这种**意图声明**。这正对应工具层强制不变量、prompt（业务层）只解释世界的分工：业务代码没有机会忘记外点关闭（它根本不写这段），如同模型没有机会绕过工具层校验。唯一一次反向教训是导航布局：`min-w` 这类布局不变量如果散落在调用点 className 里，就是「把约束写进 prompt」——散、易漏（P1-1 就补了一次 07e1d39）；最后靠「紧凑用法约定写进 button.tsx 注释」收敛，说明约束的**文档位置**也要跟着约束走。

### 3.3 泄压阀——三情形在受控弹窗与空值组件里的对应

- **违规/失败情形不吞**：Radix 在 Action 点击后必发 close，若不拦截，异步失败的错误消息会随弹窗一起消失——用户看到的是「点了没反应」。`onOpenChange` 的 busy 拦截就是把「失败反馈通道」从弹窗生命周期里解耦出来，对应工具层区分「参数违规 → 可修正错误」而非静默重置。
- **合法但空**：Select 的空值场景不用 `value=""`（Radix 禁止 Item 空值）而用 placeholder + SelectValue——「未选择」是一等状态而非错误；搜索无结果时 Popover 内容区渲染「无结果」文案而不是收起浮层，防「道歉-重试」循环的 UI 版。
- **环境故障明确归因**：headless 走查里的 STILL-OPEN、坐标停 790、高度 92 全部先归因为「动画冻结伪象」再换断言口径，而不是改产品代码去迁就测试环境——环境故障不触发产品层「修复」。

### 3.4 可观测性是事件契约的一部分 → 几何断言与 chunk 体积是前端的行为契约

改造前后的等价性不靠「看起来一样」，靠证据链：CDP `getBoundingClientRect` 新旧几何逐元素对比（1280 header 子元素 w/h 全等）、四宽度回归断言清单（navSelect items=6、sheetLedger w=320、tabs count=10、sheetEsc data-state=closed 等）、build chunk 体积逐步记录（438→453→464→589 kB min / 136→142→145→187 kB gzip，Radix+lucide 增量 ~51KB gzip，预算 80KB 内）。同一事件流服务两类读者：给「人」的截图 + 给「断言」的结构化数据——与 AgentToolResult.details 的设计同构。

## 4. 评审记录

- 用户口径：不使用纯逻辑渲染，基于常见基础包与组件库优化改造 → 选型 shadcn/ui 模式（源码进仓，不引重型设计语言库）。
- 视觉等价为硬要求（无色相/层级漂移）：羊皮纸面、账本表格、关系图等定制视觉全部保留；lucide 图标加入属于计划内迁移（§Repository Research 明列 Unicode 符号行），其引发的导航布局调整（md→xl）在 §2 记录理由。

## 5. 踩过的坑（代码级辅助记录）

- **Radix Select 禁止 Item `value=""`**：空值占位用 placeholder + `SelectValue`，运行时才不炸。
- **SelectLabel 的 ref 类型**：Radix Label 内部渲染 div，forwardRef 泛型必须 `HTMLDivElement`，tsc 才放过。
- **twMerge 后传参赢**：`cn()` 里后者覆盖前者同类（`whitespace-normal` 覆盖基类 `whitespace-nowrap`）；变体扩展依赖这个语义，写 className 时顺序敏感。
- **tailwindcss-animate 是 ESM-only**：项目 `type: module`，PostCSS 经典写法下直接 require 会炸，按 v3 插件方式接入。
- **headless 伪象清单**（CDP 走查方法论的一部分，后续走查直接对照）：
  - 后台 tab 合成器不推进 CSS 动画 → Radix 关闭动画（0.15s）不结束 → DOM 延迟卸载 → 用 `data-state` 断言，不用「DOM 存在性」；
  - transform 位移动画帧不推进 → `getBoundingClientRect` 停在动画起点（如 Sheet x=790）→ 位置断言无意义，改结构断言；
  - 残留 `[role=dialog]` 的焦点陷阱会吞脚本派发的 focus → 补测用原生 `el.focus()`。
- **基线 worktree 的 WIP 污染**：历史提交混入过 WIP 文件，checkout 基线后 rest.ts 引用未跟踪的 ollama.ts——复制该文件进 worktree 解决；这也是「WIP 不得混入原子提交」的反面实证。
- **导航逐字竖排根因**：Button 是 inline-flex，文字成为匿名 flex 项，空间不足时压到 min-content（1 字/行）。`min-w` 是给文字保底宽，不是给按钮好看。

## 6. 验证输出存档

- 前端：`tsc --noEmit` 通过；`vitest run` **25 文件 248 测试全绿**；`vite build` 成功。
- chunk 体积轨迹（主 js，min/gzip）：P0 后 438.36/135.97 → P1-1 后 453.22/141.89 → P1-2 后 464.25/145.06 → 终态 589.25/**186.88**。P1 全部原语 + lucide 增量 ≈ **51KB gzip**，低于 80KB 预算（计划 §Dependencies）。
- 四宽度回归（CDP headless，`?w=ollama-verify` 工作区）：
  - 1280：导航单行（navH 33.5，六按钮 h=34）、lucide svg 存在、alert-dialog 标题正确、账本 Sheet w=320、账本 Tabs count=10 且 active 可切换、对话 Sheet Esc 后 `data-state=closed`、布局不变量成立、console 无真实错误；
  - 1200/1024：内联导航隐藏、`页面导航` 下拉可见；
  - 480/790：此前 P1 各步已过（Sheet/Select/Tabs/Popover/表单逐项走查）。
- 后端四门（确认零污染）：`uv run pytest` **641 passed**、`ruff check` 通过、`ruff format --check` 170 files formatted、`mypy` 78 files no issues。
- 走查产物：截图与脚本在 `%TEMP%\hastings-visual\`（运行产物，不入库）。

## 7. 已知留白（转后续）

1. **SettingsPanel 表单族迁移**（password/Select/Switch）随 ollama WIP 合入后再做；届时 `ui/switch.tsx`、`ui/label.tsx` 需补建（依赖已装好）。
2. **ScrollArea 未做**（决策见 §2）；若后续要做，目标容器 ChatStream/事件流。
3. **BiblePanel ⤢ 展开按钮**可换 Maximize2 图标（低优先）。
4. **语义标记字符（◈✎▸▾●✓）保留**：这些是领域语义符号（账本类型/埋设回收标记），不是通用操作图标；ThinkingCard 测试锁定文案一并保留。迁移范围收口为「导航与操作按钮」。
5. 主 chunk 589kB 触发 Vite 500kB 警告：RelationGraph 已懒加载，后续可用 manualChunks 拆 Radix/lucide vendor chunk（非本改造目标）。

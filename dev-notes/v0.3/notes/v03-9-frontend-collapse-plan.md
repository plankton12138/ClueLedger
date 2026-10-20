# 前端收拢开发计划：稿纸中心桌面

> 性质：开发计划（已实施并归档为历史记录）。日期：2026-10-19；实施：V03-9，构建记录见 [v03-9-frontend-collapse.md](v03-9-frontend-collapse.md)。
> 依据：2026-10-19 作者四项裁决（见 §1）与[作家的桌面 §5「通过交互让作者感知设计理念」](../../../product-design/writers-desk-design.md)。
> 关联：[0.3 完整开发计划](../development-plan.md)（已收口）、[模块说明](../modules/README.md)（当前实现）。
> 批次号：V03-9（写作台收拢），已实施。

## 1. 背景与裁决

### 1.1 现状问题

当前前端与「一张稿纸，几本书，一支笔」的设计基准有明显差距，一屏内同时存在四层框架与大量开发痕迹（证据：`frontend/src/App.tsx`、`frontend/src/components/desk/DeskShell.tsx`、走查截图 `artifacts/visual-walkthrough-8.5/01-desk-1440.png`）：

- **四层框架**：顶栏 8 个控件（logo、项目选择器、搜索、连接状态、头像、模型、对话开关、设置）+ 导航栏 7 项 + 主区 + 底部状态栏。
- **旧版本页面占导航**：项目概览、创作台、故事账本、变更影响、修订审阅、悬疑审计六项是 V1/V2 入口，V03-8.6 已改为只读但未退出导航；其中「创作台」（StudioPage）与 0.3「写作台」（DeskShell）职责重复。
- **开发痕迹外露**：项目选择器显示 `prj_xxx` 原始 ID；「临时视图」条目标注 `(V03-6)` `(V03-2)` 阶段编号；状态栏显示 commit hash（版本 / 章基础）；文案含「本阶段只开单章修改」等过程措辞。
- **右栏四个面板同时堆叠**：账本、关联图谱、后续状态、操作结果；「操作结果」空面板常驻，违反「空列表不占一张卡片」。
- **行为偏差**：正文 textarea 随时可编辑且有「保存正文」按钮，绕过了设计要求的「默认阅读 → 手工修改进副本」支线。

### 1.2 2026-10-19 作者裁决

1. **旧版本页面退出产品**：六个旧页面从导航撤下，产品中**不保留**「旧版数据回看」入口；后端只读接口与数据不动。
2. **首章试写前端入口暂时撤掉**：移除「试写」「试写第一章」按钮与 DraftDialog；后端 `/drafts` 起草能力保留，后续可恢复入口。对话中由作者自然语言发起创作、候选采用的路径保留。
3. **默认桌面只留稿纸和笔**：右栏默认整体收起，账本/图谱/后续状态/操作结果有内容时才以边缘标记提示，点开为抽屉；左栏只留紧凑章节目录，想法与讨论默认折叠；删除「临时视图」区块。
4. **模块布局收紧留白与间隙**：模块排布参考 [dark 原型](../../../prototypes/dark/README.md) 的空间利用方式——模块铺满可用视口、外围空白与模块间隙压到最小、内容以细分隔线组织，不靠大留白分隔。这与[作家的桌面 §3「模块化布局与空间利用」](../../../product-design/writers-desk-design.md) 已定稿的原则一致，本次落实到具体间距。

### 1.3 设计原则（重申，作为验收尺子）

不用文字把设计理念告诉用户，用交互让用户感知：作者作出明确创作决定后 Agent 才开始任务；试写候选经采用才进入正文。本计划的所有改动同时要求**不在产品中留下开发记录**——界面不出现阶段编号、内部 ID、commit hash、过程措辞，也不为开发过渡目的增设回看入口。

## 2. 目标与非目标

**目标**

- 写作台成为唯一主视图：无导航栏、无状态栏，顶栏只保留作品名、模型、设置。
- 稿纸默认只读；「手工修改」是唯一进入编辑的方式，修改中才有完成/暂存/放弃。
- 右栏默认收起，以边缘标记 + 抽屉按需展开；左栏只留章节目录与默认折叠的想法列表。
- 模块布局收紧：外围空白与模块间隙压到最小，面板铺满可用视口（dark 原型的空间利用方式，细节见 M8）。
- 撤掉首章试写前端入口；候选采用链路在写作台讨论区内闭环。
- 清除界面上的全部开发痕迹。
- 删除导航撤下后无引用的旧前端代码，测试同步收敛。

**非目标**

- 不改后端任何接口、数据与 410 拒启面；不改存储。
- 不改视觉基调（老 iOS 控件风格、纸面、字号令牌不动）。
- 不实现新功能；执行日志前端面板等既有留白不并入本批次。
- 产品文档（product-design/）的裁决登记在实施后另做，不在本批次改动产品方案文字。

## 3. 现状盘点与处置总表

### 3.1 App 外壳（`frontend/src/App.tsx`，371 行）

| 现有元素 | 行号 | 处置 |
|---|---|---|
| `nav` 数组（7 项）与导航栏渲染 | 43–53, 232–254 | 删除；`view` 状态一并移除，直接渲染 DeskShell |
| 五个旧页面 import 与分支渲染 | 26–30, 285–302 | 删除 import；候选审阅（review 分支）移入写作台（见 §4） |
| 顶栏搜索框 + SearchResults | 168–199 | 删除（账本/章节定位由写作台内导航承担） |
| 连接状态点、头像、ChatToggle | 200–223 | 删除；连接异常由输入框/讨论区原位报错承担 |
| 底部状态栏（hash、保存状态、对话状态） | 322–358 | 删除；保存状态保留在正文头部标记 |
| ChatSidebar 挂载 | 305–320 | 移除；写作台已有 DeskComposer/DeskDiscussion |
| 项目选择器显示 `prj_xxx` | 144–166 | 显示作品名（`currentProjectName` 已有），ID 不上界面 |
| `useV2Events` 订阅 | 81 | 保留订阅（实时刷新），仅删除状态点 UI |

### 3.2 写作台（`frontend/src/components/desk/DeskShell.tsx`，941 行）

| 现有元素 | 行号 | 处置 |
|---|---|---|
| 正文 textarea 随时可编辑 + 「保存正文」 | 600–613, 731–739 | 默认 `readOnly`；删「保存正文」；仅手工修改会话内可编辑 |
| 「整体调整」头部按钮 | 621–635 | 从头部撤下；入口并入右栏标记抽屉（有任务才出现标记） |
| 「专注写作」开关 | 688–698 | 删除（右栏默认已收起，左栏窄屏已是抽屉，该开关失去意义） |
| 左栏「临时视图」区块（含 V03 阶段标签） | 50–53, 517–554 | 删除；图谱/历史入口移到正文头部与右栏标记 |
| 左栏「想法与讨论」 | 465–516 | 保留，默认折叠为区块头 + 条数，点击展开 |
| 章节目录头部「试写」按钮、空态「试写第一章」 | 413–425, 436–446 | 删除；空态文案改为「还没有正文。可以导入 .txt / .md 稿件，或在下方告诉 Agent 想写什么。」 |
| DraftDialog 挂载 | 38, 298, 905–919 | 删除引用；组件文件随 M6 删除 |
| 右栏四面板常驻 | 816–877 | 默认收起；改为边缘标记 + 抽屉（见 §5 M3） |
| 「本阶段只开单章修改」等过程措辞 | 419, 429 | 清除，改为行为描述或省略 |

### 3.3 旧代码引用关系（已用 grep 核实）

**随页面删除的组件**（删除 pages/* 后无引用方）：

- `pages/OverviewPage.tsx`、`pages/StudioPage.tsx`（连带 `AgentTasks.tsx`）、`pages/LedgerPage.tsx`、`pages/ChangesPage.tsx`、`pages/AuditPage.tsx`
- 已无任何引用方的遗留组件：`ArticlesPage.tsx`、`CorpusPage.tsx`、`AgentWorkbench.tsx`、`BiblePanel.tsx`、`ManuscriptPanel.tsx`、`ChatHeader.tsx`、`ChatStream.tsx`（连带 `SyncCard.tsx`、`ThinkingCard.tsx`）、`SearchBox.tsx`、`SettingsPanel.tsx`、`EventStreamView.tsx`、`Histogram.tsx`、`RingStat.tsx`、`components/AuditPage.tsx`、`components/LedgerPage.tsx`、`SearchResults.tsx`
- ChatSidebar 移除后无引用方：`chat/ChatMessages.tsx`、`chat/ChatComposer.tsx`、`chat/IntentNote.tsx`、`chat/refs.ts`、`chat/ChatMarkdown.tsx`（实施时以 grep 复核）
- `desk/DraftDialog.tsx`；`api/drafting.ts` 仅服务 DraftDialog（候选采用走 `ChatCandidateReview` → `api/v2.ts` 的 job 接口，不经 drafting.ts），一并删除
- 连带测试：`CorpusPage.test.ts`、`AuditPage.test.ts`、`RingStat.test.ts`、`SessionSelector.test.ts`、`ChatCards.test.tsx`（候选卡若复用则保留并改写）

**必须保留**：`Workbench.tsx`（desk 各组件在用 `ConfirmDiscard/Empty/ErrorText/Pill/Panel/Notice/titleOf/kindLabels`）、`AgentOutputView.tsx`（ChatCandidateReview 依赖）、`chat/ChatCards.tsx` 的候选卡（§4 复用）、`chat/ChatCandidateReview.tsx`（移入写作台）、`SettingsDrawer`/`ModelSettings`/`LocalModelCard`/`ModelButton`、全部 `desk/` 其余组件与 `state/`、`api/` 中被 desk 引用的模块。

**e2e**：`frontend/e2e/` 的 `j1–j8`、`v2-*` spec 多数以旧页面为对象；本批次逐条核对，对象已删的 spec 一并删除，写作台相关场景并入新走查脚本。`walkthrough-v03-8.5.mjs`、`walkthrough-v03-8.7-revision-panel.mjs` 以旧布局为基线，需按新桌面重写。

## 4. 关键设计决定：候选采用链路的新家

撤掉试写按钮与 ChatSidebar 后，「作者在对话中要求创作 → Agent 产出候选 → 作者采用」这条链路必须在写作台内闭环，否则等于删掉了能力而不只是入口。现状链路：候选以 `CandidatePayload` 消息进入会话 → ChatSidebar 用 `ChatCards` 渲染「待审阅候选」卡 → 点击经 App 的 review 视图打开 `ChatCandidateReview`（走 `api/v2` job 采用接口）。

**方案**：链路整体平移进写作台，不改数据流：

1. `DeskDiscussion` 渲染消息时识别候选消息，复用 `ChatCards` 的候选卡组件（不新写卡片）。
2. 点击候选卡打开主区临时视图：`state/desk.ts` 的 `MainView` 增加 `{ kind: "detail", detailKind: "candidate" }`，主区挂载 `ChatCandidateReview`。
3. 采用/放弃后 `navigate` 回该章正文或讨论，操作结果仍进 `deskStore.lastOperationId` 的既有回执通道。

DeskDiscussion 与 ChatSidebar 共享同一 chat store 会话事实源，候选消息 payload 可直接读到；实施第一步先写一个小测试验证 `views[conversationId].messages` 中候选消息的 `payload` 字段完整，再动 UI。

## 5. 分模块改动与代码示例

### M1 App 外壳

只保留：顶栏（作品标记 + 作品选择器 + 模型 + 设置）与 DeskShell。`View` 类型、`pending` 切换确认、`SearchResults`、状态栏全部移除；`isDirty` 的 beforeunload 保护保留。

```tsx
// App.tsx（目标骨架，约 120 行）
export default function App() {
  const s = useV2Store();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [modelsOpen, setModelsOpen] = useState(false);
  useV2Events(); // 保留实时订阅，不显示连接状态点

  useEffect(() => {
    if (!s.currentId) return;
    const state = useV2Store.getState();
    void state.refreshManuscript();
    void state.refreshStory();
    void state.refreshJobs();
  }, [s.currentId]);

  return (
    <div className="flex h-screen flex-col">
      <header className="topbar flex min-h-16 flex-none items-center gap-3 px-5">
        <div className="mark grid h-10 w-10 flex-none place-items-center font-serif text-2xl font-bold">H</div>
        <Select value={s.currentId ?? ""} onValueChange={(v) => {/* 同现有逻辑 */}}>
          {/* SelectItem 显示作品名；选择器宽度按名字自适应 */}
        </Select>
        <div className="flex-1" />
        <ModelButton onClick={() => setModelsOpen(true)} />
        <button aria-label="设置" onClick={() => setSettingsOpen(true)}>…</button>
      </header>
      <main className="min-h-0 flex-1 overflow-hidden">
        <ErrorBoundary key={s.currentId} label="写作台">
          <DeskShell onOpenModels={() => setModelsOpen(true)} />
        </ErrorBoundary>
      </main>
      <SettingsDrawer open={settingsOpen} onOpenChange={setSettingsOpen} />
      <SettingsDrawer open={modelsOpen} onOpenChange={setModelsOpen} modelsOnly />
    </div>
  );
}
```

要点：错误条（`s.error`）保留，它是真实状态不是开发痕迹；切换项目的未保存确认（`ConfirmDiscard`）保留。

### M2 稿纸默认只读 + 头部精简

```tsx
// DeskShell 正文区（目标）
<textarea
  aria-label="正文"
  className="paper-sheet ..."
  readOnly={!edit.active}           // 平时是稿纸，手工修改才是副本
  value={edit.active ? edit.text : chapter.draft}
  onChange={(e) => edit.active && edit.setText(e.target.value)}
/>
```

头部动作收敛为三种状态：

- 阅读态：章名 + 「手工修改」+「历史」；状态标记只在异常时出现（读取失败、有未完成副本）。
- 修改中：「修改中 · 已保存 #n」标记 + 暂存返回 / 完成修改 / 放弃（沿用现有 edit session 逻辑）。
- 删除「保存正文」「整体调整」「专注写作」三个头部按钮；「整体调整」移入右栏标记（见 M3），仅在存在任务或作者在对话中要求后出现。

行为后果（已在评审中告知作者并确认）：随手改一个字需要「手工修改 → 改 → 完成修改」三步，这是手工修改副本设计的既定行为，本次只是让前端回到设计。

### M3 右栏：边缘标记 + 抽屉

右栏默认不渲染面板。稿纸右缘放一列窄标记条，有内容才出现对应标记；点击打开右侧抽屉（复用现有 `railDrawer` 响应式抽屉的样式与 Esc/遮罩关闭逻辑），抽屉内渲染对应面板，关闭后不留痕迹。

```tsx
// DeskShell 内（目标）
type RailPanel = "ledger" | "graph" | "followups" | "results" | "revision";
const [railPanel, setRailPanel] = useState<RailPanel | null>(null);

// 标记条：稿纸右缘，绝对定位，窄条文字按钮
<div className="rail-marks flex flex-col gap-2">
  <button onClick={() => setRailPanel("ledger")}>账本</button>
  <button onClick={() => setRailPanel("graph")}>图谱</button>
  {desk && pendingCount(desk) > 0 && (
    <button onClick={() => setRailPanel("followups")}>待确认 {pendingCount(desk)}</button>
  )}
  {Object.keys(operations).length > 0 && (
    <button onClick={() => setRailPanel("results")}>结果 {Object.keys(operations).length}</button>
  )}
</div>

// 抽屉：沿用 railDrawer 的 fixed/遮罩/Esc 模式，内容按 railPanel 切换
{railPanel && (
  <aside className="rail-drawer" role="dialog" aria-label={railLabel(railPanel)}>
    {railPanel === "ledger" && <LedgerPanel projectId={pid} onSaved={...} />}
    {railPanel === "graph" && <GraphOverview ... onExpand={openGraphView} />}
    {railPanel === "followups" && desk && <DeskFollowupSummary state={desk} />}
    {railPanel === "results" && <OperationResults operations={operations} />}
    {railPanel === "revision" && <RevisionPanel ... />}
  </aside>
)}
```

规则：账本/图谱标记常驻（作者随时可能要看），后续状态/结果/整体调整标记「有内容才出现」——延续「空列表不占一张卡片」。「证据核对」（EvidenceCheck）与当前章绑定，作为正文头部「历史」旁的第三个文字入口，打开后也进抽屉，不占右栏。

### M4 左栏收拢

- 章节目录保留为唯一默认展开区块；头部只留「导入正文」一个文字按钮。
- 「想法与讨论」默认折叠：区块头显示条数（`想法与讨论 · 3`），点击展开列表；展开状态不持久化，每次进入回到折叠。
- 「临时视图」区块删除；「版本历史」入口移到正文头部（M2），「关联图谱」入口移到右缘标记（M3）。
- 宽屏下左栏保留（章节目录是「几本书」），中屏/小屏沿用现有抽屉断点不变。

### M5 撤掉试写入口

- 删除 DeskShell 中「试写」「试写第一章」按钮、空态文案改写（见 §3.2）、DraftDialog 挂载与 import。
- 删除 `desk/DraftDialog.tsx`、`api/drafting.ts` 及其测试（删除前 grep 复核无其他引用方）。
- 后端 `/drafts` 路由、领域服务、存储全部不动；`dev-notes` 既有记录不回改。
- 对话中自然语言发起的创作不受影响，走 §4 的候选卡链路。

### M6 旧代码清理

按 §3.3 清单删除；每删一批跑一次 `npm run build` 与 `vitest`，利用类型检查兜底引用遗漏。删除顺序：pages → 遗留 components → chat 侧栏组件 → DraftDialog/drafting → 孤立测试。`state/store.ts`（旧 store，SyncCard 在用）等共享模块以 grep 结果为准，确认无 desk 引用后删除。

### M7 布局密度收紧（dark 原型空间利用）

现状写作台外圈与模块间隙偏松：左右栏容器 `p-3 space-y-4`（外层 12px 内边距 + 模块间 16px 间隙），面板头 `px-4 py-3`，正文灰底区 `p-4 lg:p-6`。对照 dark 原型（模块铺满视口、细分隔线、间隙最小），逐项收紧：

```tsx
// DeskShell 三栏容器（目标）
<aside className="min-h-0 w-60 space-y-2 overflow-y-auto p-2">   // 左栏：16px→8px 间隙
<section className="panel flex min-h-0 min-w-0 flex-1 flex-col">  // 稿纸：不动行宽，只削外围
<aside className="rail-drawer ...">                              // 抽屉内同样 p-2 space-y-2
```

- 外层容器内边距 `p-3 → p-2`，模块间隙 `space-y-4 → space-y-2`；面板头 `py-3 → py-2`，条目行 `py-3 → py-2`（字号不动，靠间距收敛密度）。
- 正文灰底区 `p-4 lg:p-6 → p-2 lg:p-3`：稿纸行宽仍封顶 `max-w-[44em]` 居中，宽屏多出的空间归稿纸以外的功能模块分配，不把正文拉长，也不留大外围空白。
- 顶栏 `min-h-16 → min-h-12`，控件高度随现有令牌不动；写作台主区 `h-full` 铺满顶栏以下的全部空间。
- 面板之间用既有细边界（`border-line`）相邻排布，不新增阴影/圆角间隙制造「卡片悬浮」感。
- 密度收敛以可读性为界：正文字号 18px、行高 32px、账本与对话字号不缩；窄屏（<1024）断点行为不变。
- 对照基准：V03-8.5 走查实测的各窗口正文区宽度（1440→792px 等）为下限，收紧后正文区只增不减；用同口径复测量。

### M8 测试与走查

- 随组件删除的测试一并删除；`layout.test.ts`、`theme.test.ts`、`state/` 纯函数测试不受影响。
- ChatCards 候选卡复用后，`ChatCards.test.tsx` 改写为针对候选卡的用例。
- 新增 DeskShell 行为测试：正文默认 `readOnly`；手工修改激活后可编辑；空态无「试写」字样；右栏默认不渲染面板。
- 走查：以 `walkthrough-v03-8.5.mjs` 为底本重写四窗口（1440/1280/1024/390）走查，截图归档 `artifacts/visual-walkthrough-9.x/`；j7/j8、v2-* 旧 spec 逐条核对后删除或并入。
- 新增「无开发痕迹」断言（可放进走查脚本）：界面快照中不出现 `(V03-`、`prj_`、commit hash 样式文本、「本阶段」；源码 grep 同步确认。

## 6. 接口与数据影响

- 后端零改动：路由、410 拒启面、存储、事件流全部不动。
- 前端状态：`state/desk.ts` 的 `MainView` 增加 `candidate` detailKind（含 desk.test.ts 补用例）；`deskStore` 不新增持久化字段。
- localStorage：对话栏开关键 `OPEN_KEY` 随 ChatSidebar 移除而废止，读取代码删除；遗留键值无害，不做迁移。
- 无数据迁移；0.2 旧数据仍可由后端只读接口读取，只是产品不再提供界面入口。

## 7. 风险与取舍

1. **正文只读的手感变化**：随手修改需先进手工修改。已告知作者并确认；若日后反馈强烈，可在不改副本机制的前提下加「选中即改」交互，不在本批次。
2. **候选卡依赖会话 payload**：若真实 API 的候选消息字段与模拟器不一致，§4 的验证测试会先暴露；兜底是候选卡在真实服务下不渲染时给出明确错误，而不是静默丢失采用入口。
3. **删除的可逆性**：所有删除都经 git 历史可恢复；后端能力完整保留。本批次结束后在 release.md 登记「前端入口已撤、能力保留」的清单，避免后来者误以为能力缺失。
4. **窄屏回归**：右缘标记条在 390px 宽下改为正文头部溢出菜单，走查脚本覆盖。
5. **测试基线变动大**：预计 vitest 用例数净减（删多于增），属预期；以「保留用例全绿 + 新行为用例通过」为准，不以用例数对齐。

## 8. 验收标准

对照[作家的桌面 §7](../../../product-design/writers-desk-design.md) 逐条核对，其中本批次直接对应：第 1 条（第一眼看到文章与对话入口、无整排业务页面）、第 2 条（默认不展开）、第 3 条（账本按需可看）、第 6 条（不靠图标猜谜换简洁）。批次自身验收：

1. 写作台为唯一视图；无导航栏、无状态栏；顶栏无搜索框/连接状态/头像/对话开关，作品选择器显示作品名。
2. 正文默认只读；手工修改流程（开始/自动保存/暂存/完成/放弃）行为与现状一致。
3. 右栏默认收起；账本/图谱可随时打开抽屉；待确认、结果、整体调整标记只在有内容时出现。
4. 界面无任何「试写」入口；空作品空态只有导入与对话两条路。
5. 对话中发起创作后，候选卡在讨论区出现，可打开审阅并采用，采用后回到正文；模拟器与真实服务各验证一次。
6. 界面快照与源码均无阶段编号、内部 ID、hash、「本阶段」措辞；无「旧版数据回看」入口。
7. `npm run build` 通过；保留的 vitest 用例全绿 + 新行为用例通过；四窗口走查脚本通过并归档截图。
8. 布局密度：四窗口正文区宽度不低于 V03-8.5 实测值，页面无大外围空白、模块间隙按 M7 令牌收敛；字号与行高不回退。
9. 后端测试全量不回归（本批次不动后端，跑一遍确认即可）。

## 9. 文档同步清单（已实施收尾时完成）

- [product-design/README.md](../../../product-design/README.md) 范围决定表：补 2026-10-19 四行裁决（旧页面退出产品不设回看、首章试写前端入口暂时撤掉、默认桌面只留稿纸和笔、模块布局收紧留白与间隙）。
- [modules/desk-surface.md](../modules/desk-surface.md)：改写为收拢后的当前行为（只读稿纸、右栏抽屉、候选卡链路）。
- [release.md](../release.md)：登记入口撤除与能力保留清单、新走查证据。
- 本文件已移入 `notes/` 归档，导航相应更新（见 [v03-9-frontend-collapse.md](v03-9-frontend-collapse.md)）。

## 10. 实施顺序

1. M5 撤试写入口（最小闭环，先验证构建与测试机制顺畅）
2. M2 稿纸只读 + 头部精简
3. M4 左栏收拢
4. M3 右栏标记 + 抽屉
5. M1 App 外壳（去导航后 ChatSidebar 与旧页面失去入口）
6. M7 布局密度收紧（在结构定型后统一调间距，避免反复挪动）
7. §4 候选卡平移 + M6 代码清理（交替进行，每批一验证）
8. M8 测试/走查收敛（含正文区宽度复测）→ §8 验收 → §9 文档同步

每步提交独立 commit；任一步验收不过不回写设计文档。

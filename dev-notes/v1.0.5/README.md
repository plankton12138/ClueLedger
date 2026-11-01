# 1.0.5 三模式工作区与构思/正文双向联动（定向实现完成）

运行版本 **1.0.5**。本版完成三类改动：工作区收成正文/构思/模块库三模式、线索详情统一迁到左栏、候选正文提交后登记线索观察。悬疑视图只保留时间轴与选择，不再承担第二份线索详情。

## 已实施

### 三模式顶栏

- `AppHeader` 顶栏改为「正文 / 构思 / 模块库」三个入口；活动记录移到全局工具区。
- 正文与构思共用 `writing` 路由，构思使用 `?mode=story` 写入 URL，可刷新、前进后退和直接链接。
- 模块库返回时回到进入前的正文或构思；每个作品记住最近位置。
- 工作区内的全局二级栏从六个按钮缩为「侧栏 / 对话」两个开关。

### 左侧资料栏

- 左栏增加「目录 / 本章线索 / 全书线索」标签；构思模式只有目录与全书线索。
- 构思页删除底部线索列表与详情；`StoryMysteryView` 只保留阶段分组、泳道收起和线索选择。
- 右栏删除「对话 / 全书线索」标签，只保留对话。
- 构思、正文全书线索共用 `story.clueId`；选时间轴线索会打开左栏全书线索。
- 线索详情在左栏模式下临时加宽到 320px；目录模式沿用保存栏宽。

### 跨模式状态

- 构思里点「写这一段」切到正文模式后发起写作轮。
- 构思里的原文依据切到正文模式显示来源，并提供「返回构思」。
- 线索证据深链进入正文来源子状态，不再被页面清理逻辑误删。
- 灵感「带入对话」回到最近一次正文/构思模式并打开右栏。
- 顶栏新增「生成中」，用于从模块库等位置回到正在生成的对话。
- 正文和构思共用同一段对话、草稿和运行任务，切模式不重置。

### 前序联动

- `ClueMode` 增加 `action`，修复全书线索列表/详情 500。
- 构思页明确「写第一章 / 写正文」时升级为写作轮；节点详情提供「写这一段」。
- 新增 `story_clue_observations.py`：候选提交后把正文中实际出现的线索原句登记为账本观察；后续章登记为提及，失败不回滚正文。
- 全书线索显示仅构思 / 正文未登记 / 正文待复核 / 已入正文；无正文时说明「还只在构思里」。
- 对话「添加文件」只列目录中真实存在的正文、角色小传与固定设定；展开时重读一次目录，缺失的固定设定不再占位。
- 结构视图独立错误边界与动态加载重试；画布使用 `useReactFlow().getNodesBounds`。

## 主要文件

- `src/hastings/workspace/clue_models.py`
- `src/hastings/workspace/service.py`
- `src/hastings/workspace/agent.py`
- `src/hastings/workspace/story_clue_observations.py`
- `src/hastings/prompts/authoring.py`
- `frontend/src/state/navigation.ts`
- `frontend/src/components/workspace/AppHeader.tsx`
- `frontend/src/components/workspace/WritingLayout.tsx`
- `frontend/src/components/story/StoryMysteryView.tsx`
- `frontend/src/components/story/StoryClues.tsx`
- `frontend/src/components/clues/SourceReader.tsx`
- `frontend/src/components/ideas/IdeasSurface.tsx`
- `frontend/src/state/layout.ts`
- `frontend/src/index.css`

## 验证

- 后端定向与扩展：线索 action、写作路由升级、候选观察、故事线索视图共 115+ 项通过。
- 前端：30 个测试文件、339 项单测通过；`tsc --noEmit` 与 `npm run build` 通过。
- 浏览器（8010 验证实例，1680×1050）：三模式切换、`?mode=story` 深链刷新、构思左栏全书线索、正文左栏本章/全书范围、模块库与返回、共用线索选择、无底部线索区、无右栏线索标签；控制台无 error。
- 未执行：在线模型从构思生成第一章的端到端验收；从正文反向整理验收；模块库新建表单未保存状态的模式切换确认。旧数据不迁移。

## 已知边界

- 未选择节点直接写正文时，模型只拿到方案概览；优先从节点详情「写这一段」进入。
- 模块库不常驻对话；切换到正文/构思依靠顶栏和最近模式。
- 左栏线索在 320px 下长文仍会滚动；可继续拖宽到 520px。
- 模块库新建表单的未保存内容在切模式时没有确认拦截，后续需要补草稿状态。

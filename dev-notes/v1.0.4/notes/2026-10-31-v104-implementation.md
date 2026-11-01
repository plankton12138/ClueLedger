# 2026-10-31 v1.0.4 实施记录

版本：1.0.4。范围：悬疑视图、只读悬疑投影、结构诊断、创建 brief。实现完成后补记；完整发布验收与在线生成验收尚未执行。

界面截图：[悬疑视图（最新真实记录）](../artifacts/mystery-view-20261031.png)。

## 已完成

### 后端

- 新增 `src/hastings/workspace/story_mystery.py`：
  - `event_times` 拓扑排序；未登记节点按阶段顺序、`reveal_order`、ID 追加。
  - 线索按 `clue_plans` + `clue_uses` + 线索账本聚合，保留计划-only、使用-only、无身份三种情况。
  - 节点返回线索使用、`knowledge_changes`、前后实际时间关系；主体按 `subject_id` 聚合。
  - 只读，不写状态，不调用模型。
- 扩展 `src/hastings/workspace/story_structure.py`：
  - `STORY_MAIN_CHAIN_LONG`：一条主链加旁支也能被识别，不再只检查“恰好一条链”。
  - `STORY_TIMELINE_EMPTY / PARTIAL / CYCLE`。
  - `STORY_CLUE_LIFECYCLE_INCOMPLETE / READING_MISSING / TRUTH_MISSING / UNUSED`。
  - `STORY_KNOWLEDGE_MISSING`：有线索使用点但完全没有角色知情。
  - 诊断支持可读线索标题；现有 `structure_issues` 仍使用既有提交路径。
- `src/hastings/workspace/story_api.py` 新增只读接口：
  `GET /api/file-workspaces/{workspace_id}/story/variants/{variant_id}/mystery`。
- 新增/扩展测试：
  - `tests/workspace/test_story_mystery.py`：顺序、阶段回退、循环、线索聚合、主体聚合、诊断。
  - `tests/workspace/test_story_api.py`：投影接口、缺失方案。

### 前端

- `frontend/src/api/story.ts`：新增悬疑投影类型与 `readStoryMystery()`。
- 新增 `frontend/src/components/story/mysteryLayout.ts`：顺序、阶段过滤、角色标签、知情聚合、读者读法、线索生命周期等纯函数。
- 新增 `frontend/src/components/story/StoryMysteryView.tsx`：
  - 横向实际时间轴，左侧固定泳道标签。
  - “实际发生 / 侦探与助手 / 读者 / 线索”四行。
  - 线索使用点可选中；下方线索列表与详情显示表面呈现、作者真相、想引导、相关知情、生命周期和线索诊断。
  - 节点链接可切到结构视图并选中节点。
  - 结构诊断默认折叠，显示全局数量。
- `StorySurface.tsx`：增加“悬疑视图 / 结构视图”切换，默认悬疑；阶段筛选沿用；修复 `StoryDraft` 与 `StoryBatches` 同级 key 重复。
- `frontend/src/index.css`：新增悬疑视图样式，窄屏底部堆叠，时间轴内部滚动，不造成整页横向溢出。
- 新增 `frontend/src/components/story/mysteryLayout.test.ts`。

### 创建契约

- `StoryCreationDialog.tsx`：空对话默认填入悬疑 brief；从对话创建时提示实际时间线、线索生命周期、侦探/助手知情。
- `src/hastings/prompts/authoring.py`：补充实际时间、`next` / `reframe`、线索生命周期、读者读法、知情记录和非单链要求。
- `src/hastings/prompts/tools.py`：`update_story` 说明补充 `set_event_time`、线索角色与读者读法要求。

### 补充修复：插话消息不回显

- 现象：生成中点击「插话」后，后端已把作者要求写成 `author_interjected` 事件，但前端的事件联合类型和 `appendEvent` 没有处理它，所以对话里看不到这条作者消息，只看到旧回复停止和新的“思考中”。
- 修复：`api/workspace.ts` 增加 `author_interjected` 事件；`state/chatMessages.ts` 增加作者消息项，按事件顺序排在旧回复之后；`ChatTurnView` 用与原消息相同的气泡渲染，并保留轮次定位属性。
- 回归：`chatMessages.test.ts` 覆盖插话位置、原 prompt 不变、重复事件不重复显示；前端全量单测 338 passed，构建通过。
- 未在浏览器里重放真实插话会话；该用例由同一套 `appendEvent` 回放与实时接收共用。

### 版本与文档

- `pyproject.toml`、`src/hastings/__init__.py`、`frontend/package.json`、`frontend/package-lock.json`、`uv.lock`：1.0.4。
- 新增 `dev-notes/v1.0.4/`、`product-design/v1.0.4/`，更新 `dev-notes/README.md` 索引。

## 验证结果

- 后端目标套件：
  `uv run pytest tests/workspace/test_story_mystery.py tests/workspace/test_story_api.py tests/workspace/test_story_tools.py tests/workspace/test_story_remediation.py tests/workspace/test_story_contracts.py -q`
  → **91 passed**。
- 前端全量单测：`npm test` → **338 passed / 30 files**。
- 前端构建：`npm run build` → `tsc --noEmit` 与 Vite 构建通过；仅有既有的大 chunk 警告。
- 浏览器专项（最新真实记录 `未命名作品4`，只读）：
  - 悬疑视图打开：时间 **6/20**，线索 **17**，诊断 **7**。
  - 诊断包含：主链过长、实际时间部分覆盖、缺少角色知情、线索只有埋设/推进、三条误导线索缺少读者读法。
  - 线索点击、诊断“查看线索”、节点跳结构视图并选中、结构/悬疑切换均通过。
  - 820px 窄屏：无整页横向溢出；时间轴内部滚动，底部列表与详情堆叠。
  - 浏览器控制台无 error，React 同级 key 警告已消除。
- 最新记录的实际状态：6 个节点登记实际时间，14 个按阶段回退；没有任何 `knowledge_changes`，所以“侦探/助手”层显示“未登记”，并有对应诊断。这不是渲染错误，是当前构思内容缺项。

## 未完成

- 未执行完整后端全量测试和完整发布验收；只跑了 story 相关目标套件。
- 未执行强模型在线生成验收；创建 brief 和提示已改，但本轮没有新建在线构思。
- 未实现全局跨视图选中（右侧“全书线索”与悬疑视图的选中状态尚未共享）。
- 未实现独立线索矩阵、自动读者模拟器、公平性校验或 `Event / Reveal / Clue / Hypothesis` 重构。
- 未解析 `knowledge_changes.subject_id` 的角色显示名；当前直接显示 ID。

## 已知限制

- 20 节点记录在时间轴中仍需要纵向滚动才能看到全部四行；这是内容密度造成的，不是结构错误。
- 线索详情与右侧“全书线索”仍是两个入口；本版只在悬疑视图内部完成线索选择与详情。
- 诊断只提示，不阻止作者保留单线或开放结局。

## 下一步

1. 用强模型按新 brief 创建一个真实构思，检查 `event_times` 覆盖、线索生命周期和角色知情是否实际改善。
2. 若仍单链，实施 `design_truth / design_clues / design_reveals / design_narrative` 分阶段工具。
3. 再决定是否把 `Clue` / `Hypothesis` / `ReaderBelief` 提升为独立对象。

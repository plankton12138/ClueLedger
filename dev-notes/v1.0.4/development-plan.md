# 1.0.4 开发计划：悬疑视图与构思诊断

## 基线

- 运行版本：1.0.3。
- 真实记录：`未命名作品4`，20 个节点、6 条故事线、21 条关系、17 条线索、4 条实际时间关系。
- 已有能力：`StoryVariant` 已区分 `next` / `reframe` / `answer` 与 `event_times`；`StoryNode` 已有 `clue_uses`、`knowledge_changes`、`time_note`；`StoryCluePlan` 已有 `author_truth`、`intention`、`resolution_intent`。
- 现有缺口：投影接口只给节点/推进边；线索不在画布上；创建入口没有悬疑 brief；单链诊断只看“恰好一条链”，一条主链加一个旁支就会漏掉。

## 设计取舍

### 1. 实际时间作为横轴，叙述顺序留在结构视图

`event_times` 是故事内实际先后，`next` 是揭示依赖，两者本来就不是一回事。悬疑视图以 `event_times` 的拓扑顺序为横轴；未登记实际时间的节点按阶段顺序、`reveal_order` 和 ID 追加，并明确标注“时间未登记”。结构视图继续按 `next` / 阶段展示，不把两套顺序混成一张图。

取舍：实际时间登记不完整时，横轴会产生“已登记段 + 未登记段”。这是有意暴露缺口，不用 `reveal_order` 伪装成实际时间。

### 2. 线索是投影出来的一等对象，不复制真相

悬疑视图的线索来自 `clue_uses` 与 `clue_plans` 的联合投影：标题来自线索账本，呈现与角色来自节点，作者真相与回收意图来自计划。投影不新增第二份真相，也不把角色的说法写成作者真相。

取舍：线索仍然是“挂在节点上的使用”，不是独立写入对象。这样不用重写提交协议，但一条线索的“生命周期”是多个使用点聚合出来的，而不是单独编辑的实体。

### 3. 诊断是提示，不是硬拦截

新增诊断只描述可判定的缺项：主链过长、实际时间覆盖不足、线索没有回收、误导线索没有写读者读法。它们进入提交结果的 `structure_issues`，并显示在悬疑视图顶部；不阻止作者保留有意的模糊、开放结局或单线作品。

取舍：诊断不能保证模型一定改。它把问题从“画布看起来像一条线”变成模型和作者都能读到的具体缺项；强制拦截留到后续版本，避免把正常创作卡死。

### 4. 创建入口先给“谜题 brief”，再给章纲

创建构思的默认文本改为：谜题与场景、真相与实际时间线、嫌疑人与秘密、关键线索（表面读法 / 作者真相 / 埋设 / 回收）、误导与两个结论、侦探与助手各自知道什么。系统提示同时强调：先登记实际时间和线索生命周期，再写节点与推进关系。

取舍：这仍是一次生成，不是分阶段工具链；它能降低“章纲化”的概率，但不能替代后续的 `design_truth / design_clues / design_reveals` 工具。

### 5. 只读投影，不新增写入路径

本版新增的 `GET /story/variants/{variant_id}/mystery` 只读；作者的修改仍走现有 `update_story` / 结构变更接口。这样不会产生第二套写入协议，也不会让界面绕过修订与授权。

## 工作包

### P1 悬疑投影与诊断

- 新增 `src/hastings/workspace/story_mystery.py`：纯函数投影，输入 `StoryVariant` 与 `StoryClueCatalog`，输出顺序、节点附加信息、线索聚合、诊断和统计。
- 扩展 `src/hastings/workspace/story_structure.py`：保留现有 `STORY_THREAD_INCOMPLETE`、`STORY_SINGLE_CHAIN`，新增主链长度、实际时间覆盖、线索生命周期和读者读法诊断。
- 新增只读接口 `GET /api/file-workspaces/{workspace_id}/story/variants/{variant_id}/mystery`，返回投影。
- 测试：顺序与回退、循环时间关系、线索聚合、诊断码、接口响应。

### P2 悬疑视图

- `frontend/src/api/story.ts` 增加 `StoryMystery` 类型与 `readStoryMystery()`。
- 新增 `frontend/src/components/story/StoryMysteryView.tsx`：
  - 顶部统计与诊断条。
  - 横向滚动的实际时间轴，左侧固定泳道标签，节点列按实际顺序排列。
  - “实际发生 / 侦探与助手 / 读者”三层内容；线索使用点可点击。
  - 下方线索列表 + 线索详情：表面呈现、作者真相、想引导、角色知情、生命周期、诊断。
  - 点击线索使用点或节点可切到结构视图并选中对应节点。
- `StorySurface.tsx` 增加“悬疑 / 结构”切换，默认悬疑；阶段筛选沿用。
- `frontend/src/index.css` 增加悬疑视图样式；移动端横向滚动、底部详情堆叠。
- 测试：纯排序/分组函数、服务端渲染的静态结构、现有结构视图回归。

### P3 创建契约

- `frontend/src/components/story/StoryCreationDialog.tsx`：默认文本和 placeholder 改为悬疑 brief。
- `src/hastings/prompts/authoring.py`：在构思任务中加入实际时间、线索生命周期、读者读法和 `next` / `reframe` 的职责说明。
- `src/hastings/prompts/tools.py`：更新 `UPDATE_STORY_DESCRIPTION`，要求 `event_times` 覆盖实际时间、误导线索填写 `expected_interpretation`、关键线索有回收或重新解释。
- 不新增工具；本轮仍用现有 `update_story`。

### P4 测试与交付

- 后端：新增/扩展 `tests/workspace/test_story_mystery.py`、`test_story_remediation.py`、`test_story_api.py`。
- 前端：新增 `frontend/src/components/story/mysteryLayout.test.ts`，补充 `StorySurface` 的视图切换测试（能静态渲染即可）。
- 版本：`pyproject.toml`、`src/hastings/__init__.py`、`frontend/package.json`、`frontend/package-lock.json` 升到 1.0.4。
- 文档：本目录、`dev-notes/README.md` 索引；补一份 `notes/2026-10-31-v104-implementation.md`，记录实际完成、未完成和验证结果。

## 完成条件

- 悬疑视图能在现有 20 节点记录上打开，按实际时间展示已登记节点，并按叙述顺序追加未登记节点；不伪造时间。
- 每条线索能在时间轴上找到使用点，详情能分开显示“表面呈现 / 想引导 / 作者真相 / 角色知情 / 生命周期”。
- 诊断能指出最新记录的主链过长、实际时间覆盖不足、线索只有埋设没有回收等问题；不阻断现有提交。
- 结构视图、阶段筛选、节点详情、撤销与提交不回归。
- 后端目标测试、前端类型检查与目标测试通过；浏览器专项在真实记录或固定夹具上通过。
- 版本号、开发文档和验收记录一致。

## 风险

- **实际时间稀疏**：最新记录只有 4 条 `event_times`，悬疑视图会出现大量“未登记”。这是提示，不是渲染错误；诊断会明确数量。
- **主体名不可读**：`knowledge_changes.subject_id` 目前是角色 ID，本版直接显示 ID，不解析角色名；后续可在模块/角色账本里补显示名。
- **横向宽度**：20 个节点约 3—4 千像素宽，需要横向滚动；本版不做缩放，先保证顺序和线索可读。
- **模型不一定听提示**：P3 只改提示和默认文本，不保证模型不再写单链；诊断和悬疑视图先让问题可见，强制工具链留给后续版本。
- **诊断误报**：开放结局、单线作品可能被提示；诊断只做提示，作者可以保留原结构。

## 与后续版本的关系

本版验证“视图 + 诊断 + brief”是否足以让作者看懂并让模型少写单链。若仍不足，下一版再做：

1. `design_truth / design_clues / design_reveals / design_narrative` 分阶段工具；
2. 自动读者模拟器与公平性校验；
3. 把 `Clue` / `Hypothesis` / `ReaderBelief` 提升为独立对象。

在这些之前，不重写现有故事模型。

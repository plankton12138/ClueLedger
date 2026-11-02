# P5 实施记录：Aqua 视觉与旧链路清理

日期：2026-11-02。状态：**已实施（P5）**。版本仍为 1.0.8；P6 验收与发布未开始。

## 1. 实际改动

### 1.1 Aqua 材质与控件

| 文件 | 改动 |
|---|---|
| `frontend/src/index.css` | 新增 `--aqua-*`、`--paper*`、`--focus-ring` 等全局 token；按钮、输入、对话框、顶栏、写作工具栏、稿纸工具栏、导航选中统一从 token 取材质 |
| 同文件 | 主按钮使用蓝色三段渐变；普通按钮补齐默认、悬停、按下内陷、键盘焦点、禁用与忙碌状态；输入框统一内凹与聚焦外环 |
| 同文件 | 对话框标题区使用 chrome token，内容区回到平整纸色；编辑器工具条移到稿纸上方 chrome；正文保持浅纸色、深灰字与衬线行高 |
| 同文件 | 增加 `prefers-reduced-motion` 规则；删除旧构思页、时间轴、线索页、旧资料页与旧正文标记样式 |

### 1.2 旧页面、状态与接口撤除

| 位置 | 改动 |
|---|---|
| `frontend/src/components/story/` | 删除 `StorySurface`、`StoryTimeline`、`StoryBatches`、`StoryDraft`、时间轴定位工具与旧测试 |
| `frontend/src/components/clues/ClueSurface.tsx` | 删除旧线索页 |
| `frontend/src/components/workspace/` | 删除 `WorkspaceDetail`、`ObjectReadDetail`、`ChapterContext`、旧 `WorkspaceToc` 与线索标注组件、对应测试 |
| `frontend/src/components/workspace/Paper.tsx` | 简化为只读稿纸：只保留来源高亮、选段讨论、翻页、字号与文件信息；不再读取旧构思出处、线索下划线或正文标记 |
| `frontend/src/components/workspace/WorkspaceFileTree.tsx` | 新增模块库左侧文件树，替代旧目录组件；保留搜索、导入、正文与设定人物 |
| `frontend/src/components/writing/WritingNavigator.tsx` | 补回作品挂载模块列表，避免写作页失去模块入口；导航名称改为「作品目录」 |
| `frontend/src/api/story.ts` | 只保留改动记录读取与撤销；删除方案图、时间轴、同步、正文标记与本章聚合客户端 |
| `frontend/src/api/clues.ts` | 只保留来源定位；删除线索列表、详情、标注与关系客户端 |
| `frontend/src/state/story.ts`、`state/clues.ts`、`state/taskPurpose.ts` | 删除旧读模型与用途判断 |
| `frontend/src/state/conversations.ts` | 不再向对话附加旧构思面上下文；结构写入后由工作台统一重读 |
| `src/hastings/workspace/story_api.py` | 撤除方案图、时间轴、同步、节点详情、悬疑投影、正文标记、本章聚合、任务草稿与通用结构提交路由；保留写作工作台、开始/保存事务、改动读取与撤销 |
| `src/hastings/workspace/clue_api.py` | 只保留来源定位路由；撤除线索列表、详情、标注、关系与新建身份路由 |
| `src/hastings/workspace/` | 删除只服务已撤除路由的 `story_mystery.py`、`story_prose_marks.py`、`story_chapter_view.py`、`clue_marks.py`、`clue_relations.py`、`clue_api_models.py`、`story_timeline.py` |
| 后端测试 | 删除或改写旧路由测试；聊天写入测试改用受管设定文件，继续覆盖轮次、Git、停止、检查、任务范围与参考完整性；发布恢复测试直接核对账本 |

保留：`StoryStore` 提交/撤销、章节写入门槛、工作台投影、候选、索引、来源定位、`build_attention_view`、`StoryDraftStore`、线索视图与模块装配等仍被 Agent、提交卡片或上下文装配使用的领域服务。

### 1.3 浏览器测试清理

删除旧页面与旧入口专用用例：`chapter-context`、`clue-reference`、`clue-without-story`、`clueledger-workspace`、`core-pages`、`frontend-experience`、`large-workspace`、`story-overview-removal`、`story-structure`、`workspace`、`clue-source-navigation`、`text-selection`、`frontend-foundation`。

重写 `aqua-layout.spec.ts`：覆盖 1440、1024、760、640、390 与 200% 等效宽度下的写作页，检查 chrome 与稿纸分层、按钮悬停/焦点/禁用、对话框标题材质、减少动态效果、页边索引开合与无水平滚动。截图输出到 `dev-notes/v1.0.9/artifacts/aqua/`。

## 2. 验证

| 命令 | 结果 |
|---|---|
| `npm run build`（frontend） | tsc 与 Vite 构建通过 |
| `npm test`（frontend） | 29 个文件、324 个用例通过 |
| `.\.venv\Scripts\python.exe -m pytest tests/workspace -q` | 1454 passed |
| `npx playwright test --project=workspace`（保留的 9 个确定性用例文件） | 37 passed |
| `ruff check`（本次后端改动与测试） | All checks passed |

在线模型、P6 完整验收矩阵与版本统一尚未执行。

## 3. 消费者结论

- 旧构思页、线索页、本章相关页与旧目录不再是日常入口；其前端组件、状态、样式和专用 HTTP 路由一并撤除。
- 对话里的提交卡片仍需读取改动记录并撤销，因此 `/story/changes/{id}` 与 `/story/changes/{id}/undo` 保留。
- 来源定位仍从对话与检查报告进入，因此 `/source-locations/{id}` 保留；线索账本、披露、时间轴等作为 Agent 上下文服务保留，不因 HTTP 路由撤除而删除。
- 模块库、灵感、活动记录、对话、模型设置、Git 与导入不属于单一写作页替代范围，保持原职责。

## 4. 未完成项（进入 P6）

1. 执行验收计划中的确定性、浏览器、在线模型与低频场景；P5 的浏览器结果只覆盖保留的确定性用例。
2. 补候选采用时 `candidate_id` 的服务端过期核对用例；页面采用目前仍走普通正文保存。
3. 完成页边索引在窄屏的段落级内联展示、对话材料冻结与选段建议；当前窄屏为可开合列表辅助区。
4. 完成 P0 编辑器验证方案的专用编辑内核比较；当前仍使用原生 textarea。
5. 补齐 P5 截图清单中的模型可用/不可用、候选生成/过期、索引失败等状态截图。
6. 所有验收通过后统一版本为 1.0.9；在此之前保持 1.0.8。

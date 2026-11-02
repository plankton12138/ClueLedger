# P4 实施记录：单一写作工作区

日期：2026-11-02。状态：**已实施（前端 P4）**。后端未新增接口，未改版本号；运行版本仍为 1.0.8。

## 1. 实际改动

| 文件 | 改动 |
|---|---|
| `frontend/src/state/navigation.ts` | 写作路由改为 `book`（无选择）/ `chapter` / `prose`，可选 `information`；旧 `mode=story / clues / chapter` 解析后回到统一写作页，不再写回旧模式 |
| `frontend/src/api/writing.ts` | 工作台、全书意图、章节壳、章节构思、开始事务、正文保存、创作候选的类型与请求；与 P2/P3 后端接口一一对应 |
| `frontend/src/state/writing.ts` | 工作台缓存；按章节分开的正文草稿；本地存储恢复；连续输入合并保存；摘要冲突保留本地文字；模型候选轮询、停止与放弃 |
| `frontend/src/components/writing/WritingNavigator.tsx` | 左侧导航：全书构思、章节、未挂构思正文、设定与人物，以及新建章节和导入正文 |
| `frontend/src/components/writing/BookPlanView.tsx` | 空白作品起点：作品意图、模型候选、信息线、章节列表与「写第一章」 |
| `frontend/src/components/writing/ChapterPlanGate.tsx` | 章节前置区：自然语言构思、模型候选、整理后的摘要、开始写作；已开始的章节保存后重新确认 |
| `frontend/src/components/writing/ChapterEditor.tsx` | Markdown 源码编辑、中文输入法组合保护、Ctrl/Cmd+S、失焦保存、自动保存状态、冲突对比与覆盖/载入服务端、模型起草/续写 |
| `frontend/src/components/writing/ChapterMarginIndex.tsx` | 页边索引：已写入出处、尚未写入、正文新增、需要对齐；原句点击定位到编辑器 |
| `frontend/src/components/writing/ModelCandidate.tsx` | 通用候选区：生成中、就绪、过期、失败、停止、采用与放弃 |
| `frontend/src/components/workspace/WritingLayout.tsx` | 单一写作面：左侧导航，中部全书/章节前置区/编辑器，辅助区在对话与索引间切换；来源与模块阅读保留 |
| `frontend/src/components/workspace/AppHeader.tsx` | 删除构思、线索入口，只保留「写作」；生成中返回上次写作位置 |
| `frontend/src/components/workspace/WorkspaceShell.tsx`、`ideas/IdeasSurface.tsx`、`clues/SourceReader.tsx` | 去掉旧页面模式下对写作位置的回退 |
| `frontend/src/components/workspace/useUnsavedChanges.ts` | 正文未保存草稿纳入离开提醒 |
| `frontend/src/state/workspace.ts` | 一轮结束后除回读目录与 Git，也按当前选择重读写作工作台 |
| `frontend/src/index.css` | P4 写作导航、构思前置区、候选、连续稿纸与页边索引样式；宽容器并排正文与索引，窄容器由可开合辅助区承载 |
| `frontend/src/state/navigation.test.ts`、`state/writing.test.ts` | 新路由往返、旧模式重定向、草稿保存、摘要冲突与刷新恢复 |
| `frontend/e2e/writing-workbench.spec.ts` | 从空作品写全书意图、建第一章、开始写作、编辑、刷新与切章后正文仍在 |
| `frontend/playwright.config.ts` | 把 P4 闭环用例登记到确定性项目 |

## 2. 关键语义

- **一个页面完成从空作品到第一章。** 全书意图保存后可直接建章节；章节构思保存与开始写作在同一个前置区完成，不跳到别页。
- **模型不自动开始。** 全书意图、章节构思、正文起草/续写都只生成候选；作者采用后仍要保存，采用本身不触发正式写入。
- **门槛不靠隐藏。** 没有正文的章节先显示前置区；已有正文但构思修订变化时，服务端把正文标为只读，页面回到前置区重新确认，不提供可写的空正文。
- **保存是乐观修订。** 自动保存合并连续输入；同一文字的网络重试复用请求键；服务端摘要变化时保留本地文字，提供载入服务端版本或用本地覆盖，不自动覆盖。
- **本地草稿独立于页面。** 草稿按作品与章节保存，切换章节或刷新后仍能恢复；刷新恢复的未保存文字继续自动保存，冲突草稿等作者决定。
- **索引只按事实分组。** 有计划出处就归入正文；没有出处只在开头清单；语义候选标为「正文新增」或「需要对齐」，不合并成计数。未保存的本地文字不参与定位。
- **旧入口不再渲染。** `StorySurface`、`ClueSurface`、`WorkspaceDetail` 源码保留到 P5 清理，但日常写作页不再渲染；来源阅读与模块阅读仍可用。
- **持锁写入不再自锁。** P4 开始前确认 `StoryStore._apply_prose` 已用 `allow_unplanned=True` 调用文件写入，提交事务内不会再次读取故事状态；先前卡住的测试进程来自旧代码。

## 3. 验证

| 命令 | 结果 |
|---|---|
| `npm run build`（frontend） | tsc 与 Vite 构建通过 |
| `npm test`（frontend） | 36 个文件、355 个用例通过 |
| `.\.venv\Scripts\python.exe -m pytest tests/workspace/test_chapter_writing.py tests/workspace/test_writing_api.py tests/workspace/test_creative_assist.py -q` | 22 passed |
| `npx playwright test e2e/writing-workbench.spec.ts --project=workspace` | 1 passed；含 1024 宽度索引辅助区与 760 宽度索引独占主内容后返回正文 |

未运行：前端全部旧 E2E、后端全量与在线模型。P4 浏览器用例使用确定性服务，不调用模型。

## 4. 未完成项（进入 P5）

1. 旧 E2E 用例仍按正文 / 构思 / 线索入口与旧页面编写；P5 清理旧入口时一并更新或删除，不在 P4 用测试保留隐藏页面。
2. 编辑器使用原生 textarea，已做组合输入、撤销与保存保护；P0 的专用编辑内核比较尚未执行，若引入依赖需保持现有保存与冲突适配。
3. 采用模型候选时尚未把 `candidate_id` 带到正文保存；服务端已有采用前核对能力，页面在 P5/P6 接入后需要补采用过期用例。
4. 文件搜索只保留在旧目录组件中，新导航只提供导入、章节、未挂正文与设定人物；P5 需要决定搜索入口的去留。
5. 页边索引的窄屏逐段显示仍是列表辅助区；段落级插入与对话选段材料冻结留到 P5 的一体化布局。
6. Aqua 视觉细化、旧组件与无消费者接口清理、版本统一属于 P5—P6。

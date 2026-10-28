# S2：原文定位与独立章界

日期：2026-10-27。状态：已接入当前账本、资料详情和阅读区，验证结果见本页末尾。
运行包仍为 `0.9.0`，下一阶段为 S3 的共享详情与本章入口。

本页保留 S2 交付时的验证结果。之后按作者要求移除旧数据兼容，当前账本为第 2 版；最新验证见[当前格式说明](current-format-only.md)。

## 作者得到的变化

线索详情的每条依据新增「定位原文」。选择后打开真实来源，突出对应句子或所在段落；返回时恢复资料截止章、原阅读文件、阅读滚动和按钮焦点。当前对话及未发送草稿继续保留。

原文已修改时显示保存的旧引文，打开当前文件，不按旧坐标高亮，也不通过搜索同一句重新关联。删除、外部移动、暂不可读和区间不符分别给出状态。

## 当前数据与接口

| 位置 | 实际职责 |
|---|---|
| `workspace/source_locations.py` | 来源标识、账本依据查找、版本与坐标核验、正文及精确模块修订解析 |
| `workspace/clue_api.py` | `GET /api/file-workspaces/{workspace_id}/source-locations/{evidence_id}` |
| `workspace/story_materials.py`、`api.py` | 当前格式线索的每条可见依据返回 evidence_id |
| `frontend/src/api/clues.ts` | 与后端同名的来源判别类型及只读请求 |
| `frontend/src/state/sourceLocation.ts` | 在途请求代次、作品隔离、返回位置、独立任务章界 |
| `frontend/src/state/navigation.ts` | 安全来源 ID、浏览章界与任务章界的 hash 路由 |
| `frontend/src/components/clues/SourceHighlight.tsx` | 码点转 UTF-16、Markdown 源位置映射、段落降级与原文显示 |
| `Paper.tsx`、`WritingLayout.tsx`、`WorkspaceShell.tsx` | 原文滚动、高亮、窄屏阅读、返回与浏览器历史 |

后端路径相对 `src/hastings/`，三个既有组件位于 `frontend/src/components/workspace/`。

依据 ID 含作品身份及保存的来源内容指纹，不接受客户端路径、引文或坐标。跨作品标识为 403，同作品无记录或超出章界为 404，格式无效为 400；损坏账本继续明确报错。
GET 不初始化、不迁移账本，也不调用模型。按新项目约定，本阶段仅为当前账本提供来源 ID，不扩展旧数据迁移或旧来源链接兼容。

来源响应采用 `source.kind=file|module` 判别，返回 quote、freshness、readable、current_digest 和有界 excerpt。
仅 valid 返回 start/end；坐标是实际读取全文的 Unicode 码点左闭右开区间，digest 核对原始字节。

## 阅读、范围和返回

阅读路径、资料 `as_of_chapter` 与发送请求的 `state_chapter` 分开。
来源跳转保存浏览截止章，并冻结原任务截止章；既有首次和续聊发送使用同一 ContextInput。
写作链接的 `source`、`clue` 只接受安全标识，`as_of`、`task_chapter` 只接受非负安全整数，URL 不读取任意本机路径。

迟到来源响应不能覆盖新作品、新来源或作者随后选择的文件；定位响应与实际文件读取之间再次改稿，也会取消旧区间。
返回先恢复普通写作入口及保存位置，再完成文件回读；浏览器后退和快速重新进入写作不重新打开已关闭定位。

Markdown 使用解析器的源位置，而非渲染文本第一次匹配。中文、emoji、重复引文、强调和链接可定位到对应字符。
实体、转义、行内代码等不能安全对应时突出实际段落，并说明「已定位至所在段落」。
人物小传移除元信息后的展示坐标不替代全文坐标：来源定位时显示完整原文，包括元信息。

服务保存的成功 `file_changed/action=move` 事件可用于解析同章受管改名；不靠同文搜索发现新文件。
章号改变时要求重新核对，超出查看截止章的目标拒绝返回。外部移动没有受管记录，显示来源缺失。

## 模块与后续引用

模块位置核对 library/module/revision/entry 四段引用、字节指纹、区间和创建时 binding_id。
库 head 更新不替换来源；绑定升级或卸下后返回 detached，保留精确旧修订片段并取消高亮。
模块原文片段阅读器已具备；作者选择和保存计划关联的入口仍由 S4 接入，不在本阶段开放。

前后端新增共用 ClueReference 结构，包含 lineage、clue_id、clue_revision、浏览章界及可选推理／报告身份。
该结构尚未接入草稿、请求受理或上下文注入；S6 接入前不提供发送精确线索引用的动作，也不授予任何新写权限。

## 验证

最终结果：后端全量 **1,265 项通过**，前端 **316 项通过**，浏览器 **7 项通过**；静态检查、130 个源文件的类型检查及前端构建通过。
浏览器回归曾发现后退时旧滚动事件覆盖返回位置，已改为独立保存并在阅读区可见后恢复；修复后的同一场景与整组回归通过。

- [来源测试](../../../tests/workspace/test_source_locations.py)：真实字节、BOM/CRLF、中文与 emoji、重复引文、未来回收、失效、受管和外部移动、人物元信息、精确模块、HTTP 权限及引用结构。
- [高亮测试](../../../frontend/src/components/clues/SourceHighlight.test.tsx)、[导航状态测试](../../../frontend/src/state/sourceLocation.test.ts)：源坐标、段落降级、迟到响应、独立章界、改稿竞争。
- [真实浏览器测试](../../../frontend/e2e/clue-source-navigation.spec.ts)：定位、返回、草稿、焦点、滚动、390 宽度与 150% 字号、未来依据和跨作品拒绝。另回归现有选段加入对话。
- 实际命令、源码指纹、最终数量与截图见[验证清单](../artifacts/s2/manifest.json)。

[1440 宽度定位截图](../artifacts/s2/source-1440.png) · [390 宽度、150% 字号截图](../artifacts/s2/source-390-150.png)

上述均为确定性验证，不包含在线模型推理质量、S3 完整本章流程或桌面发布验收。

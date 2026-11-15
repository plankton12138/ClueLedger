# 创作性文本输入清单（P0）

日期：2026-11-02。状态：**已实施（P0）**；P3 已补齐后端辅助端点，前端按钮与候选面板仍待 P4。范围：`frontend/src` 当前所有可输入文本的位置。登记项用于 P3「所有创作文本都有模型路径」的发布检查；控制信息单独列出，不进入辅助协议。

## 1. 判定规则

- **创作内容**：会进入作品、灵感、模块或个人表达，作者可能希望先得到候选的文本。
- **控制信息**：搜索、筛选、路径、URL、连接名称、模型 ID、密钥、数值设置、结构字段；模型代填没有意义或会破坏确定性。
- 目前所有创作输入都只有人工路径，没有任何一处带模型动作；这是 P3 的主要缺口。

## 2. 创作内容

| # | 位置 | 内容 | 保存目标 | 当前模型入口 | v1.0.9 辅助类型 |
|---|---|---|---|---|---|
| 1 | `components/workspace/DirectoryDialog.tsx` 第 136 行 | 新建作品时的「作品名」 | `POST /api/file-workspaces`，同时作为目录名 | 无 | `title`；目录名本身仍是控制信息 |
| 2 | `components/workspace/RenameWorkspaceDialog.tsx` 第 73 行 | 重命名作品 | `PATCH /api/file-workspaces/{id}` | 无 | `title` |
| 3 | `components/ideas/IdeaCapture.tsx` 第 34 行 | 灵感速记 | `POST /{workspace}/ideas` | 无 | `idea` |
| 4 | `components/ideas/IdeaEditor.tsx` 第 85 行 | 灵感正文 | `PATCH /{workspace}/ideas/{idea_id}` | 无 | `idea` |
| 5 | `components/library/NewModuleForm.tsx` 第 35 行 | 模块名称 | `POST /library/modules` | 无 | `title` |
| 6 | 同文件第 36 行 | 模块正文 | 同上 | 无 | `module_content` |
| 7 | `components/workspace/WorkspaceModulePaper.tsx` 第 48 行 | 作品内模块名称 | `PUT /{workspace}/assembly/{binding_id}/content` | 无 | `title` |
| 8 | 同文件第 51 行 | 作品内模块正文 | 同上 | 无 | `module_content` |
| 9 | `components/library/ModuleEditor.tsx` 第 10 行 | 模块条目正文 | 模块更新 / 变更集 | 无 | `module_content` |
| 10 | `components/library/ModuleActions.tsx` 第 16 行 | 另存变体的名称 | `POST /library/modules/{id}/fork` | 无 | `title` |
| 11 | `components/workspace/ConversationPanel.tsx` 第 489 行 | 对话指令 | `POST /{workspace}/conversations/{id}/turns` | 无 | `next_action`（先给建议，作者选择后再发送） |
| 12 | `components/chat/AuthorQuestionCard.tsx` 第 74 行 | 回答作者问题 | 提问回答接口 | 无 | `next_action`；回答内容仍是作者决定 |
| 13 | `components/workspace/OperationControls.tsx` 第 50 行 | 补充任务要求 / 插话 | 操作插话接口 | 无 | `next_action` |
| 14 | `components/library/EntryFields.tsx` 第 22 行 | 模块条目字段值 | 随模块保存 | 无 | 按字段类型决定；自由文本字段可用 `module_content`，枚举 / 数值不代填 |

## 3. 本版新增、当前不存在的创作输入

| # | 位置 | 内容 | 保存目标 | 辅助类型 |
|---|---|---|---|---|
| 15 | 全书构思区（待建） | 一段作品意图 | `PUT /story/book-intent` | `book_intent` |
| 16 | 章节前置区（待建） | 章节标题 | `PUT /story/chapters/{id}/plan` | `title` |
| 17 | 章节前置区（待建） | 本章最小构思 | 同上 | `chapter_plan` |
| 18 | 章节编辑器（待建） | 正文起草 / 续写 / 改稿 | `PUT /story/chapters/{id}/prose` | `prose_draft`、`prose_continue` |
| 19 | 章末差异区（待建） | 差异处理说明 | 故事变更或联合提交 | `alignment_resolution` |
| 20 | 对话输入（待建动作） | 「给我下一步建议」 | 只填入输入框，不自动发送 | `next_action` |

## 4. 控制信息与排除原因

| 位置 | 内容 | 排除原因 |
|---|---|---|
| `ClueSurface.tsx` 第 191 行、`IdeaList.tsx` 第 96 行、`LibraryBrowser.tsx` 第 17 行、`WorkspaceToc.tsx` 第 87 行、`ProviderPicker.tsx` 第 38 行、`ReferencePicker.tsx` 第 112 行 | 搜索与筛选 | 只影响读取范围，不进入作品事实 |
| `DirectoryDialog.tsx` 第 136 行中的路径部分、`AssemblyPanel.tsx` 第 122 行 | 目录名 / 路径 | 文件系统标识，必须由作者明确选择 |
| `ConnectionForm.tsx` 第 287、341、390、456、477 行 | 连接地址、密钥、模型 ID | 安全与配置信息，模型不得代填 |
| `ActivityPanel.tsx` 第 357、359、387、389 行，`ModuleLibraryPage.tsx` 第 61 行 | 复选开关 | 界面状态 |
| `FontSizeControl`、布局宽度、`ModuleActions` 删除确认 | 数值与破坏性确认 | 确定性设置，不由模型建议 |
| `EntryFields.tsx` 第 33 行 | 条目字段 JSON | 结构序列化，不要求模型生成 JSON；字段内的自由文本按第 14 项处理 |

## 5. 缺口与实现顺序

1. **先有数据模型，再有输入。** 第 15—19 项目前没有对应接口，P1/P2 先落契约，P3 再补模型动作。
2. **对话类输入不自动执行。** 第 11—13、20 项只产生建议文本，选择后仍由作者发送或提交。
3. **标题类动作不与「开始写作」争夺主操作。** 作品名、章节名、模块名的模型入口放在输入旁，不改变页面唯一主操作。
4. **无模型时全部可见但禁用。** 人工输入与保存不受影响；错误码统一为 `MODEL_UNAVAILABLE`。
5. **发布检查以本清单为准。** P6 逐项核对第 2、3 节；新增创作输入必须补登记，控制信息不得出现无意义模型按钮。

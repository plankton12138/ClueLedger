# I0 冻结边界与建立专项基线

日期：2026-10-25。状态：**已完成**。本阶段只记录现状、样本与编号，不修改产品行为。

## 1. 目的

让灵感专项不覆盖已有修复，也不把历史证据当成新结果。后续阶段（I1–I6）都以此处的
现状描述、样本集合与验收编号为准。

## 2. 工作树现状（开始 I0 时的记录）

分支 `codex/v0.8`，最近提交 `c54d212 feat(v08): 完成前端体验、编辑可靠性与交互验收`。

未提交改动 21 个文件（135 行新增、44 行删除），都属于上一轮前端体验收尾，不属于本专项：

| 类别 | 文件 |
|---|---|
| 文档 | `dev-notes/README.md`、`dev-notes/v0.8/README.md`、`dev-notes/v0.8/frontend-experience-plan.md`、`dev-notes/v0.8/notes/H2-frontend-foundation.md`、`frontend/README.md` |
| 证据 | `dev-notes/v0.8/artifacts/frontend-experience/verification.md`、同目录 8 张 H2 截图 |
| 前端 | `frontend/src/components/workspace/WritingLayout.tsx`、`frontend/src/index.css`、`frontend/src/state/layout.ts`、`frontend/src/state/layout.test.ts`、`frontend/e2e/frontend-foundation.spec.ts`、`frontend/e2e/workspace.spec.ts` |
| 未跟踪 | `dev-notes/v0.8/ideas-experience-plan.md`、`dev-notes/v0.8/ideas-experience-examples.md` |

处理方式：本专项的改动与上述改动分开提交，不重排、不覆盖这些文件里的既有结论。
`data/` 已被 `.gitignore` 忽略，样本作品与运行数据不进版本库。

## 3. 现状核对

### 3.1 弹层能力

- 只有 `AppDialog`（Radix Dialog）：固定居中、带遮罩、默认模态、限制背景焦点、Esc 关闭、
  关闭后把焦点还给触发元素。宽屏悬浮面板不能直接套用它的模态行为。
- 写作页的目录按钮与正文目录树各自处理 Esc：`WritingLayout` 在未停靠时按 Esc 收起目录，
  `WorkspaceToc` 在重命名时用 Esc 取消重命名。
- 会话面板重命名也用 Esc 取消。

### 3.2 快捷键

- 目前没有应用级快捷键，也没有全局键盘监听。
- 唯一的保存快捷键在 `IdeaEditor` 的正文输入框内：`Ctrl/⌘ + S` 触发保存。
- 新面板的默认组合需要避开浏览器保留键（`Ctrl/⌘ + W`、`Ctrl/⌘ + N`、`Ctrl/⌘ + L`、
  `Ctrl/⌘ + T`、`Ctrl/⌘ + S` 已在编辑器内使用），并避开中文输入法选词阶段的按键。

### 3.3 草稿与路由

- 灵感草稿只存在内存：`state/ideas.ts` 按「作品 + 条目键」保存 `entryEditors`，
  新建草稿用 `new-idea-N` 递增键区分，切换作品各自保留。
- 除布局偏好外，前端没有 `localStorage`/`sessionStorage` 持久化，因此刷新即丢失草稿，
  与本专项「只承诺当前页面会话内保留」一致。
- 旧链接 `#/workspaces/{id}/ideas?entry={ideaId}` 由 `state/navigation.ts` 解析，
  `WorkspaceShell` 用它渲染独立页面 `IdeasPage`。
- 顶栏「灵感」是普通导航链接，不是面板开关。

### 3.4 服务与工具

- `IdeaStore.create/update` 目前会 `strip()` 正文；长度也按去空白后的文本计算。
- 列表固定按 `updated_at` 倒序，摘要固定取前 120 字符，没有创建时间、长度与截断标记。
- `freeze_context` 只检查所选修订的 `deleted` 标记，没有同时检查条目当前是否已删除。
- `save_idea` 的幂等键固定为 `{turn_id}:idea`，一轮只能保存一条；schema 仍要求标题与标签。

## 4. 合成样本

脚本：`scripts/build_ideas_samples.py`（可反复重建，默认写入 `data/ideas-samples/`）。
全部内容由脚本生成，不含作者真实作品；破坏性操作只在样本目录内执行。

| 样本 | 覆盖的边界 |
|---|---|
| `plain` | 最普通的记录，列表与编辑的对照基准 |
| `legacy-title-tags` | 旧客户端写入的标题与标签，验证读改不清空 |
| `untitled` | 无标题记录，验证界面不以内部 ID 充当标题 |
| `long` | 7211 字符，超过摘要长度，验证完整读取后再编辑 |
| `unicode` | 表情、制表符、中文标点与换行，验证字符数与原文保真 |
| `whitespace-only` | 旧文件形态的全空白正文，验证诊断而不是隐藏条目 |
| `history` | 四条修订（新建、两次修改、一次恢复），验证历史与列表顺序 |
| `deleted` | 已删除条目，验证最近删除与撤销 |
| `sample-b` 作品 | 第二个作品的灵感，验证作品隔离 |

损坏样本：`灵感本/idea_00000000000000ff.json` 写入不完整 JSON，用于验证列表
`invalid` 计数且不隐藏其他记录。

重建命令：

```powershell
uv run python scripts/build_ideas_samples.py
```

## 5. 验收编号分配

`IE01–IE22` 已在计划的验收矩阵中定义。对应阶段如下，证据统一写入
`dev-notes/v0.8/artifacts/ideas-experience/`：

| 编号 | 阶段 |
|---|---|
| IE01、IE02 | I2 |
| IE03、IE04、IE05、IE06、IE09、IE10 | I1、I2 |
| IE07、IE08 | I2 |
| IE11、IE12、IE16 | I3 |
| IE13、IE14、IE15、IE17 | I4 |
| IE18、IE19、IE20 | I5 |
| IE21、IE22 | I6 |

## 6. 基线结论

- 现状与计划第 2 节的差距表一致；未发现计划里已经过期或已实现的条目。
- 基线测试：`tests/workspace/test_ideas.py`、`test_ideas_api.py`、`test_ideas_tools.py`
  在本次执行时 38 项全部通过，作为 I1 改动前的对照。
- 未覆盖项：多会话样本与在线模型结果不在本阶段建立，分别由 I3 与 I6 补齐。
  历史 H1–H6 的截图与结论保持原样，不作为本专项证据。

# 灵感悬浮体验

日期：2026-10-25。状态：已实施，本次确定性验证通过。版本保持 `0.8.0`。
产品取舍见[设计说明](../../../product-design/v0.8/ideas-companion.md)。

## 当前行为

灵感从主导航移到唯一悬浮入口。按钮与面板标题栏可以拖动、用方向键移动和复位；
面板默认显示快速记录和最近三条内容，每条可直接加入对话、原处修改。
记录较多时主动打开全部与搜索；删除恢复、历史、使用记录及模型操作仍可达。
同一秒内连续创建的灵感现在按微秒时间排序，旧时间格式兼容，不迁移旧文件。

## 实现分工

| 位置 | 职责 |
|---|---|
| `components/ideas/IdeasSurface.tsx` | 唯一容器、快捷键、加入对话、目标对话核对、来源与模型操作 |
| `components/ideas/useIdeasPosition.ts` | 可视区域、顶栏避让、实际尺寸、拖动阈值、指针捕获与键盘移动 |
| `state/ideasSurface.ts` | 面板开关、全部内容模式、旧链接定位、本机位置偏好 |
| `components/ideas/IdeaList.tsx`、`IdeaEditor.tsx` | 短记录、直接引用、原处编辑、按需管理、筛选后保留当前操作 |
| `components/ideas/IdeaCapture.tsx` | 记录与记下并加入、中文输入法保护、保存过程连续输入保护 |
| `state/ideas.ts` | 完整原文读取、冻结保存命令、请求重放、按作品隔离草稿与异步结果 |
| `state/layout.ts`、`WritingLayout.tsx` | 引用成功后显示目标对话，兼容窄窗口与原有聊天草稿 |
| `workspace/ideas.py` | 新时间戳精度，记录与修订存储契约保持兼容 |

## 数据和接口

继续使用已有灵感创建、单条读取、修改、分页、历史、恢复与活动接口，不新增同义服务。
从列表直接加入时读取完整原文，不选中条目、不加载历史，也不使用截断摘要作为正文。
有修改稿时先保存并绑定保存结果；已有引用保持固定修订，作者明确更新才替换。

记录成功才清空提交的输入。结果不明时重用原命令与请求键，失败不会提前加入引用；
保存期间继续输入的内容保留在同一条记录的修改稿中。跨作品的保存结果只更新原作品草稿，
不清除新作品的忙状态和提示。直接读取或保存期间切换对话，不把结果加入后来选中的对话。

位置以可用区域中右下角的比例保存在 `hastings.ideas.position`。拖动达到 6px 才视为移动，
移动结束消费对应点击；输入框和正文不参与拖动。缩放与可视区域变化只钳制当前显示，
鼠标、触摸与键盘共用位置边界。存储损坏或不可写时，记录和本次拖动仍可用。

## 新记录即时编辑修复（2026-10-25）

现象：通过“记下”或“记下并加入对话”创建记录后，立即打开修改，输入无法更新；先选择另一条记录再回来可以编辑。
原因：快速记录保存成功后已将返回结果设为选中项，干净的新记录尚未建立编辑草稿；
`select` 遇到已选中且版本一致的记录直接返回，`setEntryBody` 因缺少草稿而忽略输入。

修复：已选中分支在缺少草稿时用完整保存结果建立正文、修订和指纹基线，已有草稿继续保留。
修改后保存仍更新同一条记录。新增单元回归在修复前复现失败，修复后通过；两种创建方式均补了真实浏览器即时编辑和保存验证。

## 验证记录

| 检查 | 结果 |
|---|---|
| `npm test` | 即时编辑修复后：24 个文件，251 项通过 |
| `npm run build`，以及浏览器服务启动前重新构建 | 类型检查和生产构建通过；保留既有单包超过 500 kB 的提示 |
| `npx playwright test --project=workspace` | 悬浮体验初次交付时：67 项通过，1 项历史基线默认跳过；当时包含 7 项灵感专项 |
| `npx playwright test --project=workspace ideas-companion.spec.ts` | 即时编辑修复后：9 项通过，包含两种创建方式的立即修改与同条记录保存 |
| `uv run pytest tests/workspace/test_ideas.py tests/workspace/test_ideas_api.py tests/workspace/test_idea_activity.py tests/workspace/test_ideas_tools.py -q` | 70 项通过 |
| `uv run ruff check src/hastings/workspace/ideas.py tests/workspace/test_ideas.py` | 通过 |
| `uv run mypy src/hastings/workspace/ideas.py` | 通过 |
| `git diff --check` | 通过 |

浏览器使用独立端口 `8938`，作品、状态和库位于被忽略的 `data/test-tmp/ideas-companion-20261025/`。
新专项 `frontend/e2e/ideas-companion.spec.ts` 覆盖位置恢复、拖动区分、直接连续加入、全文引用、
原处修改、失败重放、窗口与字号、切换对话和旧链接。截图位于
`data/test-tmp/ideas-companion-screenshots/`，检查了实际桌面与窄窗口画面，没有覆盖历史验收截图。
窗口矩阵包括 1440、1024、640、390px 与 100%、150% 字号；实际触摸设备未单独验收。

本轮不执行在线模型评测或桌面打包；模型展开与用于写作的现有接线通过确定性回归核对，
不将模拟响应结果记为在线语义验证。

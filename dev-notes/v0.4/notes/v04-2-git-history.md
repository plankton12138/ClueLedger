# V04-2：Git 版本闭环

日期：2026-10-20。范围：计划 M02 与 M05 的记录部分——真实 Git 初始化、外部修改基线、差异、提交与最新撤销。不含写作 Agent、对话、HTTP 端点与前端接线。

## 目标

让文件改动有真实历史：作者的改动与模型的改动分属不同提交；没有变化不产生空提交；停止或失败时已写入的内容仍然记录并标明；Git 失败不掩盖「文件已保存」这一事实。

## 实际改动

新增：

- `src/hastings/workspace/git.py`：仓库校验（根一致、分支、冲突、预暂存）、初始化与基线提交、状态、工作区差异、提交差异、历史、提交、最新撤销。
- `src/hastings/workspace/service.py`：`WriteSession` 与 `RunResult`，负责「准备基线 → 结束记录」的两步协调。
- `tests/workspace/test_git.py`、`tests/workspace/test_service.py`：全部使用真实临时仓库。
- `dev-notes/v0.4/modules/git-history.md`：模块说明。

修改：

- `src/hastings/workspace/api.py`：新建作品时一并初始化 Git（`create=true`），打开已有目录仍然只读检查、不初始化。
- `tests/workspace/test_api.py`：新建作品断言 `.git` 存在，打开普通目录断言不产生任何文件。

## 契约与数据影响

- 新建作品目录多出 `.git/` 与 `.gitignore`；`.gitignore` 固定忽略 `.obsidian/`、`.DS_Store`、`Thumbs.db`、`.hastings-*.tmp`。
- 一轮写入的结果结构固定为 `{status, recording, changed_paths, commit, note}`；`status` 与 `recording` 是两条独立的轴。
- 新增错误码：`GIT_NOT_REPOSITORY`、`GIT_ROOT_MISMATCH`、`GIT_NO_BRANCH`、`GIT_MERGE_CONFLICT`、`GIT_STAGED_CHANGES_CONFLICT`、`GIT_HEAD_MOVED_CONFLICT`、`GIT_DIRTY_CONFLICT`、`GIT_REVERT_CONFLICT`、`GIT_COMMIT_NOT_FOUND`、`GIT_UNAVAILABLE_FAILED`、`GIT_TIMEOUT_FAILED`、`GIT_COMMAND_FAILED`。
- 提交身份固定为 `Hastings <hastings@local>`，只对本次命令生效，不写全局配置。
- 作品内容仍然只在 Markdown 文件里；Git 只记录历史，不参与内容读取。

## 实现取舍

- **不做自己的版本对象**：差异、日志、撤销都直接读 `git` 输出。代价是输出格式依赖 Git 的稳定接口（porcelain v1、`%x1f` 分隔），收益是作者在应用外看到的历史与应用内完全一致。
- **未跟踪文件不进工作区差异**：`git diff HEAD` 本身不含新文件，这里不伪造 diff。新章节的完整内容在提交差异里看；工作区状态把它标为「未跟踪」。这个边界写进了模块说明，避免把「差异为空」误读成「没有改动」。
- **提交前复查暂存区**：`record()` 暂存完自己的路径后，如果暂存区里出现本次之外的路径，直接拒绝提交，不把未知改动混进本轮。这是对「运行期间外部改动」的低成本防护。
- **撤销只覆盖最新一次**：`git revert` 生成反向提交，不移动分支指针；有后续改动时明确拒绝，不做三方合并。
- **初始化在创建操作里**：新建作品时同时建立版本记录，历史从第一份内容开始；打开已有目录不擅自初始化，作者需要时再启用。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| Git 与记录协调 | `uv run pytest tests/workspace/test_git.py tests/workspace/test_service.py -q` | 33 项通过 |
| 文件工作区整体 | `uv run pytest tests/workspace -q` | 107 项通过 |
| 完整后端回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --junitxml=data/test-tmp/v04-2-full.xml` | 2082 项通过，0 失败 0 错误 0 跳过（370 秒） |
| 静态检查 | `uv run ruff check src/hastings tests/workspace` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 268 个文件无问题 |

覆盖的场景：初始化与已有内容基线、重复初始化、上级仓库不被采用、仓库根不一致、预暂存与合并冲突拒绝、分离头指针拒绝、受管范围、只提交受管路径、无变化不提交、提交身份、工作区与提交差异、未知提交、历史顺序、撤销成功/HEAD 不匹配/工作区不干净、git 缺失与超时；记录侧覆盖外部改动先单独提交、只读讨论不提交、停止与失败仍记录、Git 失败记 `unrecorded`、HEAD 被外部推进、暂存区被占用、单独补记。

## 偏差与未完成

- 没有 HTTP 端点：`git/status`、`git/diff`、`git/log`、`git/init`、`git/record`、`git/revert` 未提供，前端接线阶段再加。
- 没有并发锁与对话生命周期：同一作品「同时最多一个写任务」随 V04-3 的轮次实现。
- 撤销冲突路径只验证了拒绝条件（HEAD 不匹配、工作区不干净），没有构造真实的 revert 冲突仓库；该分支会清理中间状态并保留工作文件，但未取证。
- 写作 Agent、会话与事件流仍未接入；`WriteSession` 目前只由测试驱动。

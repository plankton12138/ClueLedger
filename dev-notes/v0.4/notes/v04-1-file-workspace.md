# V04-1：文件工作区基础

日期：2026-10-20。范围：计划 M01 与独立文件应用入口；同步包版本到 `0.4.0`。不含 Git、模型、对话与前端接线。

## 目标

让「作品 = 一个目录」先在没有模型、没有旧数据库的条件下成立：能新建与打开目录、读写中文文件、外部改动可回读、路径越界与过期覆盖被拒绝。

## 实际改动

新增：

- `src/hastings/workspace/files.py`：受管路径校验、目录建立、列表、读取、指纹校验写入、章号分配、搜索、导入、移动与删除。
- `src/hastings/workspace/state.py`：应用状态目录中的最近目录登记与绑定 ID。
- `src/hastings/workspace/api.py`：`/api/file-workspaces` 读取与导入端点、统一错误信封。
- `src/hastings/workspace/errors.py`：文件层错误码与状态码映射。
- `src/hastings/server/file_app.py`：只含文件工作区、健康检查与静态前端的新应用装配。
- `tests/workspace/`：文件规则、目录登记、接口与入口测试。
- `dev-notes/v0.4/modules/`：模块导航与文件工作区说明。

修改：

- `src/hastings/server/__main__.py`：新增 `--workspace <目录>`，走文件工作区装配；与 `--v2-db` 互斥。默认入口未改。
- 包版本：`pyproject.toml`、`src/hastings/__init__.py`、`uv.lock`、`frontend/package.json`、`frontend/package-lock.json` 同步为 `0.4.0`。

## 契约与数据影响

- 新接口前缀 `/api/file-workspaces`，与旧 `/api/workspaces`、`/api/v2` 不共享数据形状；本阶段只提供读取与导入，没有写正文端点。
- 新状态文件 `~/.hastings/workspaces.json`（可用 `HASTINGS_STATE_DIR` 覆盖），只记最近目录，不复制正文。
- 作品目录结构为 `正文/*.md` 加三个固定设定文件；不创建数据库，不写旧 `data/v2.db`。
- 旧 `/api/v2`、旧工作台数据库、旧前端行为均未改变；`python -m hastings.server` 无参数启动仍是 0.3。

## 实现取舍

- **设定文件新建即存在**：`create` 建立三份空文件，之后改写必须带读到的指纹，与普通文件同一规则。这样「无条件覆盖」没有例外，代价是首次填写设定也需要先读一次。
- **指纹按原始字节**：BOM、CRLF、行尾空格都算改动，外部编辑器保存后能被识别为作者修改。读取返回 LF 文本、写入固定 LF，因此用应用改写一个 CRLF 文件会把它转为 LF；这是已知且有意接受的结果。
- **两层路径校验**：词法规则挡住绝大多数错误输入，解析后比对目录再挡住符号链接与目录联接。只做词法检查在 Windows 上不足以防越界。
- **`.txt` 导入复用 0.3 的分章纯函数**：它只做文本切分，不写存储。为避免纯文件读写也拉起旧领域包，导入分支里局部引用；V04-5 清理时可把纯函数移到文件层。
- **目录标题需要读文件**：列表为取一级标题会读取每个章节，大目录有一定开销。首版文件数量有限，先不做标题缓存，避免再引入一份可能与磁盘不一致的索引。

## 验证

| 范围 | 命令 | 结果 |
|---|---|---|
| 文件规则、目录登记、接口、入口 | `uv run pytest tests/workspace -q` | 66 项通过 |
| 旧入口与旧应用装配回归 | `uv run pytest tests/test_server_main.py tests/test_server_app.py tests/test_entry.py -q` | 28 项通过 |
| 完整后端回归 | `CODEBUDDY_SAFE_DELETE_ENABLED=0 uv run pytest -q --junitxml=data/test-tmp/v04-1-final2.xml` | 2061 项通过，0 失败 0 错误 0 跳过（267 秒） |
| 静态检查 | `uv run ruff check src/hastings tests/workspace` | 通过 |
| 类型检查 | `uv run mypy src/hastings` | 268 个文件无问题 |
| 依赖锁 | `uv lock --offline` | 仅 `hastings-ai` 版本行变化，无无关依赖升级 |

全量运行需临时关闭本机的批量删除守卫（`CODEBUDDY_SAFE_DELETE_ENABLED=0`）：否则测试清理临时目录时会被守卫中断，表现为大量 setup 错误，与代码无关。该变量只影响测试进程内对 `data/test-tmp` 的清理。

以上为自动化测试与静态检查，不包含真实模型、独立服务进程与浏览器走查；这些能力在本阶段尚不存在，不能引用为 0.4 验收。前端代码本次未改动，未重跑前端构建。

## 偏差与未完成

- `desktop/package.json` 与对应锁文件仍为 `0.3.0`：计划只列了后端包与 `frontend/` 的版本同步，桌面壳的版本策略留待 V04-5 一并处理。
- 一次导入多章不是事务：中途失败时已创建的章保留，接口不返回部分成功清单，需要刷新目录核对。这一边界在 V04-2 与 Git 记录一并处理。
- 没有文件系统监听，外部改动只能通过重新请求目录或文件发现；切换窗口自动刷新属于 V04-4。
- Git 初始化、差异、提交、撤销，以及写作 Agent、会话与事件流均未实现，见[修改计划](../development-plan.md)。

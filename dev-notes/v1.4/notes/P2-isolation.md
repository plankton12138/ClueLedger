# v1.4 P2：账号下的作品、任务、模型与模块库隔离

更新：2026-11-06。状态：离线测试、两账号接口验证与本地 server 模式浏览器流程通过；真实在线模型的双账号调用与线上部署未验证。

## 一、交付范围

| 面 | 实现 |
|---|---|
| 当前账号 | `identity.py` 用请求级 ContextVar 保存 `user_id`；认证中间件进入应用前绑定、退出后恢复 |
| 作品注册 | 数据库新增 `workspaces(id, owner_user_id, storage_key, name, ...)`；`accounts/workspaces.py` 负责按账号列表、创建、打开、改名与删除 |
| 服务器目录 | 作品落在 `--workspaces-root/<user_id>/<workspace_id>`；客户端只提交作品名，路径由服务器解析并校验在作品根内 |
| 访问边界 | 所有作品路由先按 `id + owner_user_id` 查询；跨账号与不存在统一 404，不泄露存在性 |
| 本地入口 | server 模式禁用 `/default-location`、`/pick-directory`、任意 `path` 打开，以及 `--workspace`/`--config`/`--model`/`--library-dir` 共享参数 |
| 任务归属 | `Operation.owner_user_id` 创建时固定；读取、列表、暂停、继续、事件流与结果查询都核对当前账号 |
| 运行任务键 | 章节索引、修订分析与创作辅助的内存任务表按作品状态目录加前缀，同名章节不会跨作品取消 |
| 模型 | `server/user_services.py` 每账号惰性建立 `ProviderBook`、`ModelConnectionService`、授权服务与配置路径；当前选择按账号保存 |
| 凭据 | `ScopedCredentialStore` 以 `hastings:user:<user_id>:` 作为命名空间；订阅引用判断改为按段匹配，兼容带前缀的引用 |
| 模块库 | 每账号独立目录 `<libraries-root>/<user_id>` 与独立 catalog；作品装配使用所属账号的 `library_id` |
| 前端 | server 模式只提供名称新建；隐藏打开已有目录、路径展示与位置选择；退出与 401 失效整页重载，清空模块级 store |

主要入口：[user_services.py](../../../src/hastings/server/user_services.py)、[workspaces.py](../../../src/hastings/accounts/workspaces.py)、[identity.py](../../../src/hastings/identity.py)、[DirectoryDialog.tsx](../../../frontend/src/components/workspace/DirectoryDialog.tsx)。

## 二、验证证据

| 项 | 结果 |
|---|---|
| 作品隔离测试 | 4 项：两账号列表/读写互不可见、跨账号改名/删除/活动记录/事件流 404、server 模式入口禁用、客户端路径被忽略、重命名与删除边界、模型工具式文件读取无法穿越到其他作品 |
| 操作隔离测试 | 1 项：另一账号读取、列表、暂停与事件流均 404 |
| 模型与模块库隔离 | 1 项：连接与选择按账号、凭据引用带账号前缀且不重叠、授权服务与私有库实例独立、模块列表互不可见 |
| 账号与入口回归 | `tests/accounts` 18 项、`tests/workspace/test_entry.py` 15 项通过 |
| 模型/库/作品回归 | `test_model_api`、`test_model_probe`、`test_model_subscription`、`test_module_api`、`test_assembly_api`、`test_api` 共 139 项通过 |
| 静态检查 | mypy 171 个源文件通过；ruff check 与 format 通过 |
| 前端 | 339 项单测与 `npm run build` 通过 |
| 本地模式浏览器 | `writing-workbench` 5 项 Playwright 通过，未受账号资源注册表影响 |
| server 模式浏览器 | A 新建「雪山旅馆」→ 退出 → B 登录看到空列表 → B 新建自己的作品；B 读取 A 作品返回 404；A 作品 ID 不出现在 B 列表；列表路径为空；控制台除预期的 404 外无错误 |

浏览器验证使用 `--mode server --database ... --workspaces-root ... --libraries-root ... --static-dir frontend/dist`，邮件走文件投递。

## 三、尚未完成与边界

- 未用真实在线模型执行双账号写作轮次；模型连接、选择、凭据与授权会话的隔离已在接口层验证，模型工具的文件边界以 `Workspace` 读取越界测试代替。
- P3 已把 relay 的订单与权益接到账号 `user_id`，匿名 `POST /v1/session` 已移除，记录见 [P3 账号订单](P3-billing.md)；P4 的用量预留与代理仍未实现。
- 生产 SMTP、HTTPS、反向代理来源与备份恢复仍属于 P5；当前文件投递只用于本地与测试。
- 每账号资源在进程内缓存、不主动回收；单机用户量增长后可加空闲淘汰，但不影响正确性。
- server 模式仍使用单机 SQLite 与单进程装配；多副本写入不在本版范围。

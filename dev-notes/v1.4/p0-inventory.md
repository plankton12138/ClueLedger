# v1.4 P0：盘点与契约冻结

更新：2026-11-06。状态：P0—P4 已完成离线与本地浏览器验证；P5 的生产配置、部署与运维代码/脚本已完成，真实部署联调待执行。记录见 [P1](notes/P1-email-accounts.md)、[P2](notes/P2-isolation.md)、[P3](notes/P3-billing.md)、[P4](notes/P4-metering.md) 与 [P5](notes/P5-deployment.md)。本文记录实施前基线与冻结契约，作为 P6 上线验收的共同依据。范围与验收以[开发计划](development-plan.md)为准。

## 一、基线结论

| 面 | 现状 | 对 v1.4 的影响 |
|---|---|---|
| 写作服务 | `/api/file-workspaces/*`、`/api/model-*`、`/api/module-library/*`、`/api/operations/*` 全部无鉴权 | P1 先建立登录门；P2 再逐资源加归属 |
| 作品登记 | `RecentWorkspaces` 单例，作品 ID = 规范化绝对路径哈希 | 两个账号打开同一路径会得到同一 ID，P2 改为服务器分配 `storage_key` |
| 全局状态 | 运行注册表、操作存储、私有模块库、模型选择、凭据引用、任务取消表均为进程级 | P2/P3 逐项加入 `user_id` 与 `workspace_id` 范围 |
| 目录访问 | `POST /api/file-workspaces` 接受任意绝对路径；系统目录选择器在服务端弹窗 | server 模式禁用任意路径与系统选择器，P2 改为服务器分配目录 |
| 模型凭据 | 系统凭据存储服务名 `hastings`，连接名即主键；`config.yaml` 只有一份当前选择 | P3 改为账号范围加密存储，平台订阅密钥不下发浏览器 |
| 任务与事件 | 操作按 48 位 ID 全局寻址；SSE 只校验作品与会话 | P2 校验事件订阅、重连与结果查询归属 |
| 支付服务 | `relay` 独立进程、独立 SQLite；`POST /v1/session` 匿名发 token；订单与权益已可用 | P3 复用账号并统一数据库，生产关闭匿名入口 |
| 计量 | `usage_windows` / `usage_ledger` 表存在但从未写入 | P4 建预留表、代理与结算 |
| 前端 | 5 个 API 客户端 + 少量裸 `fetch`，无鉴权头、无登录页、无账号状态 | P1 增加会话门与统一 CSRF；P2 起所有消费者按用户范围取数 |
| 部署 | 写作服务 `127.0.0.1:8000`，支付服务 `0.0.0.0:8300`，无反向代理配置 | P5 单域名 HTTPS，内部端口只监听回环 |

盘点证据（文件与行号）保留在本文第三节，代码修改后以源码为准。

## 二、已冻结契约

### 2.1 运行模式

`hastings.server` 增加 `--mode local|server`，默认 `local`。

| 模式 | 身份 | 作品目录 | 账号接口 | 用途 |
|---|---|---|---|---|
| `local` | 进程即作者，无登录 | 任意本地目录，保留现有选择器 | `/api/auth/me` 返回 `{"mode":"local","authenticated":true}` | 现有本地与桌面方式 |
| `server` | 邮箱账号 + 服务器会话 | 服务器分配（P2） | 完整注册、验证、登录、退出、找回 | 公网部署 |

server 模式启动必须显式给出 `--database`；缺项直接失败，不回退仓库内沙箱文件或本地状态目录。两种模式共用同一代码路径，差别只在装配时是否挂认证服务。

### 2.2 账号与会话

- `user_id` 为 `usr_` + 16 字节随机十六进制，创建后不变；作品、模型连接、订单、权益、用量只引用该值。
- 邮箱保存两份：`email` 保留用户输入的 NFKC 规范化结果，`email_norm` 为 `email.casefold()` 的唯一比较值；不删除点号、不删除 `+` 后缀。
- 密码使用 `hashlib.scrypt`（N=16384、r=8、p=1、32 字节输出、16 字节随机盐），编码为 `scrypt$N$r$p$salt$hash`；校验使用常量时间比较。密码长度 8—128。
- 邮箱未验证不创建会话。验证与重置使用 6 位数字验证码，数据库只存 SHA-256 哈希，绑定用途、账号与有效期；同一用途重新发送会使旧验证码失效，连续输错 5 次后需重新发送。
- 会话为服务端记录，浏览器只持有随机 token；Cookie 名 `clue_session`，`HttpOnly`、`SameSite=Lax`，公共地址为 HTTPS 时加 `Secure`。会话默认 30 天，与订阅有效期无关。
- 写操作校验来源：`Origin` 存在时必须等于公共地址或显式允许的来源；有会话的写请求还必须带 `X-CSRF-Token`，值与该会话记录一致。
- 登录与邮件接口按「邮箱 + 来源地址」固定窗口限速；注册、重发验证、忘记密码统一返回已受理，不披露账号是否存在。

### 2.3 数据库

server 模式使用一个 SQLite 数据库，默认由 `--database` 指定；本地模式不创建。连接统一启用 `foreign_keys=ON`、`busy_timeout=15000`，初始化时启用 WAL。所有时间戳为 UTC Unix 秒。

```sql
CREATE TABLE accounts (
    user_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    email_norm TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',      -- active | disabled
    verified_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    csrf_token TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    last_seen_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked_at INTEGER
);
CREATE INDEX idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE auth_codes (
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    purpose TEXT NOT NULL,                      -- verify_email | reset_password
    code_hash TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    consumed_at INTEGER,
    PRIMARY KEY (user_id, purpose)
);

CREATE TABLE auth_rate_limits (
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    window_start INTEGER NOT NULL,
    count INTEGER NOT NULL,
    PRIMARY KEY (scope, key, window_start)
);

CREATE TABLE workspaces (
    id TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    storage_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    opened_at INTEGER NOT NULL
);
CREATE INDEX idx_workspaces_owner ON workspaces(owner_user_id, opened_at DESC);
CREATE UNIQUE INDEX idx_workspaces_owner_name ON workspaces(owner_user_id, name);
```

`storage_key` 形如 `<user_id>/<workspace_id>`，由服务器在 `--workspaces-root` 下解析；客户端只提交作品名。P3—P4 在同一数据库追加 `orders`、`entitlements`、`usage_*` 等表；旧 `data/relay/relay.sqlite3` 不迁移。当前 relay 的 `accounts/sessions` 在 P3 由本表替换，订单与权益改为引用 `accounts.user_id`。

### 2.4 认证接口

统一 JSON；错误响应为 `{code,message,request_id}`，`message` 使用 `[错误码] 位置：现象。建议：…`。成功响应不返回密码、token 哈希或凭据。

| 方法与路径 | 请求 | 成功响应 | 约束 |
|---|---|---|---|
| `POST /api/auth/register` | `{email,password}` | `202 {status:"accepted"}` | 邮箱已存在也返回同一状态；限速 |
| `POST /api/auth/verify-email` | `{email,code}` | `200 {verified:true,email}` | 6 位验证码；单次消费，连续错误 5 次失效 |
| `POST /api/auth/resend-verification` | `{email}` | `202 {status:"accepted"}` | 仅未验证账号实际发信；限速；旧验证码失效 |
| `POST /api/auth/login` | `{email,password}` | `200 {user,csrf_token}` + Cookie | 未验证返回 403；错误凭证统一 401 |
| `GET /api/auth/me` | — | `200 {mode,authenticated,user,csrf_token}` | 无会话时 `authenticated:false`，不返回 401 |
| `POST /api/auth/logout` | — | `204` | 需会话与 CSRF；撤销当前会话 |
| `POST /api/auth/forgot-password` | `{email}` | `202 {status:"accepted"}` | 不披露账号存在；限速 |
| `POST /api/auth/reset-password` | `{email,code,new_password}` | `200 {reset:true}` | 6 位验证码；单次消费；撤销该账号全部旧会话 |

`GET /api/auth/me` 的 `user` 字段为 `{user_id,email,verified}`。`mode=local` 时 `user` 为 `null`，前端直接进入工作区。

### 2.5 邮件

| 发送器 | 触发条件 | 行为 |
|---|---|---|
| SMTP | 配置 `--smtp-host` | 通过 `smtplib` 发送纯文本邮件；密码只从 `CLUELEDGER_SMTP_PASSWORD` 读取 |
| 文件投递 | 未配置 SMTP | 写入 `<state-dir>/mail-outbox/*.json`，并在日志输出验证码邮件内容（仅本地） |

邮件只包含 6 位数字验证码和有效期，不发送链接。验证码 10 分钟内有效，同一用途重新发送会替换旧码；验证码只能使用一次，连续输错 5 次后需重新发送。

### 2.6 配置项

| 参数 | 环境变量 | 说明 |
|---|---|---|
| `--mode` | `CLUELEDGER_MODE` | `local` / `server`，默认 local |
| `--database` | `CLUELEDGER_DATABASE` | server 模式必填 |
| `--public-base-url` | `CLUELEDGER_PUBLIC_BASE_URL` | 邮件链接与 Cookie Secure 判断；server 模式默认 `http://127.0.0.1:8000` 并打印警告 |
| `--allowed-origin` | `CLUELEDGER_ALLOWED_ORIGINS` | 逗号分隔的额外来源；公共地址始终允许 |
| `--mail-outbox` | `CLUELEDGER_MAIL_OUTBOX` | 文件投递目录，默认 `<state-dir>/mail-outbox` |
| `--smtp-host` / `--smtp-port` / `--smtp-username` / `--smtp-from` | `CLUELEDGER_SMTP_*` | 配置后启用 SMTP |
| — | `CLUELEDGER_SMTP_PASSWORD` | 只从环境读取，不进命令行 |
| — | `CLUELEDGER_TRUST_PROXY` | 为 `1` 时信任 `X-Forwarded-For` 第一项作为来源地址 |

### 2.7 前端消费

- 新增 `state/session.ts`：保存 `mode`、当前账号、CSRF token；启动先请求 `/api/auth/me`，server 模式未登录时整页进入认证界面，不初始化作品与模型 store。
- 所有 `/api` 客户端经统一 `apiFetch` 发出：自动带 Cookie、写请求附加 `X-CSRF-Token`、收到 401 时标记会话失效并回到登录页；草稿保留在现有 localStorage 键中。
- 验证码在账号页面内输入：注册后进入验证码页，找回密码后进入重置页；不使用链接路由，也不从 URL 读取凭证。
- 顶栏在 server 模式显示当前邮箱与「退出」；local 模式不显示账号入口。

## 三、P0 盘点证据

### 3.1 写作服务路由与全局状态

| 资源组 | 入口 | 当前范围 |
|---|---|---|
| 作品与文件 | `workspace/api.py` | 全局作品列表；任意绝对路径打开；按作品 ID 读写 |
| 正文/结构/线索 | `workspace/story_api.py`、`ideas_api.py`、`clue_api.py` | 按作品 ID，无账号检查 |
| 对话与事件 | `workspace/api.py`、`operation_api.py` | 会话按作品；操作按全局 ID |
| 模型设置 | `server/model_api.py` | 进程级选择与凭据引用 |
| 模块库与装配 | `library/api.py`、`assembly_api.py` | 私有库为全局目录 |
| 应用状态 | `workspace/state.py` | 单例 `RecentWorkspaces`，键为路径哈希 |

需要逐项加范围的对象：`RecentWorkspaces`、`RunRegistry._active`、`OperationStore`、`WorkspaceServices.turns/runners/_activity`、`ui_stores`、`ProviderBook._runtime_selection`、`ModuleLibrary`、`ProbeRunner._operations`、`ModelAuthService._sessions`、三个按裸章节/候选 ID 取消的任务表。

### 3.2 支付服务

`relay/app.py` 已有套餐、下单、订单查询、关单、退款、通知验签、回跳与权益记录；身份来自匿名 `POST /v1/session`，token 永久有效且存于浏览器 localStorage。缺少：订单创建幂等键、后台未决订单查单、用量写入、账号状态、CSRF、统一错误格式。P3 以本文账号与会话替换其身份层，P4 补计量。

### 3.3 前端调用面

| 客户端 | 路径前缀 | 需要改造 |
|---|---|---|
| `api/workspace.ts` | `/api/file-workspaces` | 统一 `apiFetch`、401 处理、SSE |
| `api/models.ts` | `/api` | 统一 `apiFetch` |
| `api/moduleLibrary.ts` | `/api/module-library`、`/api/file-workspaces` | 统一 `apiFetch` |
| `api/ideas.ts` | `/api/file-workspaces` | 统一 `apiFetch` |
| `api/activity.ts` | `/api/file-workspaces/{id}/activity` | 裸 `fetch` 与流式读取 |
| `api/operations.ts` | `/api/operations` | 统一 `apiFetch` |
| `api/clues.ts` | `/api/file-workspaces` | 唯一未封装的裸 `fetch` |

`App.tsx` 目前直接渲染 `WorkspaceShell`；P1 在它之前增加会话门。`WorkspaceShell` 初始化作品与模型 store 的时机不变，只在登录后挂载。

## 四、P1 范围与完成条件

P1 只做账号闭环，不改作品归属与计费：

1. 账号数据库、密码哈希、会话、凭证、限速与邮件投递。
2. `/api/auth/*` 八个接口；server 模式所有 `/api/*`（除公开认证与健康检查）要求会话；写请求校验来源与 CSRF。
3. server 模式启动配置与 local 模式兼容；本地运行与现有测试不要求登录。
4. 前端登录门、注册/验证/找回/重置页面、顶栏账号与退出、统一 401 处理。
5. 测试：注册与验证、未验证拒绝登录、跨客户端登录同一账号、退出后旧会话失效、重置撤销全部旧会话、重复/过期凭证失败、CSRF 拒绝、限速生效。

P1 不宣称多用户隔离完成；在 P2 通过前不开放公网写作。

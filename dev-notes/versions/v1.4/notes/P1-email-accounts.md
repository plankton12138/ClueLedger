# v1.4 P1：邮箱账号实施记录

更新：2026-11-06。状态：P1 离线与本地浏览器验证通过；真实邮件投递、公网域名与生产部署未验证。

## 一、交付范围

| 面 | 实现 |
|---|---|
| 账号存储 | `src/hastings/accounts/db.py`：共享 SQLite、WAL、外键、`BEGIN IMMEDIATE` 写事务 |
| 密码 | `passwords.py`：标准库 scrypt（N=16384、r=8、p=1），参数随哈希保存 |
| 会话 | `store.py` / `service.py`：服务端会话、可撤销、30 天有效，Cookie 名 `clue_session` |
| 验证码 | 注册验证与找回密码使用 6 位数字验证码；只存 SHA-256 哈希，10 分钟有效，连续错误 5 次失效；消费与生效在同一写事务内完成 |
| 限速 | 注册、登录、重发验证、找回密码按邮箱与来源地址固定窗口计数 |
| 邮件 | `email.py`：SMTP 发送；未配置时写入 `<state-dir>/mail-outbox/*.json` |
| 接口 | `api.py`：`/api/auth/register|verify-email|resend-verification|login|me|logout|forgot-password|reset-password` |
| 登录门 | `middleware.py`：server 模式除公开认证与健康检查外要求会话；写请求校验来源与 CSRF |
| 入口 | `server/__main__.py`：`--mode local|server`、`--database`、`--public-base-url`、邮件与来源参数 |
| 前端 | `api/http.ts`、`api/auth.ts`、`state/session.ts`、`components/auth/AuthScreen.tsx`；工作区只在登录后挂载 |
| 顶栏 | `AppHeader` 显示当前邮箱与退出；退出失败保留会话并显示原因 |

## 二、验证证据

| 项 | 结果 |
|---|---|
| 新增账号接口测试 | `tests/accounts/test_auth_api.py` 13 项通过：注册验证码、未验证拒绝登录、跨客户端登录、CSRF、登录门与公开接口的来源拒绝、退出撤销、重置撤销、过期/重复/错误次数验证码、重发替换、限速、local 模式、server 模式缺库拒绝 |
| 受影响后端测试 | `tests/accounts`、`tests/workspace/test_api.py`、`test_entry.py` 共 67 项通过 |
| 后端静态检查 | mypy 203 个源文件通过；ruff check 与 format 通过 |
| 前端测试 | 339 项单测通过；`npm run build` 通过 |
| 本地模式浏览器 | `writing-workbench` 5 项 Playwright 用例通过，登录门未影响原有工作区 |
| server 模式浏览器 | 注册 → 文件投递取 6 位验证码 → 页面输入验证码 → 登录 → 工作区 → 退出 → 找回密码 → 输入重置验证码 → 新密码登录，全部通过；控制台无错误；`/api/auth/me` 登录后返回 CSRF，退出后 `authenticated:false` |

浏览器验证使用 `--mode server --database .pytest-tmp/... --public-base-url http://127.0.0.1:8932`，静态目录为构建后的 `frontend/dist`。

## 三、尚未完成

- P2 已实施：作品、任务、模型配置/凭据与私有模块库按账号隔离，记录见 [P2 用户隔离](P2-isolation.md)。P2 的在线模型双账号验收仍未执行，公网开放按 P5 的门槛判断。
- 真实 SMTP 送达、域名 HTTPS、Cookie `Secure` 与反向代理来源未在线上验证；当前文件投递仅用于本地与测试。
- P3 尚未把 relay 的订单与权益接到本账号 `user_id`，匿名 `POST /v1/session` 仍在。
- P4 尚未实现用量预留、结算与订阅模型代理。
- 公开认证接口目前只做来源校验与限速，缺少统一 422 校验错误格式；P2 整理错误契约时一并处理。

# v1.4 P3：账号订单、权益与订阅前端

更新：2026-11-06。状态：离线测试与本地浏览器购买流程通过；真实支付宝沙箱/生产交易、线上反向代理与部署验收未执行。

## 一、交付范围

| 面 | 实现 |
|---|---|
| 统一数据库 | relay 改用 `AccountDatabase`；订单、权益、通知、退款与用量表引用 `accounts.user_id`，不再创建 relay 私有匿名账号 |
| 身份 | 支付服务读取写作服务的 `clue_session` Cookie，并按 `auth_sessions` 校验；写请求校验来源与 `X-CSRF-Token` |
| 匿名入口 | 删除 `POST /v1/session` 与独立购买页；`/purchase` 只跳回网站；旧 `relay.sqlite3` 不迁移 |
| 下单 | `POST /api/billing/orders` 只收 `plan_code` 与 `request_id`；金额、天数与额度来自服务端套餐表；`(user_id, request_id)` 唯一 |
| 体验卡 | 每账号限购一次；存在未关闭的体验卡订单时拒绝再次购买 |
| 订单读取 | `GET /api/billing/orders/{id}`、`POST .../query` 只返回当前账号订单；支付人 `buyer_id` 不影响归属 |
| 通知 | 验签、应用、金额、收款方、通知 ID 与交易状态校验；`notify_id` 唯一，重复/乱序不重复发放权益；已退款订单不再重发 |
| 权益 | 订单状态与权益记录在同一写事务内更新；续费从现有有效期末尾继续累加；全量退款撤销对应权益 |
| 查单恢复 | 主动查询更新订单与权益；后台 reconciler 按间隔扫描未决订单，失败记录次数与错误，关闭页面也能收敛 |
| 回跳 | 回跳页只显示状态；签名订单号可查询状态，但不建立会话、不发权益、不写 Cookie |
| 退款 | 用户侧仅提交 `refund-request`；运营用 `X-Admin-Token` 执行 `/v1/orders/{id}/refund` 与退款查询 |
| 网站入口 | 顶栏「订阅」；套餐卡、权益摘要、最近订单、支付结果查询与退款申请；待查订单号不是登录凭证，退出时清除 |
| 本地代理 | 写作服务在配置 `--billing-url` 时提供受登录门保护的 `/api/billing` 反向代理；Vite 与生产 nginx 也可直接转发同一路径 |

主要入口：[relay/service.py](../../../src/hastings/relay/service.py)、[relay/store.py](../../../src/hastings/relay/store.py)、[relay/app.py](../../../src/hastings/relay/app.py)、[SubscriptionDialog.tsx](../../../frontend/src/components/billing/SubscriptionDialog.tsx)。

## 二、验证证据

| 项 | 结果 |
|---|---|
| 支付测试 | 10 项通过：公开套餐、未登录拒绝、同请求幂等与跨账号 404、体验卡限购、通知只发一次且代付不改归属、金额/验签失败、主动查单与后台 reconciler、用户退款申请与运营退款撤销、回跳无 Cookie、运营关单、统一数据库配置 |
| 受影响后端 | `tests/relay`、`tests/accounts`、`tests/workspace/test_entry.py` 共 43 项通过 |
| 静态检查 | mypy 172 个源文件通过；ruff check/format 通过 |
| 前端 | 339 项单测与 `npm run build` 通过 |
| 本地模式浏览器 | `writing-workbench` 5 项 Playwright 通过，订阅入口在 local 模式不显示 |
| server 模式浏览器 | A 登录 → 订阅 → 购买体验卡 → 查询支付结果 → 权益生效、顶栏显示「订阅有效」；退出后 B 登录看到「尚未订阅」、三档套餐且看不到 A 的订单；控制台无错误 |

浏览器验证使用写作服务 + 独立 relay + Vite 代理 + 假支付网关（仅 QA 脚本，不进入产品代码），真实支付宝未调用。

## 三、尚未完成与边界

- 未调用真实支付宝沙箱或生产网关；正式应用、密钥、`ALIPAY_NOTIFY_URL`、HTTPS 回跳与商户开通属于 P5。
- 生产反向代理需把 `/api/billing/*` 转到支付服务，并保持 Cookie 同域；当前仅 Vite 开发代理配置。
- 部分退款不撤销权益，只有与订单金额相等的全量退款会结束对应权益；退款政策需在上线前确认并展示。
- 后台 reconciler 默认 60 秒一次，测试直接调用单次收敛；未见真实支付宝长时间未决订单。
- 没有订单列表、运营后台页面与退款请求队列 UI；用户侧只显示最近一笔订单。
- 用量页面仍返回权益摘要；真正的预留、扣点与代理结算已在 [P4](P4-metering.md) 实现。

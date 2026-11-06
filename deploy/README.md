# 线簿 / ClueLedger 生产部署

更新：2026-11-06。本文给出单台 Linux 服务器、单域名 HTTPS、写作与支付分进程的部署方式。实际域名、证书、支付宝产品开通、SMTP 送达与恢复演练尚未在本仓库执行；按本文操作后仍以 P6 验收结果为准。

## 一、目标形态

```
浏览器
  │  HTTPS 443
  ▼
nginx（唯一公网入口）
  ├─ /api/billing/*、/notify/alipay、/return/alipay → 127.0.0.1:8300（relay）
  └─ 其余 /api/*、静态资源、SPA              → 127.0.0.1:8000（writing）
```

- 两个应用都只监听回环地址；`/v1/*` 内部模型代理与运营接口不对外，nginx 显式返回 404。
- 单机 SQLite，不启动多个应用副本；写作、支付、模型代理共享 `/var/lib/cluleledger/clueledger.sqlite3`。
- 浏览器只持有 `clue_session` Cookie；平台模型密钥与支付宝密钥只存在服务器进程/文件中。

## 二、服务器与目录

以 `/opt/cluleledger` 放代码与虚拟环境，`/var/lib/cluleledger` 放数据，`/etc/cluleledger` 放密钥：

```bash
sudo useradd --system --home /opt/cluleledger --shell /usr/sbin/nologin clueledger
sudo mkdir -p /opt/cluleledger /var/lib/cluleledger /etc/cluleledger /var/backups/cluleledger
sudo chown -R clueledger:clueledger /opt/cluleledger /var/lib/cluleledger /var/backups/cluleledger
sudo chown root:clueledger /etc/cluleledger
sudo chmod 750 /etc/cluleledger
```

代码更新后：

```bash
cd /opt/cluleledger
sudo -u clueledger python3 -m venv .venv
sudo -u clueledger .venv/bin/pip install -e .
cd frontend && sudo -u clueledger npm ci && sudo -u clueledger npm run build
```

生产只用 `frontend/dist`，不运行 Vite 开发服务器。

## 三、密钥与配置

```bash
sudo deploy/generate-credential-key.sh /etc/cluleledger/credential.key
openssl rand -base64 32 | sudo tee /etc/cluleledger/billing-internal.secret >/dev/null
openssl rand -base64 32 | sudo tee /etc/cluleledger/relay-admin.token >/dev/null
sudo chmod 600 /etc/cluleledger/*.key /etc/cluleledger/*.secret /etc/cluleledger/*.token
sudo chown root:clueledger /etc/cluleledger/*
```

- `credential.key` 是作者自带模型 API Key 的加密主密钥，必须与数据库分开保管，并单独离线备份；丢失后已有凭据无法解密。
- `billing-internal.secret` 同时供写作服务签发订阅 grant 与 relay 验证，两个环境文件必须一致。
- 将 `deploy/env.server.example`、`deploy/env.relay.example` 复制到 `/etc/cluleledger/server.env`、`relay.env`，替换域名与 SMTP/支付宝配置；`chmod 600`。
- 支付宝应用私钥、公钥、平台模型密钥、SMTP 密码、管理令牌支持 `*_FILE` 形式，避免密钥出现在进程参数或环境变量值里。

生产启动会拒绝以下缺项：非 HTTPS 公共地址、缺账号库/作品根/私有库根、缺凭据主密钥、缺 SMTP、缺订阅代理地址或内部密钥、缺 `--trust-proxy`。relay 生产模式也会拒绝沙箱网关、非显式数据库、缺管理令牌或内部密钥。

## 四、进程

```bash
sudo cp deploy/systemd/cluleledger-server.service /etc/systemd/system/
sudo cp deploy/systemd/cluleledger-relay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cluleledger-server cluleledger-relay
```

两个单元都设置 `Restart=always`、`UMask=0077`、`ProtectSystem=strict`，只允许写 `/var/lib/cluleledger`。配置改动后：

```bash
sudo systemctl restart cluleledger-server cluleledger-relay
```

## 五、nginx 与 HTTPS

```bash
sudo apt install nginx certbot python3-certbot-nginx
sudo mkdir -p /etc/nginx/snippets
sudo cp deploy/nginx/cluleledger-proxy.conf /etc/nginx/snippets/
sudo cp deploy/nginx/cluleledger.conf /etc/nginx/sites-available/cluleledger
sudo ln -s /etc/nginx/sites-available/cluleledger /etc/nginx/sites-enabled/cluleledger
# 编辑 server_name 与证书路径，按实际域名替换 clue.example.com
sudo certbot --nginx -d clue.example.com
sudo nginx -t && sudo systemctl reload nginx
```

防火墙只开放 80/443；8000/8300 不得对公网开放。nginx 已为 SSE 路径关闭缓冲并把读超时放宽到 1 小时，同时透传 `X-Request-ID`、`X-Forwarded-For`、`X-Forwarded-Proto`。

## 六、邮件与支付宝

- SMTP：配置专用发信域与 SPF/DKIM/DMARC；先用真实邮箱验证注册、找回和重发验证。生产模式不提供文件投递回退。
- 支付宝：在商家平台开通 AI 网页应用收款（`FAST_INSTANT_TRADE_PAY`），配置应用公钥、支付宝公钥、`ALIPAY_SELLER_ID`、HTTPS 回跳和通知地址。通知地址指向 `https://<域名>/notify/alipay`，回跳指向 `/return/alipay`。
- `RELAY_RECONCILE_SECONDS` 控制未决订单后台查单间隔；为 0 只保留启动时恢复。生产保持开启，关闭浏览器也能确认支付。

## 七、健康检查与日志

```bash
curl -fsS https://clue.example.com/api/health
curl -fsS http://127.0.0.1:8300/health
sudo journalctl -u cluleledger-server -u cluleledger-relay -f
```

- 两个进程默认 `CLUELEDGER_LOG_JSON=1` / `RELAY_ENV=production` 输出 JSON；访问日志包含 `request_id/method/path/status/duration_ms/user_id`，不含查询串、Cookie、正文或密钥。
- 上游模型、支付通知、后台查单与任务失败写异常日志；用 `request_id` 关联 nginx 与应用日志。
- 账号库不可用时健康检查返回 503 `degraded`。

## 八、备份与恢复

```bash
# 在线备份；严格一致时 PAUSE_SERVICES=1（会短暂停止两个服务）
sudo PAUSE_SERVICES=1 /opt/cluleledger/deploy/backup.sh
# 定时任务示例：每天 03:20
20 3 * * * root PAUSE_SERVICES=1 /opt/cluleledger/deploy/backup.sh
```

- 备份包含数据库一致性快照、作品、私有模块库、应用状态（会话/操作/附件）、加密凭据文件；主密钥单独输出为 `*.credential.key`，不要与归档放在同一介质。
- 恢复前确认主密钥与备份匹配；`deploy/restore.sh` 会先把当前数据移到 `before-restore-*`，校验数据库完整性后再启动并检查健康。上线前至少在独立目录完成一次恢复演练。

## 九、上线前检查

| 项 | 要求 |
|---|---|
| 域名与证书 | 正式域名 HTTPS、证书自动续期；HTTP 跳转 HTTPS |
| 端口 | 8000/8300 只监听 127.0.0.1；`/v1/*` 公网 404 |
| 配置 | 生产模式启动无缺项；不回退沙箱或本地状态目录 |
| 邮件 | 注册、验证、找回、重发在目标邮箱实际送达 |
| 支付 | 正式应用、商户、回跳、通知、主动查单与退款核验 |
| 数据 | 备份可恢复、主密钥独立保存、恢复演练记录 |
| 合规 | 按实际经营场景完成备案、隐私/服务条款与退款规则 |

## 十、本仓库未验证

正式域名、证书、云主机、SMTP 送达、支付宝产品开通与真实交易、备份恢复演练均未在本次开发中执行；本目录提供配置与脚本，不能替代 P6 的实际证据。

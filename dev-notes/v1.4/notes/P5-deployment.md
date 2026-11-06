# v1.4 P5：生产配置、部署与运维

更新：2026-11-06。状态：生产配置校验、加密凭据、日志、健康检查、nginx/systemd 与备份恢复脚本已实现并通过离线测试；真实域名、HTTPS、SMTP 送达、支付宝开通与恢复演练未执行。

## 一、交付范围

| 面 | 实现 |
|---|---|
| 生产校验 | 写作服务 `--production` 要求 HTTPS 公共地址、显式数据库/作品根/私有库根、凭据主密钥、SMTP、订阅代理与内部密钥、`--trust-proxy`；缺项一次列全并拒绝启动 |
| 支付校验 | relay `--production` / `RELAY_ENV=production` 拒绝沙箱、本地默认数据库、非 HTTPS 站点地址、缺管理员令牌或内部密钥 |
| 凭据存储 | `EncryptedFileCredentialStore` 使用 AES-GCM，主密钥从独立文件读取；服务器不再依赖桌面 keyring，也不会重启后静默丢 Key |
| 密钥来源 | 支付宝私钥/公钥、平台模型 Key、管理令牌、内部密钥、SMTP 密码支持 `*_FILE`，避免密钥出现在进程参数 |
| 反向代理 | `deploy/nginx/cluleledger.conf`：单域名 HTTPS；账单/通知/回跳转 relay，其余转写作服务；`/v1/*` 公网 404；SSE 关闭缓冲；透传 Host、XFF、协议与请求编号 |
| 进程 | systemd 两个单元，写作 8000、relay 8300 只监听回环；自动重启、严格文件系统权限、仅数据目录可写 |
| 日志 | JSON 访问日志，包含 request_id、方法、路径、状态、耗时与 user_id；不记录查询串、Cookie、正文或密钥；支持上游 X-Request-ID 透传 |
| 健康检查 | 写作 `/api/health` 与 relay `/health` 检查数据库；不可用时返回 503 `degraded` |
| 备份恢复 | SQLite 一致性快照、作品/私有库/状态/加密凭据复制，主密钥单独输出；恢复前校验数据库完整性并保留旧数据目录 |
| 部署文档 | `deploy/README.md`：目录、密钥、环境文件、进程、nginx/证书、SMTP/支付宝、日志、备份恢复与上线检查清单 |

## 二、验证证据

| 项 | 结果 |
|---|---|
| 生产配置测试 | 5 项：缺项聚合、生产启动拒绝缺项/非 HTTPS、加密凭据往返与错误主密钥、访问日志关联且不含查询串、健康检查 |
| 支付生产测试 | 3 项：拒绝沙箱回退、缺显式数据库/管理令牌/内部密钥拒绝、HTTPS 与 `*_FILE` 密钥读取通过 |
| 后端回归 | `tests/accounts` 27 项、`tests/relay` 25 项、`test_entry` 15 项，共 67 项通过 |
| 静态检查 | mypy 178 个源文件通过；ruff check/format 通过；`uv lock --check` 通过 |
| 前端 | 339 项单测与 `npm run build` 通过 |
| 脚本 | `bash -n deploy/backup.sh deploy/restore.sh deploy/generate-credential-key.sh` 通过 |

本机没有安装 nginx，未执行 `nginx -t`；没有真实域名、证书、SMTP 服务器或支付宝生产配置，未执行部署。

## 三、未完成与边界

- 未在真实 Linux 服务器安装 nginx/systemd 并启动；未验证证书、HTTP 跳转、SSE 代理、端口暴露与 `X-Forwarded-For` 实际取值。
- 未向真实邮箱发送验证/找回邮件；SMTP 送达、SPF/DKIM/DMARC 与退信处理待验证。
- 未完成支付宝 AI 网页应用收款的产品开通、正式密钥、通知/回跳与真实交易；沙箱通过不等于生产收款成功。
- 未执行备份恢复演练；主密钥单独备份与恢复必须在上线前实际演练一次。
- 域名、云主机、地域、邮件服务商与备案材料尚未选定；本目录不代表采购或开通授权。
- 生产凭据文件按用户 API Key 加密；尚未做主密钥轮换。轮换需要离线迁移凭据文件并重启。
- 本地直接由写作服务托管构建前端时，`--billing-url` 会挂一个受登录门保护的 `/api/billing` 反向代理；生产仍建议由 nginx 转发，避免写作进程转发账单。

#!/usr/bin/env bash
# 备份线簿的数据：账号/订单数据库使用 SQLite 备份 API 生成一致快照，
# 作品、私有模块库、应用状态与加密凭据文件按目录复制。
# 默认在线备份；要求严格一致时设置 PAUSE_SERVICES=1 暂停两个服务。
set -euo pipefail

APP_NAME="${APP_NAME:-cluleledger}"
DB_PATH="${DB_PATH:-/var/lib/cluleledger/clueledger.sqlite3}"
WORKSPACES_ROOT="${WORKSPACES_ROOT:-/var/lib/cluleledger/workspaces}"
LIBRARIES_ROOT="${LIBRARIES_ROOT:-/var/lib/cluleledger/libraries}"
STATE_ROOT="${STATE_ROOT:-/var/lib/cluleledger/state}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-/var/lib/cluleledger/credentials.vault}"
CREDENTIAL_KEY_FILE="${CREDENTIAL_KEY_FILE:-/etc/cluleledger/credential.key}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/cluleledger}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
PAUSE_SERVICES="${PAUSE_SERVICES:-0}"

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
destination="$BACKUP_ROOT/$stamp"
mkdir -p "$destination"

started_services=0
if [[ "$PAUSE_SERVICES" == "1" ]]; then
  systemctl stop "${APP_NAME}-relay.service" "${APP_NAME}-server.service" || true
  started_services=1
  trap 'systemctl start "${APP_NAME}-server.service" "${APP_NAME}-relay.service" || true' EXIT
fi

python3 - "$DB_PATH" "$destination/clueledger.sqlite3" <<'PY'
import sqlite3
import sys

source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
with target:
    source.backup(target)
target.close()
source.close()
PY

for pair in \
  "$WORKSPACES_ROOT:workspaces" \
  "$LIBRARIES_ROOT:libraries" \
  "$STATE_ROOT:state"; do
  source="${pair%%:*}"
  name="${pair##*:}"
  if [[ -d "$source" ]]; then
    mkdir -p "$destination/$name"
    rsync -a --numeric-ids "$source/" "$destination/$name/"
  fi
done

if [[ -f "$CREDENTIALS_FILE" ]]; then
  cp --preserve=mode,timestamps "$CREDENTIALS_FILE" "$destination/credentials.vault"
fi

# 主密钥单独放在归档外，避免与数据库同包；请把它存到另一处离线介质。
if [[ -f "$CREDENTIAL_KEY_FILE" ]]; then
  cp --preserve=mode,timestamps "$CREDENTIAL_KEY_FILE" "$BACKUP_ROOT/$stamp.credential.key"
  chmod 600 "$BACKUP_ROOT/$stamp.credential.key"
fi

tar -C "$BACKUP_ROOT" -czf "$BACKUP_ROOT/$stamp.tar.gz" "$stamp"
sha256sum "$BACKUP_ROOT/$stamp.tar.gz" >"$BACKUP_ROOT/$stamp.tar.gz.sha256"
rm -rf "$destination"

find "$BACKUP_ROOT" -maxdepth 1 -type f -name '*.tar.gz' -mtime "+$RETENTION_DAYS" -delete
find "$BACKUP_ROOT" -maxdepth 1 -type f -name '*.sha256' -mtime "+$RETENTION_DAYS" -delete

if [[ "$started_services" == "1" ]]; then
  systemctl start "${APP_NAME}-server.service" "${APP_NAME}-relay.service"
  trap - EXIT
fi

echo "备份完成：$BACKUP_ROOT/$stamp.tar.gz"
echo "主密钥备份：$BACKUP_ROOT/$stamp.credential.key（请单独离线保存）"

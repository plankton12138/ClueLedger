#!/usr/bin/env bash
# 从 backup.sh 的归档恢复。恢复前会把当前数据移到 .before-restore-<时间戳>，不直接删除。
# 主密钥不随归档恢复；需先用 CREDENTIAL_KEY_FILE 指定与备份匹配的密钥。
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "用法：$0 <备份.tar.gz>" >&2
  exit 2
fi

ARCHIVE="$1"
DATA_ROOT="${DATA_ROOT:-/var/lib/cluleledger}"
DB_PATH="${DB_PATH:-$DATA_ROOT/clueledger.sqlite3}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-$DATA_ROOT/credentials.vault}"
CREDENTIAL_KEY_FILE="${CREDENTIAL_KEY_FILE:-/etc/cluleledger/credential.key}"
APP_NAME="${APP_NAME:-cluleledger}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

[[ -f "$ARCHIVE" ]] || { echo "归档不存在：$ARCHIVE" >&2; exit 1; }
[[ -f "$CREDENTIAL_KEY_FILE" ]] || { echo "缺少主密钥：$CREDENTIAL_KEY_FILE" >&2; exit 1; }

tar -xzf "$ARCHIVE" -C "$WORK"
ROOT="$(find "$WORK" -maxdepth 1 -mindepth 1 -type d | head -n 1)"
[[ -n "$ROOT" ]] || { echo "归档结构不正确" >&2; exit 1; }
python3 - "$ROOT/clueledger.sqlite3" <<'PY'
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1])
result = connection.execute("PRAGMA integrity_check").fetchone()
connection.close()
if not result or result[0] != "ok":
    raise SystemExit(f"数据库完整性检查失败：{result}")
PY

systemctl stop "${APP_NAME}-server.service" "${APP_NAME}-relay.service" || true

backup_dir="$DATA_ROOT/before-restore-$STAMP"
mkdir -p "$backup_dir"
for name in workspaces libraries state; do
  if [[ -e "$DATA_ROOT/$name" ]]; then mv "$DATA_ROOT/$name" "$backup_dir/$name"; fi
done
if [[ -e "$DB_PATH" ]]; then mv "$DB_PATH" "$backup_dir/clueledger.sqlite3"; fi
for suffix in -wal -shm; do
  if [[ -e "$DB_PATH$suffix" ]]; then mv "$DB_PATH$suffix" "$backup_dir/clueledger.sqlite3$suffix"; fi
done
if [[ -e "$CREDENTIALS_FILE" ]]; then mv "$CREDENTIALS_FILE" "$backup_dir/credentials.vault"; fi

mkdir -p "$DATA_ROOT"
cp "$ROOT/clueledger.sqlite3" "$DB_PATH"
for name in workspaces libraries state; do
  if [[ -d "$ROOT/$name" ]]; then cp -a "$ROOT/$name" "$DATA_ROOT/$name"; fi
done
if [[ -f "$ROOT/credentials.vault" ]]; then cp -a "$ROOT/credentials.vault" "$CREDENTIALS_FILE"; fi
chown -R "${SERVICE_USER:-clueledger}:${SERVICE_USER:-clueledger}" "$DATA_ROOT" || true
chmod 700 "$DATA_ROOT"
chmod 600 "$DB_PATH" "$CREDENTIALS_FILE" 2>/dev/null || true

systemctl start "${APP_NAME}-server.service" "${APP_NAME}-relay.service"
sleep 2
curl -fsS "${PUBLIC_BASE_URL:-https://clue.example.com}/api/health" >/dev/null
curl -fsS "http://127.0.0.1:8300/health" >/dev/null
echo "恢复完成。旧数据保留在：$backup_dir"

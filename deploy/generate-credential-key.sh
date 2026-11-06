#!/usr/bin/env bash
# 生成服务器凭据加密主密钥；输出到参数指定的文件（默认 /etc/cluleledger/credential.key）。
# 该文件必须与数据库分开保管，并独立备份；丢失后已有 API Key 无法解密。
set -euo pipefail
target="${1:-/etc/cluleledger/credential.key}"
umask 077
python3 -c 'import base64, os; print(base64.b64encode(os.urandom(32)).decode())' >"$target"
chmod 600 "$target"
echo "已生成 32 字节主密钥：$target"

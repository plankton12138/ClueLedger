"""应用层端口。

0.4 只保留凭据存储端口：模型档案在 `server/provider_config.py` 与
`infrastructure/credentials.py` 里流转，其余 0.3 的端口已随旧链路删除。
"""

from __future__ import annotations

from hastings.application.ports.credentials import CredentialStore

__all__ = ["CredentialStore"]

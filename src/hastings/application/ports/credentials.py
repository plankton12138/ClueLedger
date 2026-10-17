"""凭据存储端口（M02/V03-1.3）：桌面凭据走操作系统凭据存储。

连接元数据只存 ``credential_ref``（config.yaml 不再保存明文 Key）；
实际密钥经本端口读写。升级顺序（计划 §4.1）：
新存储写入并验证 → 元数据切换 → 清理旧 Key 字段；失败保持旧连接
可恢复并明确提示——见 ``provider_config.migrate_plaintext_credentials``。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["CredentialStore"]


@runtime_checkable
class CredentialStore(Protocol):
    """操作系统凭据存储的最小端口（keyring / Windows 凭据管理器等）。"""

    def save(self, ref: str, secret: str) -> None:
        """写入或覆盖 ref 对应的密钥；失败抛异常（调用方保持旧连接）。"""

    def load(self, ref: str) -> str | None:
        """读取 ref 对应的密钥；不存在返回 None（合法空结果）。"""

    def delete(self, ref: str) -> None:
        """删除 ref；不存在时静默成功。"""

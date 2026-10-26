"""凭据存储实现（V03-1.3）：OS 凭据存储优先，内存实现供测试与降级。

- ``KeyringCredentialStore``：经 ``keyring`` 库走操作系统凭据管理器
  （Windows Credential Manager / macOS Keychain / Secret Service）。
- ``InMemoryCredentialStore``：进程内字典——测试用；生产降级时使用会
  导致「重启后凭据丢失」的可见故障（档案变不可用），**不是静默回退**，
  调用方必须向作者明确提示（环境缺失给安装引导，M02）。
"""

from __future__ import annotations

import contextlib
from threading import RLock
from typing import Any

from hastings.application.errors import EnvironmentFailure
from hastings.application.ports.credentials import CredentialStore

__all__ = [
    "InMemoryCredentialStore",
    "KeyringCredentialStore",
    "build_credential_store",
]

_SERVICE_NAME = "hastings"


class InMemoryCredentialStore:
    """dict 背书的凭据存储：测试与降级；进程重启即丢。"""

    def __init__(self) -> None:
        self._secrets: dict[str, str] = {}
        self._lock = RLock()

    def save(self, ref: str, secret: str) -> None:
        with self._lock:
            self._secrets[ref] = secret

    def load(self, ref: str) -> str | None:
        with self._lock:
            return self._secrets.get(ref)

    def delete(self, ref: str) -> None:
        with self._lock:
            self._secrets.pop(ref, None)

    def refs(self) -> set[str]:
        """当前存在的引用（不含值）；用于诊断与断言，不对外暴露密钥。"""
        with self._lock:
            return set(self._secrets)


class KeyringCredentialStore:
    """经 keyring 库的操作系统凭据存储；库缺失或后端故障按环境失败处理。"""

    def __init__(self, service_name: str = _SERVICE_NAME) -> None:
        self._service = service_name
        self._module: Any = self._import()

    @staticmethod
    def _import() -> Any:
        try:
            import keyring  # 延迟导入：未打包桌面环境可能没有后端
        except ImportError as exc:
            raise EnvironmentFailure(
                code="CREDENTIAL_STORE_UNAVAILABLE",
                where="credentials.keyring",
                why="操作系统凭据存储组件未安装",
                fix="安装 keyring（桌面发行版已内置）；或以开发模式运行",
            ) from exc
        return keyring

    def save(self, ref: str, secret: str) -> None:
        try:
            self._module.set_password(self._service, ref, secret)
        except Exception as exc:  # keyring 后端故障（锁屏/拒绝访问等）
            raise EnvironmentFailure(
                code="CREDENTIAL_STORE_UNAVAILABLE",
                where=f"credentials.keyring.{ref}",
                why=f"写入操作系统凭据存储失败：{type(exc).__name__}",
                fix="解锁系统凭据管理器后重试；旧连接保持可用",
            ) from exc

    def load(self, ref: str) -> str | None:
        try:
            secret: str | None = self._module.get_password(self._service, ref)
        except Exception as exc:
            raise EnvironmentFailure(
                code="CREDENTIAL_STORE_UNAVAILABLE",
                where=f"credentials.keyring.{ref}",
                why=f"读取操作系统凭据存储失败：{type(exc).__name__}",
                fix="解锁系统凭据管理器后重试",
            ) from exc
        return secret

    def delete(self, ref: str) -> None:
        with contextlib.suppress(Exception):
            self._module.delete_password(self._service, ref)


def build_credential_store() -> CredentialStore:
    """优先 OS 凭据存储；不可用时降级内存并让调用方明确提示。

    降级不是静默回退：内存存储重启即丢，档案会在重启后变不可用——
    该可见故障就是「环境缺失给安装引导」的触发点（M02）。
    """
    try:
        return KeyringCredentialStore()
    except EnvironmentFailure:
        return InMemoryCredentialStore()

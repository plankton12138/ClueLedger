"""凭据存储实现（V03-1.3）：OS 凭据存储优先，内存实现供测试与降级。

- ``KeyringCredentialStore``：经 ``keyring`` 库走操作系统凭据管理器
  （Windows Credential Manager / macOS Keychain / Secret Service）。
- ``InMemoryCredentialStore``：进程内字典——测试用；生产降级时使用会
  导致「重启后凭据丢失」的可见故障（档案变不可用），**不是静默回退**，
  调用方必须向作者明确提示（环境缺失给安装引导，M02）。
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any

from hastings.application.errors import EnvironmentFailure
from hastings.application.ports.credentials import CredentialStore

__all__ = [
    "CredentialStoreError",
    "EncryptedFileCredentialStore",
    "InMemoryCredentialStore",
    "KeyringCredentialStore",
    "ScopedCredentialStore",
    "build_credential_store",
    "load_master_key",
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


class CredentialStoreError(RuntimeError):
    """服务器凭据文件损坏、主密钥不匹配或无法解密。"""


def load_master_key(path: Path) -> bytes:
    """读取 32 字节主密钥；文件可以是原始字节或 base64 文本。"""
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        raise CredentialStoreError(f"无法读取凭据主密钥：{path}") from exc
    if len(raw) == 32:
        return raw
    try:
        decoded = base64.b64decode(raw.strip(), validate=True)
    except Exception as exc:
        raise CredentialStoreError(
            "凭据主密钥必须是 32 字节原始数据或 base64 编码的 32 字节"
        ) from exc
    if len(decoded) != 32:
        raise CredentialStoreError("凭据主密钥长度必须是 32 字节")
    return decoded


class EncryptedFileCredentialStore:
    """服务器侧 AES-GCM 凭据文件；主密钥从独立文件读取，不随数据库一起落盘。"""

    def __init__(self, path: Path, key: bytes) -> None:
        if len(key) != 32:
            raise CredentialStoreError("凭据主密钥长度必须是 32 字节")
        self.path = Path(path)
        self._key = key
        self._lock = RLock()

    def _read(self) -> dict[str, dict[str, str]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise CredentialStoreError(f"凭据文件无法读取：{self.path}") from exc
        items = raw.get("items") if isinstance(raw, dict) else None
        if not isinstance(items, dict):
            raise CredentialStoreError(f"凭据文件结构损坏：{self.path}")
        result: dict[str, dict[str, str]] = {}
        for ref, item in items.items():
            if isinstance(ref, str) and isinstance(item, dict):
                result[ref] = {str(k): str(v) for k, v in item.items()}
        return result

    def _write(self, items: dict[str, dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "items": items}
        handle, temp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=f".{self.path.name}-", suffix=".tmp"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, sort_keys=True)
                stream.write("\n")
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, self.path)
        except OSError as exc:
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
            raise CredentialStoreError(f"凭据文件无法写入：{self.path}") from exc

    @staticmethod
    def _cipher(key: bytes, nonce: bytes) -> Any:
        from Crypto.Cipher import AES  # 延迟导入：本地模式不依赖加密组件

        return AES.new(key, AES.MODE_GCM, nonce=nonce)

    def save(self, ref: str, secret: str) -> None:
        with self._lock:
            items = self._read()
            nonce = os.urandom(12)
            cipher = self._cipher(self._key, nonce)
            cipher.update(ref.encode("utf-8"))
            ciphertext, tag = cipher.encrypt_and_digest(secret.encode("utf-8"))
            items[ref] = {"value": base64.b64encode(nonce + tag + ciphertext).decode("ascii")}
            self._write(items)

    def load(self, ref: str) -> str | None:
        with self._lock:
            items = self._read()
            item = items.get(ref)
            if item is None:
                return None
            try:
                packed = base64.b64decode(item["value"], validate=True)
                nonce, tag, ciphertext = packed[:12], packed[12:28], packed[28:]
                cipher = self._cipher(self._key, nonce)
                cipher.update(ref.encode("utf-8"))
                plaintext: bytes = cipher.decrypt_and_verify(ciphertext, tag)
                return plaintext.decode("utf-8")
            except Exception as exc:
                raise CredentialStoreError(f"凭据解密失败：{ref}") from exc

    def delete(self, ref: str) -> None:
        with self._lock:
            items = self._read()
            if ref not in items:
                return
            items.pop(ref)
            self._write(items)

    def refs(self) -> set[str]:
        with self._lock:
            return set(self._read())


class ScopedCredentialStore:
    """给同一台机器上的多个账号加命名空间；底层仍是系统凭据存储。

    所有引用在读写前加前缀，账号之间即使使用相同连接名也不会命中同一条密钥。
    """

    def __init__(self, base: CredentialStore, prefix: str) -> None:
        self._base = base
        self._prefix = prefix

    def _scoped(self, ref: str) -> str:
        return f"{self._prefix}{ref}"

    def save(self, ref: str, secret: str) -> None:
        self._base.save(self._scoped(ref), secret)

    def load(self, ref: str) -> str | None:
        return self._base.load(self._scoped(ref))

    def delete(self, ref: str) -> None:
        self._base.delete(self._scoped(ref))


def build_credential_store(
    *, key_file: Path | None = None, credentials_file: Path | None = None
) -> CredentialStore:
    """本地优先 OS 凭据存储；服务器显式给出主密钥时使用加密文件。

    服务器不能依赖桌面 keyring：无桌面会话时降级到内存会让作者凭据在重启后丢失。
    生产入口要求 ``key_file``，主密钥文件与数据库分开放置。
    """
    if key_file is not None:
        path = (
            Path(credentials_file)
            if credentials_file is not None
            else Path(key_file).with_suffix(".vault")
        )
        return EncryptedFileCredentialStore(path, load_master_key(key_file))
    try:
        return KeyringCredentialStore()
    except EnvironmentFailure:
        return InMemoryCredentialStore()

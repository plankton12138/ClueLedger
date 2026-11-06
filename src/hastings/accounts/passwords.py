"""密码哈希：标准库 scrypt，参数随哈希一起保存。

不引入额外依赖，也不自写算法。参数写在编码里，未来调高成本时旧哈希仍可校验，
登录成功后再按需升级不在第一版范围内。
"""

from __future__ import annotations

import base64
import hashlib
import secrets

__all__ = ["hash_password", "verify_password"]

_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return f"scrypt${_N}${_R}${_P}${_encode(salt)}${_encode(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_text, r_text, p_text, salt_text, digest_text = encoded.split("$")
        if algorithm != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt_text),
            n=int(n_text),
            r=int(r_text),
            p=int(p_text),
            dklen=len(_decode(digest_text)),
        )
    except (ValueError, TypeError):
        return False
    return secrets.compare_digest(candidate, _decode(digest_text))

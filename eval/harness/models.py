"""评测专用模型入口：默认读取 .env，不修改产品的凭据或配置文件。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from hastings.server.provider_catalog import ProviderApi
from hastings.server.provider_runtime import ModelSnapshot, build_provider, is_loopback_url
from hastings.workspace.service import ProviderFactory

_ENV_KEYS = {"OPENAI_API_KEY", "OPENAI_BASE_URL", "HASTINGS_MODEL"}


def online_endpoint(url: str) -> bool:
    parsed = urlsplit(url)
    host = parsed.hostname
    if parsed.scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
        return False
    if is_loopback_url(url) or host.endswith(".localhost"):
        return False
    try:
        address = ip_address(host)
    except ValueError:
        return True
    return not address.is_loopback and not address.is_unspecified


def read_env(path: Path) -> dict[str, str]:
    """只读取三个评测字段；支持单行引号与注释，不执行变量展开或修改进程环境。"""
    values = {}
    for number, original in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        line = original.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        key, separator, raw = line.partition("=")
        key = key.strip()
        if key not in _ENV_KEYS:
            continue
        if not separator or key in values:
            raise ValueError(f"模型配置第 {number} 行字段缺少等号或重复")
        raw = raw.strip()
        if raw.startswith(("'", '"')):
            match = re.fullmatch(r"""("(?:\\.|[^"\\])*"|'[^']*')\s*(?:\#.*)?""", raw)
            if match is None:
                raise ValueError(f"模型配置第 {number} 行引号未闭合或含多行值")
            quoted = match[1]
            try:
                value = json.loads(quoted) if quoted.startswith('"') else quoted[1:-1]
            except ValueError:
                raise ValueError(f"模型配置第 {number} 行引号内容格式不合法") from None
        else:
            value = re.split(r"\s+#", raw, maxsplit=1)[0].strip()
        if not value or "\n" in value or "\r" in value:
            raise ValueError(f"模型配置第 {number} 行需要非空单行值")
        values[key] = value
    return values


@dataclass
class EnvProfile:
    base_url: str
    api_key: str = field(repr=False)
    api: ProviderApi = "openai-completions"
    no_auth: bool = False

    def resolved_api_key(self) -> str:
        return self.api_key


@dataclass(frozen=True)
class EvaluationModel:
    model: str
    connection_id: str
    api: str
    source: str
    factory: ProviderFactory = field(repr=False)


def load_model(*, env_file: Path, config: Path | None, model: str | None) -> EvaluationModel:
    if config is not None:
        from hastings.infrastructure.credentials import build_credential_store
        from hastings.server.__main__ import load_provider_book
        from hastings.server.file_app import configured_provider_factory

        book, _, _ = load_provider_book(config, model, build_credential_store())
        selected = book.selection()
        profile = book.profile(selected.connection_id) if selected.connection_id else None
        if profile is None or not selected.model or not online_endpoint(profile.base_url):
            raise ValueError("在线评测需要配置文件中选择的在线模型连接")
        return EvaluationModel(
            selected.model,
            selected.connection_id or "",
            profile.api,
            "product_config",
            configured_provider_factory(book),
        )
    values = read_env(env_file)
    missing = {"OPENAI_API_KEY", "OPENAI_BASE_URL"} - values.keys()
    model_name = model or values.get("HASTINGS_MODEL", "")
    if missing or not model_name:
        raise ValueError(".env 需要 OPENAI_API_KEY、OPENAI_BASE_URL、HASTINGS_MODEL（或 --model）")
    if not online_endpoint(values["OPENAI_BASE_URL"]):
        raise ValueError(".env 中 OPENAI_BASE_URL 必须是在线端点，且不能携带 URL 用户名或密码")
    env_profile = EnvProfile(values["OPENAI_BASE_URL"], values["OPENAI_API_KEY"])

    def factory() -> ModelSnapshot:
        return ModelSnapshot(
            "eval-env", "评测在线连接", model_name, build_provider(env_profile, worker=True)
        )

    return EvaluationModel(model_name, "eval-env", env_profile.api, ".env", factory)

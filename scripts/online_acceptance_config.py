"""为真实服务与打包应用准备临时在线连接；Key 只进系统凭据存储，结束后清理。"""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

import yaml
from eval.harness.models import online_endpoint, read_env

from hastings.infrastructure.credentials import KeyringCredentialStore

PREFIX = "clueledger-acceptance-"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["create", "cleanup"])
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--env-file", type=Path, default=Path(__file__).resolve().parents[1] / ".env"
    )
    args = parser.parse_args()
    store = KeyringCredentialStore()
    if args.action == "cleanup":
        if args.config.exists():
            payload = yaml.safe_load(args.config.read_text(encoding="utf-8"))
            for profile in payload["providers"]:
                ref = profile.get("credential_ref", "")
                if not ref.startswith(PREFIX):
                    raise ValueError("只能清理本脚本创建的临时凭据")
                store.delete(ref)
            args.config.unlink()
        print("临时在线凭据已清理")
        return
    if args.config.exists():
        raise FileExistsError("配置已存在，请另选隔离路径")
    values = read_env(args.env_file)
    if not online_endpoint(values["OPENAI_BASE_URL"]):
        raise ValueError("验收必须使用在线模型")
    reference = PREFIX + uuid.uuid4().hex
    store.save(reference, values["OPENAI_API_KEY"])
    try:
        if store.load(reference) != values["OPENAI_API_KEY"]:
            raise RuntimeError("临时凭据回读校验失败")
        payload = {
            "providers": [
                {
                    "name": "acceptance-online",
                    "display_name": "在线验收",
                    "base_url": values["OPENAI_BASE_URL"],
                    "api": "openai-completions",
                    "credential_ref": reference,
                    "default_model": values["HASTINGS_MODEL"],
                }
            ],
            "active_provider": "acceptance-online",
            "active_model": values["HASTINGS_MODEL"],
            "selection_mode": "explicit",
        }
        args.config.parent.mkdir(parents=True, exist_ok=True)
        args.config.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    except BaseException:
        store.delete(reference)
        raise
    print(
        json.dumps(
            {
                "config": str(args.config.resolve()),
                "model": values["HASTINGS_MODEL"],
                "source": ".env",
                "credential_storage": "system",
                "plaintext_key_saved": False,
            }
        )
    )


if __name__ == "__main__":
    main()

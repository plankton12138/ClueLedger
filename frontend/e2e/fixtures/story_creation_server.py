"""构思创建表单的隔离在线服务：只从 .env 读模型，凭据留在内存。"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn
from eval.harness.models import online_endpoint, read_env

from hastings.infrastructure.credentials import InMemoryCredentialStore
from hastings.library.modules import ModuleLibrary
from hastings.server.file_app import (
    configured_limits_factory,
    configured_provider_factory,
    create_workspace_app,
)
from hastings.server.model_connections import ModelConnectionService
from hastings.server.provider_config import (
    ProviderBook,
    ProviderProfile,
    ServerConfig,
    save_server_config,
)
from hastings.workspace.state import RecentWorkspaces


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--static-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8950)
    args = parser.parse_args()
    repository = Path(__file__).resolve().parents[3]
    values = read_env(repository / ".env")
    if not online_endpoint(values["OPENAI_BASE_URL"]):
        raise ValueError("构思在线验证需要 .env 中的在线模型端点")
    model = values["HASTINGS_MODEL"]
    credentials = InMemoryCredentialStore()
    credentials.save("story-creation-eval", values["OPENAI_API_KEY"])
    config = ServerConfig(
        providers=[
            ProviderProfile(
                name="story-creation-eval",
                display_name="构思创建在线验证",
                base_url=values["OPENAI_BASE_URL"],
                credential_ref="story-creation-eval",
                default_model=model,
                models=[model],
            )
        ],
        selection_mode="explicit",
        active_provider="story-creation-eval",
        active_model=model,
    )
    args.root.mkdir(parents=True, exist_ok=True)
    config_path = args.root / "config.yaml"
    save_server_config(config, config_path)
    book = ProviderBook(
        config,
        boot_name="story-creation-eval",
        boot_model=model,
        config_path=config_path,
        credentials=credentials,
    )
    service = ModelConnectionService(book=book, config_path=config_path, credentials=credentials)
    app = create_workspace_app(
        workspaces=RecentWorkspaces(args.root / "state"),
        provider_factory=configured_provider_factory(book),
        limits_factory=configured_limits_factory(book),
        model_service=service,
        library=ModuleLibrary(args.root / "library"),
        static_dir=args.static_dir,
    )
    print(f"Isolated story-creation verification model: {model}", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()

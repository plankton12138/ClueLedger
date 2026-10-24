from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from eval.harness.models import load_model, online_endpoint, read_env


def test_env_file_is_literal_and_does_not_change_process_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "# online evaluation\nexport OPENAI_API_KEY='file-secret#literal'\n"
        'OPENAI_BASE_URL="https://example.invalid/v1" # comment\n'
        "HASTINGS_MODEL=test-model # comment\nIGNORED=${OTHER}\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "process-secret")
    values = read_env(path)
    assert values["OPENAI_API_KEY"] == "file-secret#literal"
    assert values["HASTINGS_MODEL"] == "test-model"
    assert os.environ["OPENAI_API_KEY"] == "process-secret"
    selected = load_model(env_file=path, config=None, model="override-model")
    assert selected.model == "override-model" and selected.source == ".env"
    assert "file-secret" not in repr(selected)
    snapshot = selected.factory()
    assert snapshot.model == "override-model"
    assert snapshot.provider._config.max_retries == 0  # type: ignore[attr-defined]
    asyncio.run(snapshot.aclose())


@pytest.mark.parametrize(
    "line",
    [
        "OPENAI_API_KEY='secret",
        'OPENAI_API_KEY="secret',
        "OPENAI_API_KEY=secret\nOPENAI_API_KEY=other",
        "OPENAI_API_KEY=",
    ],
)
def test_env_errors_do_not_echo_secret_values(tmp_path: Path, line: str) -> None:
    path = tmp_path / ".env"
    path.write_text(line, encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        read_env(path)
    assert "secret" not in str(failure.value)


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434/v1",
        "http://127.0.0.2:8000/v1",
        "http://[::1]:8000/v1",
        "http://0.0.0.0:8000/v1",
        "https://nested.localhost/v1",
        "file:///tmp/model",
        "https://user:secret@example.invalid/v1",
    ],
)
def test_online_endpoint_rejects_local_or_embedded_auth(url: str) -> None:
    assert not online_endpoint(url)


def test_missing_env_model_requires_explicit_override(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text(
        "OPENAI_API_KEY=secret\nOPENAI_BASE_URL=https://example.invalid/v1\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="HASTINGS_MODEL"):
        load_model(env_file=path, config=None, model=None)
    assert load_model(env_file=path, config=None, model="chosen").model == "chosen"

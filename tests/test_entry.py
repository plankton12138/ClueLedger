"""CLI 入口模块测试：环境变量指引与空 BASE_URL 归一化（R3 评审 #1）。"""

import os

import pytest

from hastings.__main__ import env_error, normalize_base_url


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "HASTINGS_MODEL"):
        monkeypatch.delenv(name, raising=False)


def test_env_error_lists_missing_variables(clean_env: None) -> None:
    hint = env_error(None)

    assert hint is not None
    assert "OPENAI_API_KEY" in hint
    assert "HASTINGS_MODEL" in hint


def test_env_error_none_when_complete(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("HASTINGS_MODEL", "deepseek-chat")

    assert env_error(None) is None


def test_env_error_honors_model_override(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("HASTINGS_MODEL", raising=False)

    assert env_error("deepseek-chat") is None  # --model 覆盖可替代环境变量
    assert env_error(None) is not None


def test_normalize_base_url_removes_empty(clean_env: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """模板里留空的 OPENAI_BASE_URL= 会被 load_dotenv 写成空串，必须移除，
    否则 tau 侧 environ.get(var, DEFAULT) 空串覆盖官方默认端点（R3 #1）。"""
    monkeypatch.setenv("OPENAI_BASE_URL", "")

    normalize_base_url()

    assert "OPENAI_BASE_URL" not in os.environ


def test_normalize_base_url_strips_whitespace(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "  https://api.deepseek.com/v1  ")

    normalize_base_url()

    assert os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"


def test_normalize_base_url_keeps_value_and_unset(
    clean_env: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    normalize_base_url()
    assert os.environ["OPENAI_BASE_URL"] == "https://api.deepseek.com/v1"

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    normalize_base_url()  # 未设置时不应报错、不应创建
    assert "OPENAI_BASE_URL" not in os.environ

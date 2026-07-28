"""Local LLM base URLs (LM Studio and friends) may run without an API key."""

import importlib

import pytest

from app.config import is_local_llm_url, resolve_llm_api_key


@pytest.mark.parametrize(
    "base_url",
    [
        "http://localhost:1234/v1",
        "http://127.0.0.1:1234/v1",
        "http://192.168.1.10:1234/v1",
        "http://10.0.0.5:11434/v1",
        "http://172.16.3.9:8000/v1",
        "http://host.docker.internal:1234/v1",
        "http://workstation.local:1234/v1",
        "http://[::1]:1234/v1",
    ],
)
def test_local_urls_get_a_placeholder_key(base_url):
    assert is_local_llm_url(base_url)
    assert resolve_llm_api_key(None, base_url) == "local"


@pytest.mark.parametrize(
    "base_url",
    [
        "https://api.openai.com/v1",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "http://8.8.8.8/v1",
        "",
        None,
    ],
)
def test_remote_urls_still_require_a_key(base_url):
    assert not is_local_llm_url(base_url)
    assert resolve_llm_api_key(None, base_url) is None


def test_explicit_key_always_wins():
    assert resolve_llm_api_key("sk-real", "http://localhost:1234/v1") == "sk-real"


LLM_ENV_VARS = (
    "LLM_API_KEY",
    "LLM_BASE_URL",
    "CHATBOT_LLM_API_KEY",
    "CHATBOT_LLM_BASE_URL",
)


@pytest.fixture
def config_from_env(monkeypatch):
    """Re-evaluate Config against a given environment, ignoring any local .env."""

    import app.config as config_module

    def build(**env):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: False)
        for name in LLM_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        return importlib.reload(config_module).Config

    yield build
    monkeypatch.undo()
    importlib.reload(config_module)


def test_chatbot_inherits_the_agent_key_on_a_shared_endpoint(config_from_env):
    cfg = config_from_env(
        LLM_API_KEY="sk-cloud",
        LLM_BASE_URL="https://api.openai.com/v1",
        CHATBOT_LLM_MODEL_NAME="gpt-4o-mini",
    )
    assert cfg.CHATBOT_LLM_BASE_URL == "https://api.openai.com/v1"
    assert cfg.CHATBOT_LLM_API_KEY == "sk-cloud"
    assert not [error for error in cfg.validate() if "LLM" in error]


def test_local_chatbot_with_cloud_agents_does_not_borrow_the_cloud_key(config_from_env):
    cfg = config_from_env(
        LLM_API_KEY="sk-cloud",
        LLM_BASE_URL="https://api.openai.com/v1",
        CHATBOT_LLM_BASE_URL="http://192.168.1.10:1234/v1",
    )
    assert cfg.LLM_API_KEY == "sk-cloud"
    assert cfg.CHATBOT_LLM_API_KEY == "local"


def test_cloud_chatbot_with_local_agents_does_not_inherit_the_placeholder(config_from_env):
    cfg = config_from_env(
        LLM_BASE_URL="http://localhost:1234/v1",
        CHATBOT_LLM_BASE_URL="https://api.openai.com/v1",
    )
    assert cfg.LLM_API_KEY == "local"
    assert cfg.CHATBOT_LLM_API_KEY is None
    assert any("CHATBOT_LLM_API_KEY" in error for error in cfg.validate())


def test_both_local_need_no_keys(config_from_env):
    cfg = config_from_env(
        LLM_BASE_URL="http://localhost:1234/v1",
        CHATBOT_LLM_BASE_URL="http://192.168.1.10:1234/v1",
    )
    assert cfg.LLM_API_KEY == cfg.CHATBOT_LLM_API_KEY == "local"
    assert not [error for error in cfg.validate() if "LLM" in error]

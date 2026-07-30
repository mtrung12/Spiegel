"""
The OASIS agent loop runs on its own model entry, so the pipeline's dominant
cost can sit on a cheap model while ontology, profiles and the report keep a
stronger one. Getting the fallback wrong is expensive rather than loud: the
loop silently runs every agent step on the general model.
"""

import importlib


def _reloaded_config(monkeypatch, **env):
    """Re-import Config against a controlled environment."""
    for name in (
        "LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL_NAME",
        "SIMULATION_LLM_API_KEY", "SIMULATION_LLM_BASE_URL",
        "SIMULATION_LLM_MODEL_NAME",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)

    # config.py calls load_dotenv(override=True) at import, which would stomp
    # the environment above with the developer's real .env.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)

    module = importlib.import_module("app.config")
    importlib.reload(module)
    return module


def _restore(monkeypatch):
    monkeypatch.undo()
    importlib.reload(importlib.import_module("app.config"))


def test_simulation_model_overrides_only_the_model(monkeypatch):
    config = _reloaded_config(
        monkeypatch,
        LLM_API_KEY="agent-key",
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_MODEL_NAME="gpt-4o-mini",
        SIMULATION_LLM_MODEL_NAME="gpt-5-nano",
    )
    try:
        assert config.Config.SIMULATION_LLM_MODEL_NAME == "gpt-5-nano"
        # Same provider, so the endpoint and key carry over.
        assert config.Config.SIMULATION_LLM_BASE_URL == "https://api.openai.com/v1"
        assert config.Config.SIMULATION_LLM_API_KEY == "agent-key"
        # Everything else stays on the general model.
        assert config.Config.LLM_MODEL_NAME == "gpt-4o-mini"
        assert config.Config.CHATBOT_LLM_MODEL_NAME == "gpt-4o-mini"
    finally:
        _restore(monkeypatch)


def test_simulation_config_falls_back_to_the_general_llm(monkeypatch):
    config = _reloaded_config(
        monkeypatch,
        LLM_API_KEY="agent-key",
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_MODEL_NAME="gpt-4o-mini",
    )
    try:
        assert config.Config.SIMULATION_LLM_MODEL_NAME == "gpt-4o-mini"
        assert config.Config.SIMULATION_LLM_BASE_URL == "https://api.openai.com/v1"
        assert config.Config.SIMULATION_LLM_API_KEY == "agent-key"
    finally:
        _restore(monkeypatch)


def test_a_different_endpoint_does_not_inherit_the_general_key(monkeypatch):
    config = _reloaded_config(
        monkeypatch,
        LLM_API_KEY="agent-key",
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_MODEL_NAME="gpt-4o-mini",
        SIMULATION_LLM_BASE_URL="https://other-provider.example/v1",
        SIMULATION_LLM_MODEL_NAME="cheap-model",
    )
    try:
        assert config.Config.SIMULATION_LLM_API_KEY is None
    finally:
        _restore(monkeypatch)


def test_a_local_simulation_endpoint_gets_the_placeholder_key(monkeypatch):
    config = _reloaded_config(
        monkeypatch,
        LLM_API_KEY="agent-key",
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_MODEL_NAME="gpt-4o-mini",
        SIMULATION_LLM_BASE_URL="http://192.168.1.10:1234/v1",
        SIMULATION_LLM_MODEL_NAME="qwen2.5-7b-instruct",
    )
    try:
        assert config.Config.SIMULATION_LLM_API_KEY == config.LOCAL_LLM_PLACEHOLDER_KEY
    finally:
        _restore(monkeypatch)

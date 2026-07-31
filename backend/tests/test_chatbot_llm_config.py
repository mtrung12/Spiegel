import importlib

from app.config import Config
from app.utils.llm_client import LLMClient


def test_for_chatbot_uses_the_chatbot_config(monkeypatch):
    monkeypatch.setattr(Config, "CHATBOT_LLM_API_KEY", "chatbot-key")
    monkeypatch.setattr(Config, "CHATBOT_LLM_BASE_URL", "https://chatbot.example/v1")
    monkeypatch.setattr(Config, "CHATBOT_LLM_MODEL_NAME", "fast-model")
    monkeypatch.setattr(Config, "LLM_API_KEY", "agent-key")
    monkeypatch.setattr(Config, "LLM_MODEL_NAME", "slow-model")

    chatbot = LLMClient.for_chatbot()

    assert chatbot.api_key == "chatbot-key"
    assert chatbot.base_url == "https://chatbot.example/v1"
    assert chatbot.model == "fast-model"


def test_chatbot_config_falls_back_to_the_agent_config(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "agent-key")
    monkeypatch.setenv("LLM_BASE_URL", "https://agent.example/v1")
    monkeypatch.setenv("LLM_MODEL_NAME", "agent-model")
    monkeypatch.delenv("CHATBOT_LLM_API_KEY", raising=False)
    monkeypatch.delenv("CHATBOT_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("CHATBOT_LLM_MODEL_NAME", raising=False)

    # config.py calls load_dotenv(override=True) at import, which would stomp the
    # env set above with whatever the developer has in a real .env. Neutralise it
    # so this test reads the same on any machine.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: False)

    # Config resolves the fallback at class-definition time, so re-import it.
    # Reload again on the way out, or the stale values leak into later tests.
    config_module = importlib.import_module("app.config")
    importlib.reload(config_module)
    try:
        assert config_module.Config.CHATBOT_LLM_API_KEY == "agent-key"
        assert config_module.Config.CHATBOT_LLM_BASE_URL == "https://agent.example/v1"
        assert config_module.Config.CHATBOT_LLM_MODEL_NAME == "agent-model"
    finally:
        monkeypatch.undo()
        importlib.reload(config_module)


def _chatbot_key_errors(monkeypatch, **attrs):
    monkeypatch.setattr(Config, "NEO4J_PASSWORD", "neo4j-password")
    for name, value in attrs.items():
        monkeypatch.setattr(Config, name, value)
    return [e for e in Config.validate() if e.startswith("CHATBOT_LLM_API_KEY")]


def test_shared_endpoint_does_not_report_a_missing_chatbot_key(monkeypatch):
    # No CHATBOT_* configured and no LLM key either: one root cause, so the
    # LLM_API_KEY error alone must cover it.
    errors = _chatbot_key_errors(
        monkeypatch,
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_API_KEY=None,
        CHATBOT_LLM_BASE_URL="https://api.openai.com/v1",
        CHATBOT_LLM_API_KEY=None,
    )

    assert errors == []


def test_separate_endpoint_without_its_own_key_is_reported(monkeypatch):
    errors = _chatbot_key_errors(
        monkeypatch,
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_API_KEY="agent-key",
        CHATBOT_LLM_BASE_URL="https://other-provider.example/v1",
        CHATBOT_LLM_API_KEY=None,
    )

    assert len(errors) == 1


def test_separate_endpoint_with_its_own_key_is_accepted(monkeypatch):
    errors = _chatbot_key_errors(
        monkeypatch,
        LLM_BASE_URL="https://api.openai.com/v1",
        LLM_API_KEY="agent-key",
        CHATBOT_LLM_BASE_URL="https://other-provider.example/v1",
        CHATBOT_LLM_API_KEY="chatbot-key",
    )

    assert errors == []

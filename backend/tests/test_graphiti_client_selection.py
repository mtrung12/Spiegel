"""Which LLM client Graphiti gets, per endpoint.

This looks cosmetic and is not. Both clients speak the OpenAI protocol, but only
one of them actually constrains the response against the ontology model on any
given endpoint, and the wrong choice does not raise - it produces garbage
attributes that fail much later, at the Neo4j write, on every single ingestion.
See _build_llm_client for the measured failure.
"""

import pytest

from app.utils.graphiti_client import _build_llm_client
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient


def _client_for(base_url):
    return _build_llm_client(
        LLMConfig(api_key="k", model="m", base_url=base_url)
    )


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v1",
    "https://api.openai.com/v1/",
    "HTTPS://API.OPENAI.COM/v1",
])
def test_openai_proper_gets_the_parse_based_client(base_url):
    """Without this, gpt-4o-mini echoes the JSON schema back as the entity's
    attributes and Neo4j rejects the nested map."""

    assert isinstance(_client_for(base_url), OpenAIClient)


@pytest.mark.parametrize("base_url", [
    "http://localhost:1234/v1",           # LM Studio
    "http://172.24.247.130:1234/v1",      # the project's own box
    "http://localhost:11434/v1",          # Ollama
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "https://openrouter.ai/api/v1",
])
def test_compatible_servers_get_the_generic_client(base_url):
    """These enforce response_format json_schema by constrained decoding, which
    the Responses API parse() path is not available for."""

    assert isinstance(_client_for(base_url), OpenAIGenericClient)
    assert not isinstance(_client_for(base_url), OpenAIClient)


def test_a_missing_base_url_does_not_crash_the_selector():
    assert isinstance(_client_for(None), OpenAIGenericClient)


def test_a_lookalike_hostname_is_not_treated_as_openai():
    """Matching on substring rather than hostname would send a proxy's traffic
    down the OpenAI-only path."""

    assert isinstance(
        _client_for("https://api.openai.com.evil.example/v1"), OpenAIGenericClient
    )

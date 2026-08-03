"""Shared Graphiti engine, wired to the project's own LLM, embeddings and Neo4j.

Graphiti is the open-source temporal knowledge graph engine that Zep Cloud is a
hosted deployment of, so the concepts carry over one to one:

    Zep Cloud                   Graphiti
    ---------                   --------
    graph_id                    group_id          (same string, no migration)
    graph.add / batch API       add_episode / add_episode_bulk
    graph.set_ontology          entity_types / edge_types / edge_type_map
    graph.search                search_ with a hybrid recipe
    edge valid_at/invalid_at    the same bi-temporal fields

What does *not* carry over is the network: ingestion now runs inside this
process instead of on someone else's queue. That removes every reason the old
client had to reconcile ambiguous replies, poll a batch for a terminal state, or
wait for an episode's ``processed`` flag - ``add_episode`` has returned means the
episode is in the graph.
"""

from __future__ import annotations

import asyncio
import atexit
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, TypeVar
from urllib.parse import urlparse

from openai import AsyncOpenAI

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.driver.neo4j_driver import Neo4jDriver
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.client import LLMClient
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

from .logger import get_logger

logger = get_logger("spiegel.graphiti")

T = TypeVar("T")

# Ingestion is LLM-bound, and every episode fans out into extraction, entity
# resolution and edge invalidation calls. Left unbounded against a self-hosted
# server this is what melts it, so cap the concurrency in one place.
GRAPHITI_MAX_COROUTINES = 8

# One embedding request should be fast even on a busy self-hosted box. The SDK
# default is 10 minutes, which is indistinguishable from a hang.
EMBEDDING_REQUEST_TIMEOUT_SECONDS = 60.0

# Hosts whose /v1 is OpenAI's own API rather than an OpenAI-compatible server.
_OPENAI_API_HOSTS = {"api.openai.com"}


def _build_llm_client(config: LLMConfig) -> LLMClient:
    """Pick the extraction client that actually constrains output on this endpoint.

    Both clients speak the OpenAI protocol, but they enforce the response schema
    by different means, and the wrong one silently corrupts the graph rather than
    failing:

    - ``OpenAIGenericClient`` asks for ``response_format: json_schema`` without
      ``strict: true`` (Graphiti omits it because Pydantic's generated schema
      does not meet OpenAI's strict subset). A constrained-decoding server -
      vLLM, llama.cpp, LM Studio, Ollama - honours that schema anyway. OpenAI
      proper treats it as a hint, and gpt-4o-mini answers by echoing the schema
      back: ``{"title": "Product", "properties": {"category": null}, ...}``.
      That lands in ``node.attributes`` as a nested map, which Neo4j then
      rejects, and every ingestion fails on the first typed entity.
    - ``OpenAIClient`` uses the Responses API's ``parse()``, which validates
      against the model server-side. Only correct against OpenAI itself.

    So: OpenAI's own endpoint gets the parse-based client, everything else gets
    the generic one.
    """

    host = (urlparse(config.base_url or "").hostname or "").lower()
    if host in _OPENAI_API_HOSTS:
        return OpenAIClient(config=config)
    return OpenAIGenericClient(config=config)


class _EventLoopThread:
    """One long-lived asyncio loop that sync callers can submit work to.

    Graphiti is async and Spiegel is a synchronous Flask app with worker
    threads. The bridge cannot be ``asyncio.run()`` per call: the Neo4j async
    driver binds its connection pool to the loop that created it, so a new loop
    each call would strand pooled connections and eventually deadlock. One
    process-wide loop keeps the pool valid for the life of the process.
    """

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run,
            name="graphiti-loop",
            daemon=True,
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Coroutine[Any, Any, T]) -> Future[T]:
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)


_loop_thread: _EventLoopThread | None = None
_loop_lock = threading.Lock()


def _get_loop() -> _EventLoopThread:
    global _loop_thread
    with _loop_lock:
        if _loop_thread is None:
            _loop_thread = _EventLoopThread()
        return _loop_thread


def run_sync(coro: Coroutine[Any, Any, T], *, timeout: float | None = None) -> T:
    """Run one Graphiti coroutine from synchronous code and return its result.

    Exceptions propagate unchanged, so callers see the same failure they would
    see under ``await``.
    """

    if _in_async_context():
        raise RuntimeError(
            "run_sync was called from inside a running event loop; await the "
            "coroutine directly instead"
        )
    return _get_loop().submit(coro).result(timeout=timeout)


def _in_async_context() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


_client: Graphiti | None = None
_client_lock = threading.Lock()


def _config():
    """Read Config through the module rather than the name bound at import.

    ``app.config`` is reloaded in places (the LLM configuration tests do it to
    re-evaluate the environment), which rebinds ``app.config.Config`` to a new
    class object. A ``from ..config import Config`` reference captured at import
    time would keep pointing at the old class, and this would then build a
    driver from settings nobody can see or change.
    """

    import app.config

    return app.config.Config


def _build_client() -> Graphiti:
    config = _config()
    if not config.NEO4J_PASSWORD:
        raise ValueError("NEO4J_PASSWORD is not configured")

    llm_config = LLMConfig(
        api_key=config.LLM_API_KEY,
        model=config.LLM_MODEL_NAME,
        base_url=config.LLM_BASE_URL,
        # Extraction is a structured-output task, not a creative one. Zep ran it
        # deterministically and the graph is much noisier when this drifts.
        temperature=0.0,
    )

    embedder = OpenAIEmbedder(
        config=OpenAIEmbedderConfig(
            api_key=config.EMBEDDING_API_KEY,
            embedding_model=config.EMBEDDING_MODEL_NAME,
            embedding_dim=config.EMBEDDING_DIMENSIONS,
            base_url=config.EMBEDDING_BASE_URL,
        ),
        # Graphiti builds its own AsyncOpenAI with no timeout, which leaves the
        # SDK default (10 minutes) governing every embedding call. Embeddings
        # are usually served by a self-hosted box (EMBEDDING_BASE_URL), and when
        # that box is loaded or unreachable the whole ingestion sits on it -
        # a graph build that looks hung rather than one that failed.
        client=AsyncOpenAI(
            api_key=config.EMBEDDING_API_KEY,
            base_url=config.EMBEDDING_BASE_URL,
            timeout=EMBEDDING_REQUEST_TIMEOUT_SECONDS,
            max_retries=2,
        ),
    )

    driver = Neo4jDriver(
        uri=config.NEO4J_URI,
        user=config.NEO4J_USER,
        password=config.NEO4J_PASSWORD,
        database=config.NEO4J_DATABASE,
    )

    client = Graphiti(
        graph_driver=driver,
        llm_client=_build_llm_client(llm_config),
        embedder=embedder,
        # Never actually called: every search recipe Spiegel uses reranks with
        # RRF, which is pure arithmetic over the hybrid result sets. It is
        # supplied only so constructing Graphiti does not fall back to a default
        # client that reads OPENAI_API_KEY straight out of the environment.
        cross_encoder=OpenAIRerankerClient(config=llm_config),
        max_coroutines=GRAPHITI_MAX_COROUTINES,
    )

    # Idempotent, and required before the first search: the fulltext and vector
    # indexes are what make retrieval hybrid rather than a plain graph scan.
    run_sync(client.build_indices_and_constraints())
    logger.info(
        "Graphiti ready: neo4j=%s db=%s model=%s embedder=%s",
        config.NEO4J_URI,
        config.NEO4J_DATABASE,
        config.LLM_MODEL_NAME,
        config.EMBEDDING_MODEL_NAME,
    )
    return client


def get_graphiti() -> Graphiti:
    """Return the process-shared Graphiti engine, building it on first use."""

    global _client
    with _client_lock:
        if _client is None:
            _client = _build_client()
        return _client


def close_graphiti() -> None:
    """Close the engine and its Neo4j pool. For tests and process shutdown."""

    global _client
    with _client_lock:
        client = _client
        _client = None
    if client is not None:
        try:
            run_sync(client.driver.close(), timeout=10)
        except Exception as error:  # pragma: no cover - shutdown best effort
            logger.warning("Graphiti driver did not close cleanly: %s", error)


atexit.register(close_graphiti)

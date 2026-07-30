"""The chunking decisions in content_index: one record per point, with context."""

import httpx
import pytest

pytest.importorskip("qdrant_client")

from app.services.content_index import (  # noqa: E402
    MAX_EMBED_CHARS,
    ContentIndexService,
    build_embedding_text,
    _records,
)
from app.services.content_sentiment import ContentItem  # noqa: E402


def _item(uid, kind, content, author="alice", post_id=None, item_id=1, platform="twitter"):
    return ContentItem(
        uid=uid,
        platform=platform,
        kind=kind,
        item_id=item_id,
        post_id=post_id,
        author_id=7,
        author_name=author,
        content=content,
        likes=3,
        dislikes=1,
        created_at="2026-01-01 00:00:00",
    )


def test_a_bare_comment_is_embedded_with_the_post_it_replies_to():
    """Without the parent, "Totally agree" matches nothing. That is the whole point."""
    post = _item("twitter:p:1", "post", "Brand X just doubled its subscription price.")
    comment = _item("twitter:c:5", "comment", "Totally agree, this is a scam.",
                    author="bob", post_id=1, item_id=5)

    text = build_embedding_text(comment, post)

    assert "Totally agree, this is a scam." in text
    assert "subscription price" in text   # topic carried in from the parent
    assert "bob" in text and "alice" in text


def test_one_short_record_stays_one_point():
    post = _item("twitter:p:1", "post", "Short reaction to the ad.")

    records = _records([post], {}, "sim-1")

    assert len(records) == 1
    # The raw text is preserved separately from the retrieval surface.
    assert records[0]["payload"]["content"] == "Short reaction to the ad."
    assert records[0]["payload"]["simulation_id"] == "sim-1"


def test_only_an_oversized_record_is_split():
    long_post = _item("reddit:p:2", "post", "Sentence about the campaign. " * 200,
                      platform="reddit", item_id=2)
    assert len(long_post.content) > MAX_EMBED_CHARS

    records = _records([long_post], {}, "sim-1")

    assert len(records) > 1
    assert [r["payload"]["part"] for r in records] == list(range(len(records)))


def test_point_ids_are_stable_so_reindexing_upserts_in_place():
    post = _item("twitter:p:1", "post", "Same text every time.")

    first = _records([post], {}, "sim-1")
    second = _records([post], {}, "sim-1")
    other_sim = _records([post], {}, "sim-2")

    assert first[0]["id"] == second[0]["id"]
    assert first[0]["id"] != other_sim[0]["id"]


def test_search_returns_payloads_scored(monkeypatch):
    class FakePoint:
        def __init__(self):
            self.score = 0.87
            self.payload = {"content": "The price is insulting.", "author_name": "bob",
                            "platform": "reddit", "kind": "comment", "likes": 4}

    class FakeResponse:
        points = [FakePoint()]

    class FakeClient:
        def collection_exists(self, name):
            return True

        def count(self, **kwargs):
            return type("C", (), {"count": 12})()

        def query_points(self, **kwargs):
            return FakeResponse()

    service = ContentIndexService(client=FakeClient())
    monkeypatch.setattr(service, "embed", lambda texts: [[0.1, 0.2, 0.3]] * len(texts))

    results = service.search("sim-1", "what did people say about price")

    assert results[0]["content"] == "The price is insulting."
    assert results[0]["score"] == 0.87

    text = service.search_as_text("sim-1", "price")
    assert "The price is insulting." in text
    assert "bob" in text


def test_an_index_from_another_embedding_model_is_rebuilt(monkeypatch):
    """A collection built at another dimension cannot be searched with this model."""
    calls = {"reindexed": False}

    class FakeClient:
        def collection_exists(self, name):
            return True

        def count(self, **kwargs):
            return type("C", (), {"count": 12})()

        def get_collection(self, name):
            vectors = type("V", (), {"size": 1536})()
            return type("Info", (), {
                "config": type("Cfg", (), {
                    "params": type("P", (), {"vectors": vectors})()
                })()
            })()

        def query_points(self, **kwargs):
            return type("R", (), {"points": []})()

    service = ContentIndexService(client=FakeClient())
    monkeypatch.setattr(service, "embed", lambda texts: [[0.1] * 2560])
    monkeypatch.setattr(
        service, "index_simulation",
        lambda sim, force=False: calls.__setitem__("reindexed", force),
    )

    service.search("sim-1", "price")

    assert calls["reindexed"] is True


def test_an_idle_unloaded_model_is_retried_but_other_400s_are_not(monkeypatch):
    """LM Studio reports its idle-unload race as a 400, which the SDK never retries."""
    from openai import BadRequestError

    def _bad_request(message):
        response = httpx.Response(400, request=httpx.Request("POST", "http://x/v1/embeddings"))
        return BadRequestError(message, response=response, body=None)

    class FakeEmbeddings:
        def __init__(self, errors):
            self.errors = list(errors)
            self.calls = 0

        def create(self, model, input):
            self.calls += 1
            if self.errors:
                raise _bad_request(self.errors.pop(0))
            data = [type("D", (), {"index": i, "embedding": [0.1] * 1024})()
                    for i, _ in enumerate(input)]
            return type("R", (), {"data": data})()

    monkeypatch.setattr("app.services.content_index.EMBED_UNLOAD_BACKOFF_SECONDS", 0)

    # Transient unload: retried, then succeeds.
    embeddings = FakeEmbeddings(["Model was unloaded while the request was still in queue.."])
    service = ContentIndexService(
        embedder=type("E", (), {"embeddings": embeddings})()
    )
    vectors = service.embed(["a", "b"])
    assert len(vectors) == 2 and len(vectors[0]) == 1024
    assert embeddings.calls == 2

    # Any other 400 is a real error and must surface immediately.
    embeddings = FakeEmbeddings(['Invalid model identifier "nope".'] * 9)
    service = ContentIndexService(
        embedder=type("E", (), {"embeddings": embeddings})()
    )
    with pytest.raises(BadRequestError):
        service.embed(["a"])
    assert embeddings.calls == 1


def test_every_service_instance_shares_one_qdrant_client(monkeypatch):
    """
    Embedded Qdrant takes an exclusive file lock, so a second live client in the
    same process cannot open the storage at all - and callers build a throwaway
    service per use.
    """
    import qdrant_client

    from app.services import content_index

    built = []

    class CountingClient:
        def __init__(self, *args, **kwargs):
            built.append(kwargs)

    monkeypatch.setattr(qdrant_client, "QdrantClient", CountingClient)
    content_index.reset_shared_clients()
    try:
        first = ContentIndexService().client
        second = ContentIndexService().client
        assert first is second
        assert len(built) == 1, "a second client would fail on the storage lock"

        content_index.reset_shared_clients()
        assert ContentIndexService().client is not first
        assert len(built) == 2
    finally:
        content_index.reset_shared_clients()


def test_empty_query_short_circuits():
    service = ContentIndexService(client=object())
    assert service.search("sim-1", "   ") == []

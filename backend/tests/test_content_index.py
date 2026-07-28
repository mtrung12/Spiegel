"""The chunking decisions in content_index: one record per point, with context."""

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


def test_empty_query_short_circuits():
    service = ContentIndexService(client=object())
    assert service.search("sim-1", "   ") == []

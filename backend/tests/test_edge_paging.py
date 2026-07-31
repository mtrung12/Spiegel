"""Pagination over one graph partition.

The cursor is no longer an opaque header from a remote service: Graphiti orders
by ``uuid DESC`` and pages with ``uuid < $cursor``, so the last item of a page
is the next cursor and a short page is the only end-of-results signal. These
tests pin that contract, because getting it wrong silently truncates a graph
read to its first page - which reads as "the build extracted very little"
rather than as a failure.
"""

from types import SimpleNamespace

import pytest

from app.utils import graphiti_graph
from graphiti_core.errors import GroupsEdgesNotFoundError


def _stub_pages(monkeypatch, pages):
    """Serve prepared pages and record the cursor each call was made with."""

    calls = []

    async def fake_getter(_driver, group_ids, limit, uuid_cursor):
        calls.append({"group_ids": group_ids, "limit": limit, "cursor": uuid_cursor})
        page = pages[len(calls) - 1]
        if isinstance(page, Exception):
            raise page
        return page

    monkeypatch.setattr(
        graphiti_graph, "get_graphiti", lambda: SimpleNamespace(driver=object())
    )
    monkeypatch.setattr(graphiti_graph.EntityEdge, "get_by_group_ids", fake_getter)
    return calls


def _edge(uuid):
    return SimpleNamespace(uuid=uuid)


def test_edge_cap_stops_pagination_at_requested_limit(monkeypatch):
    calls = _stub_pages(monkeypatch, [
        [_edge("e1"), _edge("e2")],
        [_edge("e3"), _edge("e4")],
    ])

    result = graphiti_graph.fetch_all_edges("graph", page_size=2, max_items=3)

    assert [edge.uuid for edge in result] == ["e1", "e2", "e3"]
    assert len(calls) == 2


def test_pagination_advances_by_the_last_uuid_of_each_page(monkeypatch):
    calls = _stub_pages(monkeypatch, [
        [_edge("e1"), _edge("e2")],
        [_edge("e3")],
    ])

    result = graphiti_graph.fetch_all_edges("graph", page_size=2)

    assert [edge.uuid for edge in result] == ["e1", "e2", "e3"]
    assert calls == [
        {"group_ids": ["graph"], "limit": 2, "cursor": None},
        # The cursor is the last uuid of page one, not page one's first item.
        {"group_ids": ["graph"], "limit": 2, "cursor": "e2"},
    ]


def test_a_full_final_page_is_followed_by_one_more_request(monkeypatch):
    """A page of exactly page_size cannot be assumed to be the last one."""

    calls = _stub_pages(monkeypatch, [
        [_edge("e2"), _edge("e1")],
        GroupsEdgesNotFoundError(["graph"]),
    ])

    result = graphiti_graph.fetch_all_edges("graph", page_size=2)

    assert [edge.uuid for edge in result] == ["e2", "e1"]
    assert len(calls) == 2


def test_a_graph_with_no_edges_reads_as_empty_not_as_an_error(monkeypatch):
    """Graphiti raises where it could return []; an empty graph is valid state."""

    _stub_pages(monkeypatch, [GroupsEdgesNotFoundError(["graph"])])

    assert graphiti_graph.fetch_all_edges("graph") == []


def test_page_size_must_stay_within_a_single_query_bound(monkeypatch):
    _stub_pages(monkeypatch, [])

    with pytest.raises(ValueError, match="page_size"):
        graphiti_graph.fetch_all_edges("graph", page_size=0)


def test_graph_id_is_required(monkeypatch):
    _stub_pages(monkeypatch, [])

    with pytest.raises(ValueError, match="graph_id"):
        graphiti_graph.fetch_all_edges("")

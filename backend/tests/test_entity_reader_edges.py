"""Per-node edge reads.

Zep's per-node endpoint returned only the edges where the node was the source,
so callers scanned the whole graph to see incoming ones. The underlying query
now matches an undirected pattern, and these tests pin that both directions come
back from a single node-scoped read - a regression here would quietly halve the
relationship context every agent persona is written from.
"""

from types import SimpleNamespace

from app.services import graph_entity_reader
from app.services.graph_entity_reader import GraphEntityReader


def _edge(uuid, source, target):
    return SimpleNamespace(
        uuid=uuid,
        name="KNOWS",
        fact=f"{source} knows {target}",
        source_node_uuid=source,
        target_node_uuid=target,
        attributes={"since": "2024"},
    )


def test_get_node_edges_returns_both_directions_from_one_read(monkeypatch):
    calls = []

    def fake_fetch_node_edges(node_uuid):
        calls.append(node_uuid)
        return [
            _edge("edge-out", "node-1", "node-2"),
            _edge("edge-in", "node-3", "node-1"),
        ]

    monkeypatch.setattr(
        graph_entity_reader, "fetch_node_edges", fake_fetch_node_edges
    )

    edges = GraphEntityReader().get_node_edges("node-1")

    assert [edge["uuid"] for edge in edges] == ["edge-out", "edge-in"]
    assert edges[0] == {
        "uuid": "edge-out",
        "name": "KNOWS",
        "fact": "node-1 knows node-2",
        "source_node_uuid": "node-1",
        "target_node_uuid": "node-2",
        "attributes": {"since": "2024"},
    }
    # One node-scoped read, not a scan of every edge in the graph.
    assert calls == ["node-1"]


def test_graph_id_no_longer_triggers_a_whole_graph_scan(monkeypatch):
    """The legacy graph_id argument is accepted but must not change behaviour."""

    monkeypatch.setattr(
        graph_entity_reader,
        "fetch_node_edges",
        lambda node_uuid: [_edge("edge-1", "node-1", "node-2")],
    )

    def fail(*_args, **_kwargs):
        raise AssertionError("passing graph_id must not fetch every edge")

    monkeypatch.setattr(graph_entity_reader, "fetch_all_edges", fail)

    reader = GraphEntityReader()
    assert reader.get_node_edges("node-1", graph_id="graph-1") == (
        reader.get_node_edges("node-1")
    )


def test_a_store_failure_is_not_reported_as_an_empty_edge_list(monkeypatch):
    def boom(_node_uuid):
        raise ConnectionError("neo4j unreachable")

    monkeypatch.setattr(graph_entity_reader, "fetch_node_edges", boom)

    import pytest

    with pytest.raises(ConnectionError):
        GraphEntityReader().get_node_edges("node-1")

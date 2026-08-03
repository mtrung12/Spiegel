"""Integration checks against a real Neo4j.

Everything else in the suite stubs the graph store, which cannot catch the class
of bug that only a real database shows: a pagination cursor that never advances,
an empty-result path that raises instead of returning, an edge query that only
walks one direction. Those are exactly the failures that read as "the build
found very little" rather than as an error, so they are worth a live database.

No LLM or embedding server is involved - nodes are written directly, with
embeddings supplied by hand. Set NEO4J_TEST_URI (and NEO4J_TEST_PASSWORD) to run
these; without it they skip.

    docker run -d -p 7688:7687 -e NEO4J_AUTH=neo4j/testpassword123 \\
        neo4j:5.26-community
    NEO4J_TEST_URI=bolt://localhost:7688 \\
        NEO4J_TEST_PASSWORD=testpassword123 pytest tests/test_graph_store_integration.py
"""

import os
import uuid
from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("NEO4J_TEST_URI"),
    reason="NEO4J_TEST_URI is not set; live graph store checks are skipped",
)


@pytest.fixture(scope="module")
def graph_store():
    """Point the app's config at the test database and hand back the engine."""

    import app.config
    from app.utils import graphiti_client

    # Read the module attribute rather than a previously imported name: other
    # tests reload app.config, which rebinds Config to a new class object.
    config = app.config.Config
    config.NEO4J_URI = os.environ["NEO4J_TEST_URI"]
    config.NEO4J_USER = os.environ.get("NEO4J_TEST_USER", "neo4j")
    config.NEO4J_PASSWORD = os.environ["NEO4J_TEST_PASSWORD"]

    graphiti_client.close_graphiti()
    client = graphiti_client.get_graphiti()
    yield client
    graphiti_client.close_graphiti()


@pytest.fixture
def graph_id(graph_store):
    """A unique partition per test, torn down afterwards."""

    from app.services.graph_builder import GraphBuilderService

    gid = f"spiegel_test{uuid.uuid4().hex[:12]}"
    yield gid
    GraphBuilderService().delete_graph(gid)


def _seed(graph_store, graph_id, node_count=1, with_edge=False):
    """Write nodes (and optionally one edge) straight to the store."""

    import app.config
    from app.utils.graphiti_client import run_sync
    from graphiti_core.edges import EntityEdge
    from graphiti_core.nodes import EntityNode

    now = datetime.now(timezone.utc)
    # Graphiti fills these from the embedder; supplied here so the test needs no
    # embedding server. The width must match the index the engine created.
    vector = [0.01] * app.config.Config.EMBEDDING_DIMENSIONS

    nodes = [
        EntityNode(
            name=f"Node{index}",
            group_id=graph_id,
            labels=["Entity", "Student"],
            summary=f"summary {index}",
            attributes={"major": "cs"},
            created_at=now,
            name_embedding=list(vector),
        )
        for index in range(node_count)
    ]
    for node in nodes:
        run_sync(node.save(graph_store.driver))

    if with_edge:
        edge = EntityEdge(
            source_node_uuid=nodes[0].uuid,
            target_node_uuid=nodes[1].uuid,
            name="supports",
            fact="Node0 supports Node1",
            group_id=graph_id,
            created_at=now,
            valid_at=now,
            attributes={"strength": "high"},
            fact_embedding=list(vector),
        )
        run_sync(edge.save(graph_store.driver))
    return nodes


def test_an_empty_graph_reads_as_empty_rather_than_raising(graph_id):
    from app.utils.graphiti_graph import fetch_all_edges, fetch_all_nodes, fetch_node

    assert fetch_all_nodes(graph_id) == []
    assert fetch_all_edges(graph_id) == []
    assert fetch_node("00000000-0000-0000-0000-000000000000") is None


def test_pagination_returns_every_node_not_just_the_first_page(graph_store, graph_id):
    """250 nodes over a page size of 100 must come back as 250, not 100."""

    from app.utils.graphiti_graph import fetch_all_nodes

    _seed(graph_store, graph_id, node_count=250)

    fetched = fetch_all_nodes(graph_id, page_size=100)

    assert len(fetched) == 250
    assert len({node.uuid for node in fetched}) == 250


def test_a_final_page_of_exactly_page_size_terminates(graph_store, graph_id):
    from app.utils.graphiti_graph import fetch_all_nodes

    _seed(graph_store, graph_id, node_count=50)

    assert len(fetch_all_nodes(graph_id, page_size=50)) == 50


def test_max_items_caps_the_read(graph_store, graph_id):
    from app.utils.graphiti_graph import fetch_all_nodes

    _seed(graph_store, graph_id, node_count=30)

    assert len(fetch_all_nodes(graph_id, page_size=10, max_items=12)) == 12


def test_node_edges_come_back_from_both_directions(graph_store, graph_id):
    """The source-only behaviour of Zep's endpoint must not reappear."""

    from app.utils.graphiti_graph import fetch_node_edges

    nodes = _seed(graph_store, graph_id, node_count=2, with_edge=True)

    assert len(fetch_node_edges(nodes[0].uuid)) == 1  # outgoing
    assert len(fetch_node_edges(nodes[1].uuid)) == 1  # incoming


def test_graph_data_resolves_node_names_and_temporal_fields(graph_store, graph_id):
    from app.services.graph_builder import GraphBuilderService

    _seed(graph_store, graph_id, node_count=2, with_edge=True)

    data = GraphBuilderService().get_graph_data(graph_id)

    assert data["node_count"] == 2
    assert data["edge_count"] == 1
    edge = data["edges"][0]
    assert edge["source_node_name"] == "Node0"
    assert edge["target_node_name"] == "Node1"
    assert edge["fact"] == "Node0 supports Node1"
    assert edge["fact_type"] == "supports"
    # A live edge: valid, never invalidated. The report splits active from
    # historical facts on exactly these two fields.
    assert edge["valid_at"] is not None
    assert edge["invalid_at"] is None


def test_deleting_a_graph_removes_its_nodes_and_edges(graph_store, graph_id):
    from app.services.graph_builder import GraphBuilderService
    from app.utils.graphiti_graph import fetch_all_edges, fetch_all_nodes

    _seed(graph_store, graph_id, node_count=2, with_edge=True)
    assert fetch_all_nodes(graph_id)

    GraphBuilderService().delete_graph(graph_id)

    assert fetch_all_nodes(graph_id) == []
    assert fetch_all_edges(graph_id) == []


def test_one_graphs_delete_leaves_another_graph_intact(graph_store, graph_id):
    """group_id is the only isolation between projects sharing one database."""

    from app.services.graph_builder import GraphBuilderService
    from app.utils.graphiti_graph import fetch_all_nodes

    other_id = f"spiegel_test{uuid.uuid4().hex[:12]}"
    try:
        _seed(graph_store, graph_id, node_count=3)
        _seed(graph_store, other_id, node_count=2)

        GraphBuilderService().delete_graph(graph_id)

        assert fetch_all_nodes(graph_id) == []
        assert len(fetch_all_nodes(other_id)) == 2
    finally:
        GraphBuilderService().delete_graph(other_id)

"""Ingestion arguments that decide what ends up in the graph.

Nothing here calls an LLM. It pins the arguments, because every failure in this
area is silent: the build succeeds, the graph is just emptier than it should be,
and that reads downstream as "the campaign brief was thin" rather than as a bug.
"""

from types import SimpleNamespace

import pytest

from app.services import graph_builder
from app.services.graph_builder import MAX_EPISODE_CHARS, GraphBuilderService
from app.utils.ontology import SEGMENT_EXTRACTION_INSTRUCTIONS

ONTOLOGY = {
    "entity_types": [{"name": "Customer", "attributes": ["concern"]}],
    "edge_types": [{
        "name": "OBJECTS_TO",
        "attributes": [],
        "source_targets": [{"source": "Customer", "target": "Customer"}],
    }],
}


@pytest.fixture
def builder(monkeypatch):
    """A builder whose ingestion calls are captured instead of executed."""

    calls = []
    client = SimpleNamespace(add_episode_bulk=lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(graph_builder, "get_graphiti", lambda: client)
    monkeypatch.setattr(graph_builder, "run_sync", lambda result: result)
    service = GraphBuilderService()
    service.calls = calls
    return service


def test_ingestion_applies_the_projects_ontology(builder):
    builder.add_text_batches("graph-1", ["chunk one"], ontology=ONTOLOGY)

    call = builder.calls[0]
    assert set(call["entity_types"]) == {"Customer"}
    assert set(call["edge_types"]) == {"OBJECTS_TO"}
    assert call["edge_type_map"] == {("Customer", "Customer"): ["OBJECTS_TO"]}
    assert call["group_id"] == "graph-1"


def test_ingestion_overrides_the_stock_refusal_to_extract_segments(builder):
    """Graphiti's default prompt bans abstract entities, which would drop every
    audience segment - the `general_*` ontology kinds the simulated cast is
    cloned from. Measured on a two-sentence sample, leaving this off took the
    result from 2 nodes / 3 edges down to 1 node / 0 edges."""

    builder.add_text_batches("graph-1", ["chunk one"], ontology=ONTOLOGY)

    assert builder.calls[0]["custom_extraction_instructions"] == (
        SEGMENT_EXTRACTION_INSTRUCTIONS
    )


def test_chunks_are_ingested_in_batches_with_progress_between_them(builder):
    progress = []

    written = builder.add_text_batches(
        "graph-1",
        [f"chunk {index}" for index in range(5)],
        ontology=ONTOLOGY,
        batch_size=2,
        progress_callback=lambda message, ratio: progress.append(ratio),
    )

    assert written == 5
    assert len(builder.calls) == 3  # 2 + 2 + 1
    assert [len(call["bulk_episodes"]) for call in builder.calls] == [2, 2, 1]
    # Progress must advance and finish at 1.0, since it drives a cancellable
    # build: a callback that never fires is a build that cannot be stopped.
    assert progress == sorted(progress)
    assert progress[-1] == 1.0


def test_episode_names_stay_unique_across_batches(builder):
    """Duplicate names across batches would collide when tracing an entity back
    to the chunk it came from."""

    builder.add_text_batches(
        "graph-1",
        [f"chunk {index}" for index in range(5)],
        ontology=ONTOLOGY,
        batch_size=2,
    )

    names = [
        episode.name
        for call in builder.calls
        for episode in call["bulk_episodes"]
    ]
    assert names == [f"chunk_{index}" for index in range(5)]


def test_a_missing_ontology_still_ingests_untyped(builder):
    builder.add_text_batches("graph-1", ["chunk one"], ontology=None)

    assert builder.calls[0]["entity_types"] == {}


def test_oversized_chunks_are_rejected_before_the_first_write(builder):
    with pytest.raises(ValueError, match="exceeds"):
        builder.add_text_batches(
            "graph-1",
            ["fine", "x" * (MAX_EPISODE_CHARS + 1)],
            ontology=ONTOLOGY,
        )

    assert builder.calls == []


def test_empty_input_is_rejected_before_the_first_write(builder):
    with pytest.raises(ValueError, match="At least one text chunk"):
        builder.add_text_batches("graph-1", [], ontology=ONTOLOGY)

    assert builder.calls == []


def test_graph_id_is_recorded_before_any_ingestion_can_fail(builder):
    """The reset path can only clean up a graph whose id was persisted first."""

    remembered = []
    graph_id = builder.create_graph("name", graph_id_callback=remembered.append)

    assert remembered == [graph_id]
    assert graph_id.startswith("spiegel_")

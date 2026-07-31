"""
A simulation writes its agents' activity back into the project's own graph,
so the audience must be read from the nodes the documents produced, not from
whatever the last run invented.
"""

import pytest

from app.services import zep_entity_reader
from app.services.zep_entity_reader import ZepEntityReader

BUILT_AT = "2026-07-31T06:27:00+00:00"

NODES = [
    # From the documents.
    {"uuid": "1", "name": "Petra", "labels": ["Entity", "Student"], "summary": "",
     "attributes": {}, "created_at": "2026-07-31T06:26:10.000Z"},
    {"uuid": "2", "name": "Tesla", "labels": ["Entity", "Organization"], "summary": "",
     "attributes": {}, "created_at": "2026-07-31T06:26:40.000Z"},
    # Untyped concept node - already skipped, before any of this.
    {"uuid": "3", "name": "winter conditions", "labels": ["Entity"], "summary": "",
     "attributes": {}, "created_at": "2026-07-31T06:26:50.000Z"},
    # Typed, but invented by the simulation after the build finished.
    {"uuid": "4", "name": "weekly updates thread", "labels": ["Entity", "Student"],
     "summary": "", "attributes": {}, "created_at": "2026-07-31T06:53:00.000Z"},
]


def _reader(monkeypatch, cutoff):
    # __init__ demands a live API key; none of it is exercised here.
    reader = object.__new__(ZepEntityReader)
    monkeypatch.setattr(ZepEntityReader, "get_all_nodes", lambda self, gid: list(NODES))
    monkeypatch.setattr(ZepEntityReader, "get_all_edges", lambda self, gid: [])
    monkeypatch.setattr(
        zep_entity_reader, "_document_build_cutoff",
        lambda gid: zep_entity_reader._parse_iso(cutoff) if cutoff else None,
    )
    return reader


def test_nodes_written_after_the_build_are_not_audience(monkeypatch):
    reader = _reader(monkeypatch, BUILT_AT)

    result = reader.filter_defined_entities(
        graph_id="g", defined_entity_types=["Student", "Organization"],
        enrich_with_edges=False,
    )

    assert sorted(e.name for e in result.entities) == ["Petra", "Tesla"]
    assert result.filtered_count == 2
    # total_count stays the true size of the graph, which is what the UI reports.
    assert result.total_count == 4


def test_without_a_known_build_time_nothing_is_dropped(monkeypatch):
    """A project built before graph_built_at existed keeps the old behaviour."""
    reader = _reader(monkeypatch, None)

    result = reader.filter_defined_entities(
        graph_id="g", defined_entity_types=["Student", "Organization"],
        enrich_with_edges=False,
    )

    assert sorted(e.name for e in result.entities) == [
        "Petra", "Tesla", "weekly updates thread"
    ]


@pytest.mark.parametrize("value,expected", [
    ("2026-07-31T06:26:10.000Z", True),   # Zep's format
    ("2026-07-31T06:26:10+00:00", True),  # what the project file stores
    ("2026-07-31T06:26:10", True),        # naive, assumed UTC
    ("not a date", False),
    ("", False),
])
def test_parse_iso_accepts_both_shapes(value, expected):
    assert (zep_entity_reader._parse_iso(value) is not None) is expected


def test_unparseable_timestamp_keeps_the_node():
    """Dropping a real entity is worse than keeping a simulation artefact."""
    cutoff = zep_entity_reader._parse_iso(BUILT_AT)
    assert zep_entity_reader._created_after({"created_at": "???"}, cutoff) is False
    assert zep_entity_reader._created_after({}, cutoff) is False

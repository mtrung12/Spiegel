from types import SimpleNamespace
import threading
from queue import Queue

import pytest

from app.services import graph_memory_updater as updater_module
from app.services.graph_memory_updater import (
    AgentActivity,
    GraphMemoryManager,
    GraphMemoryUpdater,
)


def _activity(index=1, content="hello"):
    return AgentActivity(
        platform="twitter",
        agent_id=index,
        agent_name=f"Agent {index}",
        action_type="CREATE_POST",
        action_args={"content": content},
        round_num=index,
        timestamp="2026-07-22T12:00:00+08:00",
    )


def _updater(monkeypatch, add, simulation_id="sim-1"):
    """Build an updater whose add_episode call is `add`, with no real engine.

    `add` receives the same keyword arguments Graphiti's add_episode does and
    returns whatever the caller wants; run_sync is stubbed to call it directly
    so no event loop or Neo4j connection is involved.
    """

    client = SimpleNamespace(add_episode=add)
    monkeypatch.setattr(updater_module, "get_graphiti", lambda: client)
    # add_episode is stubbed to return a value rather than a coroutine, so the
    # bridge just hands that value back.
    monkeypatch.setattr(updater_module, "run_sync", lambda result: result)
    monkeypatch.setattr(
        updater_module.ProjectManager, "find_projects_by_graph_id", lambda _id: []
    )
    updater = GraphMemoryUpdater("graph-1", simulation_id=simulation_id)
    updater.SEND_INTERVAL = 0
    return updater


def _episode(uuid="episode-1"):
    return SimpleNamespace(episode=SimpleNamespace(uuid=uuid))


def test_stop_drains_an_immediately_queued_tail_activity(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or _episode(),
    )

    updater.start()
    updater.add_activity(_activity())
    updater.stop()

    assert len(writes) == 1
    assert updater.get_stats()["items_sent"] == 1
    assert updater.get_stats()["queue_size"] == 0


def test_network_write_happens_outside_the_buffer_lock(monkeypatch):
    lock_was_available = []
    updater = None

    def add(**_kwargs):
        acquired = updater._buffer_lock.acquire(blocking=False)
        lock_was_available.append(acquired)
        if acquired:
            updater._buffer_lock.release()
        return _episode()

    updater = _updater(monkeypatch, add)
    updater.start()
    for index in range(updater.BATCH_SIZE):
        updater.add_activity(_activity(index))
    updater.stop()

    assert lock_was_available == [True]


def test_activity_episode_has_provenance_time_and_a_safe_size(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or _episode(),
        simulation_id="sim-provenance",
    )

    updater._send_batch_activities(
        [_activity(content="x" * 20_000)],
        "twitter",
    )

    assert len(writes) == 1
    write = writes[0]
    assert len(write["episode_body"]) <= updater.MAX_EPISODE_CHARS
    # The reference time anchors edge validity intervals, so it must be the
    # activity's own timestamp and must be timezone-aware.
    assert write["reference_time"].isoformat() == "2026-07-22T12:00:00+08:00"
    assert write["source_description"] == "Spiegel simulation activity batch"
    assert write["group_id"] == "graph-1"
    # Metadata is gone, so provenance has to live in the episode name.
    assert "sim-provenance" in write["name"]
    assert "twitter" in write["name"]


def test_failed_non_idempotent_write_is_reported_by_stop(monkeypatch):
    def add(**_kwargs):
        raise RuntimeError("write failed")

    updater = _updater(monkeypatch, add)
    updater.start()
    updater.add_activity(_activity())

    with pytest.raises(RuntimeError, match="ingestion is incomplete"):
        updater.stop()

    assert updater.get_stats()["failed_count"] == 1


def test_failed_simulation_action_is_not_ingested(monkeypatch):
    updater = _updater(
        monkeypatch,
        lambda **_kwargs: _episode(uuid="unused"),
    )

    updater.add_activity_from_dict(
        {
            "agent_id": 1,
            "agent_name": "Agent",
            "action_type": "CREATE_POST",
            "action_args": {"content": "not actually posted"},
            "success": False,
        },
        "twitter",
    )

    assert updater.get_stats()["queue_size"] == 0
    assert updater.get_stats()["skipped_count"] == 1


def test_stop_cannot_finish_between_acceptance_check_and_enqueue(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or _episode(),
    )

    put_entered = threading.Event()
    allow_put = threading.Event()

    class BlockingQueue(Queue):
        def put(self, item, block=True, timeout=None):
            put_entered.set()
            assert allow_put.wait(timeout=2)
            return super().put(item, block=block, timeout=timeout)

    updater._activity_queue = BlockingQueue()
    updater.start()
    producer = threading.Thread(target=updater.add_activity, args=(_activity(),))
    producer.start()
    assert put_entered.wait(timeout=1)

    stopper = threading.Thread(target=updater.stop)
    stopper.start()
    stopper.join(timeout=0.1)
    assert stopper.is_alive()

    allow_put.set()
    producer.join(timeout=2)
    stopper.join(timeout=2)

    assert not producer.is_alive()
    assert not stopper.is_alive()
    assert len(writes) == 1


def test_explicit_graph_destruction_can_discard_a_stopped_failed_updater():
    updater = SimpleNamespace(
        graph_id="graph-1",
        _running=False,
        _worker_thread=SimpleNamespace(is_alive=lambda: False),
    )
    GraphMemoryManager._updaters["sim-failed"] = updater
    try:
        assert GraphMemoryManager.discard_inactive_updater("sim-failed") is True
        assert "sim-failed" not in GraphMemoryManager._updaters
    finally:
        GraphMemoryManager._updaters.pop("sim-failed", None)


def test_flush_deadline_keeps_unattempted_platform_for_a_safe_retry(monkeypatch):
    now = [0.0]
    writes = []

    def add(**kwargs):
        writes.append(kwargs)
        now[0] = 2.0
        return _episode(uuid=f"episode-{len(writes)}")

    updater = _updater(monkeypatch, add)
    updater._platform_buffers["twitter"] = [_activity(1)]
    reddit_activity = _activity(2)
    reddit_activity.platform = "reddit"
    updater._platform_buffers["reddit"] = [reddit_activity]
    monkeypatch.setattr(updater_module.time, "time", lambda: now[0])

    with pytest.raises(TimeoutError, match="deadline"):
        updater._flush_remaining(deadline=1.0)

    assert updater._platform_buffers["twitter"] == []
    assert updater._platform_buffers["reddit"] == [reddit_activity]

    now[0] = 0.0
    updater._flush_remaining(deadline=1.0)
    assert updater._platform_buffers["reddit"] == []
    assert len(writes) == 2


def test_activity_is_extracted_with_the_graphs_own_ontology(monkeypatch):
    """Without the ontology, simulation activity lands as untyped Entity nodes.

    The report then filters by entity type and finds nothing, so this is the
    difference between a report with an audience in it and an empty one.
    """

    writes = []
    client = SimpleNamespace(
        add_episode=lambda **kwargs: writes.append(kwargs) or _episode()
    )
    monkeypatch.setattr(updater_module, "get_graphiti", lambda: client)
    monkeypatch.setattr(updater_module, "run_sync", lambda result: result)
    monkeypatch.setattr(
        updater_module.ProjectManager,
        "find_projects_by_graph_id",
        lambda _id: [
            SimpleNamespace(ontology=None),
            SimpleNamespace(ontology={
                "entity_types": [{"name": "Student", "attributes": ["major"]}],
                "edge_types": [{
                    "name": "SUPPORTS",
                    "attributes": [],
                    "source_targets": [{"source": "Student", "target": "Student"}],
                }],
            }),
        ],
    )

    updater = GraphMemoryUpdater("graph-1", simulation_id="sim-ontology")
    updater.SEND_INTERVAL = 0
    updater._send_batch_activities([_activity()], "twitter")

    assert set(writes[0]["entity_types"]) == {"Student"}
    assert set(writes[0]["edge_types"]) == {"SUPPORTS"}
    assert writes[0]["edge_type_map"] == {("Student", "Student"): ["SUPPORTS"]}


def test_a_graph_with_no_stored_ontology_still_ingests(monkeypatch):
    writes = []
    updater = _updater(
        monkeypatch,
        lambda **kwargs: writes.append(kwargs) or _episode(),
    )
    updater._send_batch_activities([_activity()], "twitter")

    assert writes[0]["entity_types"] == {}
    assert updater.get_stats()["failed_count"] == 0

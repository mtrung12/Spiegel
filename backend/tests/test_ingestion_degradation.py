"""
A graph export that can never complete must not destroy the run's report.

The simulation databases hold every action, so KPIs, sentiment and content
search are unaffected by a failed graph ingestion - only the parts of the report
that read run memory out of the graph see less. Failing the run instead is
terminal in the worst way: the report API requires a successful terminal
status, so a permanently failing extraction model costs the user the report
for a simulation that actually succeeded.

The distinction that makes this safe is retryability. A drain that timed out
may still finish, so it stays FAILED and stoppable again. Rejected batches are
never replayed, so retrying raises forever.
"""

import pytest

from app.services import simulation_runner as runner_module
from app.services.simulation_runner import (
    RunnerStatus,
    SimulationRunState,
    SimulationRunner,
)
from app.services.graph_memory_updater import (
    GraphMemoryManager,
    GraphIngestionIncomplete,
)


def _raise(exc):
    def _stop(_cls, _simulation_id):
        raise exc
    return classmethod(_stop)


def _stop_with(monkeypatch, simulation_id, exc):
    state = SimulationRunState(
        simulation_id=simulation_id,
        runner_status=RunnerStatus.RUNNING,
    )
    monkeypatch.setattr(
        SimulationRunner,
        "get_run_state",
        classmethod(lambda _cls, _simulation_id: state),
    )
    monkeypatch.setattr(
        SimulationRunner, "_save_run_state", classmethod(lambda _cls, _state: None)
    )
    monkeypatch.setattr(
        SimulationRunner,
        "_sync_simulation_status",
        classmethod(lambda _cls, *_args, **_kwargs: None),
    )
    monkeypatch.setattr(runner_module.GraphMemoryManager, "stop_updater", _raise(exc))
    SimulationRunner._processes.pop(simulation_id, None)
    SimulationRunner._monitor_threads.pop(simulation_id, None)
    SimulationRunner._graph_memory_enabled[simulation_id] = True
    return state


def test_rejected_batches_still_reach_a_reportable_terminal_status(monkeypatch):
    error = GraphIngestionIncomplete("116 activity batch(es) failed")
    state = _stop_with(monkeypatch, "sim-degraded", error)

    try:
        result = SimulationRunner.stop_simulation("sim-degraded")

        # Terminal and successful, so the report API's guard lets it through.
        assert result.runner_status == RunnerStatus.STOPPED
        assert result.error is None
        # ...but the loss is recorded rather than silently swallowed.
        assert "116 activity batch(es) failed" in state.graph_ingestion_error
        assert state.to_dict()["graph_ingestion_error"] == state.graph_ingestion_error
    finally:
        SimulationRunner._graph_memory_enabled.pop("sim-degraded", None)
        SimulationRunner._manual_stop_requests.discard("sim-degraded")


def test_a_retryable_drain_failure_still_fails_the_run(monkeypatch):
    """A timeout may yet succeed, so it must keep the retry contract."""
    state = _stop_with(
        monkeypatch, "sim-timeout-drain", TimeoutError("worker did not stop within 600s")
    )

    try:
        with pytest.raises(RuntimeError, match="did not complete"):
            SimulationRunner.stop_simulation("sim-timeout-drain")

        assert state.runner_status == RunnerStatus.FAILED
        assert state.graph_ingestion_error is None
    finally:
        SimulationRunner._graph_memory_enabled.pop("sim-timeout-drain", None)
        SimulationRunner._manual_stop_requests.discard("sim-timeout-drain")


class _Updater:
    """Minimal stand-in for the manager's registry value."""

    graph_id = "graph-1"

    def __init__(self, exc=None):
        self._exc = exc

    def stop(self):
        if self._exc:
            raise self._exc


def test_unretryable_stop_drops_the_registration(monkeypatch):
    """
    Left registered, the updater reads as "ingestion still pending" to the
    report barrier for the life of the process - the block the user hits.
    """
    monkeypatch.setitem(
        GraphMemoryManager._updaters,
        "sim-drop",
        _Updater(GraphIngestionIncomplete("batches failed")),
    )

    with pytest.raises(GraphIngestionIncomplete):
        GraphMemoryManager.stop_updater("sim-drop")

    assert GraphMemoryManager.get_updater("sim-drop") is None


def test_retryable_stop_keeps_the_registration(monkeypatch):
    monkeypatch.setitem(
        GraphMemoryManager._updaters,
        "sim-keep",
        _Updater(TimeoutError("worker did not stop")),
    )

    with pytest.raises(TimeoutError):
        GraphMemoryManager.stop_updater("sim-keep")

    assert GraphMemoryManager.get_updater("sim-keep") is not None

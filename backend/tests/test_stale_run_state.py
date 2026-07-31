"""
A transient run status left on disk by a dead backend must not wedge the UI.

STARTING/RUNNING/PAUSED/STOPPING only advance while the monitor thread that
owns the run is alive. If the backend is restarted mid-run - or mid-stop, which
is where the Zep drain lives - that status is persisted with nothing left to
move it. Every caller that gates on a terminal status then blocks forever: the
report barrier answers 409, and step 3 attaches to a "live" run whose
generate-report button never enables.

get_run_state settles it at read time instead. A run that finished all of its
rounds is reportable; one cut short is honestly marked failed.
"""

import json
import os

import pytest

from app.services.simulation_runner import RunnerStatus, SimulationRunner


def _write_state(tmp_path, monkeypatch, **fields):
    simulation_id = fields.pop("simulation_id", "sim-stale")
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    SimulationRunner._run_states.pop(simulation_id, None)

    sim_dir = tmp_path / simulation_id
    sim_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "simulation_id": simulation_id,
        "updated_at": "2026-07-31T06:39:21",
        **fields,
    }
    (sim_dir / "run_state.json").write_text(json.dumps(payload), encoding="utf-8")
    return simulation_id


@pytest.mark.parametrize(
    "stale_status", ["starting", "running", "paused", "stopping"]
)
def test_finished_rounds_settle_as_reportable(tmp_path, monkeypatch, stale_status):
    """All rounds ran, only finalization was cut short - the report is still valid."""
    simulation_id = _write_state(
        tmp_path,
        monkeypatch,
        simulation_id=f"sim-{stale_status}",
        runner_status=stale_status,
        current_round=50,
        total_rounds=50,
    )

    state = SimulationRunner.get_run_state(simulation_id)

    assert state.runner_status == RunnerStatus.COMPLETED
    assert state.twitter_running is False
    assert state.reddit_running is False


def test_interrupted_run_is_marked_failed(tmp_path, monkeypatch):
    simulation_id = _write_state(
        tmp_path,
        monkeypatch,
        runner_status="running",
        current_round=3,
        total_rounds=50,
    )

    state = SimulationRunner.get_run_state(simulation_id)

    assert state.runner_status == RunnerStatus.FAILED
    # The reason has to survive to the user, not just the log.
    assert "3/50" in state.error


def test_terminal_status_is_left_alone(tmp_path, monkeypatch):
    simulation_id = _write_state(
        tmp_path,
        monkeypatch,
        runner_status="failed",
        current_round=12,
        total_rounds=50,
        error="original failure",
    )

    state = SimulationRunner.get_run_state(simulation_id)

    assert state.runner_status == RunnerStatus.FAILED
    assert state.error == "original failure"


def test_a_run_this_process_owns_is_never_touched(tmp_path, monkeypatch):
    """
    The correction is for states that came off disk. A run still finalizing in
    this process lives in _run_states, which get_run_state checks first.
    """
    simulation_id = _write_state(
        tmp_path,
        monkeypatch,
        runner_status="stopping",
        current_round=50,
        total_rounds=50,
    )
    # Prime the in-memory registry the way an owning monitor would.
    live = SimulationRunner._load_run_state(simulation_id)
    live.runner_status = RunnerStatus.STOPPING
    SimulationRunner._run_states[simulation_id] = live

    try:
        assert (
            SimulationRunner.get_run_state(simulation_id).runner_status
            == RunnerStatus.STOPPING
        )
    finally:
        SimulationRunner._run_states.pop(simulation_id, None)

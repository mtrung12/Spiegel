"""Run-summary accounting: token aggregation and wall time."""

import json
import os

from app.services.simulation_runner import (
    RunnerStatus,
    SimulationRunState,
    SimulationRunner,
    _aggregate_action_log_usage,
    _elapsed_seconds,
)
from app.utils.pipeline_logger import pipeline_log


def test_elapsed_seconds_handles_missing_and_bad_stamps():
    assert _elapsed_seconds("2026-07-30T10:00:00", "2026-07-30T10:00:30.5") == 30.5
    assert _elapsed_seconds(None, "2026-07-30T10:00:00") is None
    assert _elapsed_seconds("not-a-stamp", "2026-07-30T10:00:00") is None


def test_aggregate_action_log_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_log, "log_dir", str(tmp_path))
    run_dir = os.path.join(pipeline_log._run_dir("sim-1"), )
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "actions.jsonl"), "w", encoding="utf-8") as f:
        for record in [
            {"component": "A", "metrics": {"prompt_tokens": 10, "completion_tokens": 5}},
            {"component": "A", "metrics": {"prompt_tokens": 1, "completion_tokens": 2,
                                           "total_tokens": 3}},
            {"component": "B", "metrics": {}},          # non-LLM action, skipped
            {"component": "B", "no": "metrics"},        # skipped
            "not json",
        ]:
            f.write((record if isinstance(record, str) else json.dumps(record)) + "\n")

    result = _aggregate_action_log_usage("sim-1")
    assert result["total"] == {
        "calls": 2, "prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18
    }
    assert set(result["by_component"]) == {"A"}


def test_aggregate_action_log_usage_without_a_log(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_log, "log_dir", str(tmp_path))
    assert _aggregate_action_log_usage("missing")["total"]["calls"] == 0


def test_summary_is_written_under_the_pipeline_log_root(tmp_path, monkeypatch):
    """
    The repo root is not writable in the container - it runs as uid 10001 and
    /app is root-owned - so a summary written there is silently lost on every
    run. It has to land under the mounted log root.
    """
    log_root = tmp_path / "logs"
    monkeypatch.setattr(pipeline_log, "log_dir", str(log_root))
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path / "sims"))

    sim_dir = tmp_path / "sims" / "sim-x"
    sim_dir.mkdir(parents=True)
    (sim_dir / "token_usage.json").write_text(
        json.dumps({"total": {"calls": 3, "total_tokens": 1200}}), encoding="utf-8"
    )

    state = SimulationRunState(simulation_id="sim-x")
    state.runner_status = RunnerStatus.COMPLETED
    state.started_at = "2026-07-30T10:00:00"
    state.completed_at = "2026-07-30T10:05:30"
    state.twitter_actions_count = 7

    path = SimulationRunner._write_run_summary("sim-x", state)

    assert path is not None
    assert os.path.commonpath([path, str(log_root)]) == str(log_root)
    summary = json.loads(open(path, encoding="utf-8").read())
    assert summary["elapsed_seconds"] == 330.0
    assert summary["simulation_tokens"]["total"]["total_tokens"] == 1200
    assert summary["total_actions"] == 7
    assert summary["run_id"] == os.path.basename(os.path.dirname(path))

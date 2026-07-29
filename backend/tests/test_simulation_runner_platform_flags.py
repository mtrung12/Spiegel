"""
One runner script serves all three platform choices, so the platform has to
arrive as a command-line flag. Getting that wrong is silent: the run starts,
looks healthy, and simulates the wrong platform.
"""

import json

import pytest

from app.services import simulation_runner as runner_module
from app.services.simulation_runner import SimulationRunner


def _capture_cmd(monkeypatch, tmp_path, simulation_id, platform):
    """Start a run far enough to capture the argv, then abort it."""
    sim_dir = tmp_path / "runs" / simulation_id
    scripts_dir = tmp_path / "scripts"
    sim_dir.mkdir(parents=True)
    scripts_dir.mkdir()
    (sim_dir / "simulation_config.json").write_text(
        json.dumps({"time_config": {"total_simulation_hours": 1, "minutes_per_round": 60}}),
        encoding="utf-8",
    )
    (scripts_dir / "run_parallel_simulation.py").write_text("pass\n", encoding="utf-8")

    captured = []

    class Process:
        pid = 123

        def poll(self):
            return None

    def fake_popen(cmd, *_args, **_kwargs):
        captured.append(cmd)
        return Process()

    class BrokenThread:
        """Aborts the start right after Popen, once argv is already recorded."""

        def __init__(self, **_kwargs):
            pass

        def start(self):
            raise RuntimeError("stop here")

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path / "runs"))
    monkeypatch.setattr(SimulationRunner, "SCRIPTS_DIR", str(scripts_dir))
    monkeypatch.setattr(runner_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(runner_module.threading, "Thread", BrokenThread)
    monkeypatch.setattr(
        SimulationRunner,
        "_terminate_process",
        classmethod(lambda _cls, _process, _sim_id: None),
    )
    monkeypatch.setattr(
        SimulationRunner,
        "_sync_simulation_status",
        classmethod(lambda _cls, *_args, **_kwargs: None),
    )

    try:
        with pytest.raises(RuntimeError, match="stop here"):
            SimulationRunner.start_simulation(
                simulation_id,
                platform=platform,
                enable_graph_memory_update=False,
            )
    finally:
        for registry in (
            SimulationRunner._run_states,
            SimulationRunner._processes,
            SimulationRunner._action_queues,
            SimulationRunner._stdout_files,
            SimulationRunner._stderr_files,
            SimulationRunner._graph_memory_enabled,
        ):
            registry.pop(simulation_id, None)

    assert captured, "Popen was never called"
    return captured[0]


@pytest.mark.parametrize(
    "platform, expected_flag",
    [
        ("twitter", "--twitter-only"),
        ("reddit", "--reddit-only"),
        ("parallel", None),
    ],
)
def test_platform_selects_the_single_runner_and_its_flag(
    monkeypatch, tmp_path, platform, expected_flag
):
    cmd = _capture_cmd(monkeypatch, tmp_path, f"sim-{platform}", platform)

    assert cmd[1].endswith("run_parallel_simulation.py")
    if expected_flag:
        assert expected_flag in cmd
    else:
        # Both platforms run: neither restricting flag may be present.
        assert "--twitter-only" not in cmd
        assert "--reddit-only" not in cmd

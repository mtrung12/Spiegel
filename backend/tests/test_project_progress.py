"""
Step navigation: a returning user may jump to any step they already reached,
and to none they have not.

Getting this wrong is silent in both directions. Too strict and a finished
project greys out its own report - the step-1 view holds no simulation or
report id, so it used to disable steps 2-5 for every returning user. Too loose
and the stepper routes to a page with no id to load.
"""

from types import SimpleNamespace

from app.api.graph import _project_progress
from app.models.project import ProjectStatus
from app.services.report_agent import ReportStatus
from app.services.simulation_manager import SimulationStatus
from app.services.simulation_runner import RunnerStatus


def _project(status=ProjectStatus.GRAPH_COMPLETED, ontology=None):
    return SimpleNamespace(
        project_id="proj-1", status=status, ontology=ontology or {"entity_types": []}
    )


def _simulation(created_at="2026-07-30T00:00:00", config_generated=True):
    return SimpleNamespace(
        simulation_id="sim-1",
        created_at=created_at,
        status=SimulationStatus.READY,
        config_generated=config_generated,
    )


def _reachable(progress):
    return [s["step"] for s in progress["steps"] if s["reachable"]]


def test_a_fresh_project_can_only_open_step_one(monkeypatch):
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(lambda _cls, _sid: None),
    )
    progress = _project_progress(
        _project(status=ProjectStatus.CREATED, ontology=None), [], {}
    )

    assert _reachable(progress) == [1]
    assert progress["furthest_step"] == 1
    assert progress["simulation_id"] is None


def test_a_built_graph_opens_step_two(monkeypatch):
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(lambda _cls, _sid: None),
    )
    # No simulation yet, so step 2 has no id to route to even though the
    # project has progressed far enough to create one.
    progress = _project_progress(_project(), [], {})

    assert progress["stage"] == "simulation"
    assert _reachable(progress) == [1]


def test_a_prepared_simulation_opens_step_three(monkeypatch):
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(lambda _cls, _sid: None),
    )
    progress = _project_progress(_project(), [_simulation()], {})

    assert _reachable(progress) == [1, 2, 3]
    assert progress["simulation_id"] == "sim-1"
    # Step 4 has no report to open.
    assert progress["steps"][3]["route_id"] is None


def test_a_completed_report_opens_every_step(monkeypatch):
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(
            lambda _cls, _sid: SimpleNamespace(
                runner_status=RunnerStatus.STOPPED, current_round=50, total_rounds=50
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.graph.ReportManager.get_report",
        classmethod(
            lambda _cls, _rid: SimpleNamespace(status=ReportStatus.COMPLETED)
        ),
    )
    progress = _project_progress(
        _project(), [_simulation()], {"sim-1": "report-1"}
    )

    assert _reachable(progress) == [1, 2, 3, 4, 5]
    assert progress["report_id"] == "report-1"


def test_a_report_still_generating_does_not_open_step_five(monkeypatch):
    """Step 5 reads a finished report; a half-written one has nothing to say."""
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(
            lambda _cls, _sid: SimpleNamespace(
                runner_status=RunnerStatus.STOPPED, current_round=50, total_rounds=50
            )
        ),
    )
    monkeypatch.setattr(
        "app.api.graph.ReportManager.get_report",
        classmethod(
            lambda _cls, _rid: SimpleNamespace(status=ReportStatus.GENERATING)
        ),
    )
    progress = _project_progress(
        _project(), [_simulation()], {"sim-1": "report-1"}
    )

    assert _reachable(progress) == [1, 2, 3, 4]


def test_open_project_lands_on_the_last_step_reached(monkeypatch):
    """
    The single "Open project" button routes by furthest_step, so the step it
    lands on and the steps the stepper unlocks come from one calculation. If
    they were derived separately the button could open a step the stepper then
    shows as locked.
    """
    from app.api.graph import _furthest_step

    monkeypatch.setattr(
        "app.api.graph.ReportManager.get_report",
        classmethod(
            lambda _cls, _rid: SimpleNamespace(status=ReportStatus.COMPLETED)
        ),
    )

    # Unfinished projects open where they stopped.
    assert _furthest_step("upload", None) == 1
    assert _furthest_step("graph", None) == 1
    assert _furthest_step("simulation", None) == 2
    assert _furthest_step("run", None) == 3
    # A finished report is the end of the workflow.
    assert _furthest_step("report", "report-1") == 5


def test_open_project_stops_at_the_report_while_it_is_generating(monkeypatch):
    from app.api.graph import _furthest_step

    monkeypatch.setattr(
        "app.api.graph.ReportManager.get_report",
        classmethod(
            lambda _cls, _rid: SimpleNamespace(status=ReportStatus.GENERATING)
        ),
    )

    assert _furthest_step("report", "report-1") == 4


def test_a_failed_project_opens_on_step_one(monkeypatch):
    from app.api.graph import _furthest_step

    assert _furthest_step("failed", None) == 1


def test_the_newest_simulation_is_the_one_resumed(monkeypatch):
    monkeypatch.setattr(
        "app.api.graph.SimulationRunner.get_run_state",
        classmethod(lambda _cls, _sid: None),
    )
    older = _simulation(created_at="2026-07-01T00:00:00")
    newer = _simulation(created_at="2026-07-29T00:00:00")
    newer.simulation_id = "sim-newer"

    progress = _project_progress(_project(), [older, newer], {})

    assert progress["simulation_id"] == "sim-newer"

"""The home page reads projects, so /project/list must carry the resume point."""

import os
from types import SimpleNamespace

import pytest

from app import create_app
from app.models.project import ProjectManager, ProjectStatus
from app.services.report_agent import ReportManager
from app.services.simulation_manager import SimulationManager
from app.services.simulation_runner import RunnerStatus, SimulationRunner


@pytest.fixture
def client(tmp_path, monkeypatch):
    """An app whose projects, simulations and reports live under tmp_path."""

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path / "sims"))
    monkeypatch.setattr(ReportManager, "REPORTS_DIR", str(tmp_path / "reports"))
    # No run state and no reports unless a test says otherwise.
    monkeypatch.setattr(SimulationRunner, "get_run_state", staticmethod(lambda _id: None))
    monkeypatch.setattr(ReportManager, "list_reports", classmethod(lambda cls, **kw: []))

    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def _projects(client):
    response = client.get("/api/graph/project/list")
    assert response.status_code == 200
    return response.json["data"]


def test_project_without_a_run_reports_the_stage_it_stopped_at(client):
    project = ProjectManager.create_project(name="Spring launch")

    rows = _projects(client)
    assert [row["project_id"] for row in rows] == [project.project_id]
    assert rows[0]["name"] == "Spring launch"
    assert rows[0]["stage"] == "upload"
    assert rows[0]["simulation_id"] is None
    assert rows[0]["simulation_count"] == 0

    project.ontology = {"entity_types": []}
    project.status = ProjectStatus.ONTOLOGY_GENERATED
    ProjectManager.save_project(project)
    assert _projects(client)[0]["stage"] == "graph"

    project.status = ProjectStatus.GRAPH_COMPLETED
    ProjectManager.save_project(project)
    assert _projects(client)[0]["stage"] == "simulation"


def test_project_row_carries_its_latest_simulation(client):
    project = ProjectManager.create_project(name="Spring launch")
    manager = SimulationManager()
    older = manager.create_simulation(project.project_id, "graph-1")
    newer = manager.create_simulation(project.project_id, "graph-1")
    newer.created_at = "2999-01-01T00:00:00"
    manager._save_simulation_state(newer)

    row = _projects(client)[0]
    assert row["simulation_id"] == newer.simulation_id
    assert row["simulation_count"] == 2
    assert row["stage"] == "simulation"
    assert older.simulation_id != newer.simulation_id


def test_a_generated_report_moves_the_stage_to_report(client, monkeypatch):
    project = ProjectManager.create_project(name="Spring launch")
    simulation = SimulationManager().create_simulation(project.project_id, "graph-1")
    monkeypatch.setattr(
        ReportManager,
        "list_reports",
        classmethod(lambda cls, **kw: [
            SimpleNamespace(report_id="report_new", simulation_id=simulation.simulation_id),
            SimpleNamespace(report_id="report_old", simulation_id=simulation.simulation_id),
        ]),
    )

    row = _projects(client)[0]
    # list_reports is sorted newest first, so the first hit per simulation wins.
    assert row["report_id"] == "report_new"
    assert row["stage"] == "report"


def test_deleting_a_project_also_deletes_its_simulations(client, monkeypatch):
    project = ProjectManager.create_project(name="Spring launch")
    manager = SimulationManager()
    simulation = manager.create_simulation(project.project_id, "graph-1")
    other = ProjectManager.create_project(name="Other")
    kept = manager.create_simulation(other.project_id, "graph-2")

    response = client.delete(f"/api/graph/project/{project.project_id}")

    # Assert on disk: each request builds its own manager, so an in-memory
    # cache would answer for a directory that is already gone.
    def sim_dir(simulation_id):
        return os.path.join(SimulationManager.SIMULATION_DATA_DIR, simulation_id)

    assert response.status_code == 200
    assert not os.path.isdir(sim_dir(simulation.simulation_id))
    assert os.path.isdir(sim_dir(kept.simulation_id))


def test_a_running_simulation_blocks_the_project_delete(client, monkeypatch):
    project = ProjectManager.create_project(name="Spring launch")
    simulation = SimulationManager().create_simulation(project.project_id, "graph-1")
    monkeypatch.setattr(
        SimulationRunner,
        "get_run_state",
        staticmethod(lambda _id: SimpleNamespace(
            runner_status=RunnerStatus.RUNNING,
            current_round=3,
            total_rounds=10,
        )),
    )

    response = client.delete(f"/api/graph/project/{project.project_id}")

    assert response.status_code == 409
    assert simulation.simulation_id in response.json["error"]
    assert ProjectManager.get_project(project.project_id) is not None

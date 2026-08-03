"""A project can be renamed from inside it, not only at creation."""

import pytest

from app import create_app
from app.models.project import ProjectManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path / "projects"))
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


def test_rename_persists_the_new_name(client):
    project = ProjectManager.create_project(name="Unnamed Project")

    response = client.patch(
        f"/api/graph/project/{project.project_id}", json={"name": "  Spring launch  "}
    )

    assert response.status_code == 200
    assert response.json["data"]["name"] == "Spring launch"
    assert ProjectManager.get_project(project.project_id).name == "Spring launch"


def test_a_blank_name_is_refused_and_leaves_the_old_one(client):
    project = ProjectManager.create_project(name="Spring launch")

    response = client.patch(f"/api/graph/project/{project.project_id}", json={"name": "   "})

    assert response.status_code == 400
    assert ProjectManager.get_project(project.project_id).name == "Spring launch"


def test_renaming_a_missing_project_is_a_404(client):
    assert client.patch("/api/graph/project/proj_nope", json={"name": "x"}).status_code == 404

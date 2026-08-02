import io

from app import create_app
from app.api import graph as graph_api
from app.models.project import ProjectManager, ProjectStatus
from app.models.task import TERMINAL_STATUSES, TaskManager, TaskStatus
from app.utils.llm_client import LLMResponseError


# Long enough to clear the empty-extraction guard in the upload handler; these
# tests are about how a failing generator is reported, not about the document.
_SOURCE_DOCUMENT = (
    b"A source document for the campaign brief. " * 8
)


def _post_ontology(client):
    return client.post(
        "/api/graph/ontology/generate",
        data={
            "simulation_requirement": "Simulate the discussion.",
            "files": (io.BytesIO(_SOURCE_DOCUMENT), "source.md"),
        },
        content_type="multipart/form-data",
    )


def _await_task(task_id, timeout=5.0):
    """
    Block until the background ontology task settles.

    The upload response no longer carries the LLM's outcome - it returns as soon
    as the documents are stored, so the generator's failure lands on the task
    rather than on the POST.
    """
    import time

    manager = TaskManager()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = manager.get_task(task_id)
        if task is not None and task.status in TERMINAL_STATUSES:
            return task
        time.sleep(0.02)
    raise AssertionError(f"ontology task {task_id} did not finish within {timeout}s")


def test_ontology_api_returns_safe_truncation_error_and_failed_project(
    tmp_path,
    monkeypatch,
):
    class FailingGenerator:
        def generate(self, **kwargs):
            raise LLMResponseError(
                "LLM JSON output was truncated at the token limit",
                finish_reason="length",
            )

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app()
    app.config.update(TESTING=True)
    response = _post_ontology(app.test_client())

    # The upload itself succeeded: the files are stored and the project exists.
    assert response.status_code == 200
    assert response.json["success"] is True
    project_id = response.json["data"]["project_id"]

    task = _await_task(response.json["data"]["task_id"])
    assert task.status == TaskStatus.FAILED
    assert "token limit" in task.error
    assert "traceback" not in task.to_dict()

    project = ProjectManager.get_project(project_id)
    assert project.status == ProjectStatus.FAILED
    assert project.error == task.error


def test_ontology_api_does_not_expose_provider_error_body(tmp_path, monkeypatch):
    class ProviderError(RuntimeError):
        status_code = 401
        request_id = "request-safe-id"
        body = {"error": {"message": "SECRET-PROVIDER-BODY"}}

    class FailingGenerator:
        def generate(self, **kwargs):
            raise ProviderError("SECRET-PROVIDER-BODY")

    monkeypatch.setattr(ProjectManager, "PROJECTS_DIR", str(tmp_path))
    monkeypatch.setattr(graph_api, "OntologyGenerator", FailingGenerator)

    app = create_app()
    app.config.update(TESTING=True)
    response = _post_ontology(app.test_client())

    assert response.status_code == 200
    assert "SECRET-PROVIDER-BODY" not in response.get_data(as_text=True)

    task = _await_task(response.json["data"]["task_id"])
    assert task.status == TaskStatus.FAILED
    assert "HTTP 401" in task.error
    assert "request-safe-id" in task.error
    # The provider's body reaches neither the task nor the project record.
    assert "SECRET-PROVIDER-BODY" not in str(task.to_dict())

    project = ProjectManager.get_project(response.json["data"]["project_id"])
    assert "SECRET-PROVIDER-BODY" not in str(project.to_dict())

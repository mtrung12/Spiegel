"""
Identifier validation at the API boundary.

Resource ids are joined onto filesystem paths and, in the download handlers,
handed to send_file. These tests pin the property that a traversal sequence in
an id is rejected before any handler runs, rather than reaching a path join.
"""

import pytest

from app import create_app
from app.utils.identifiers import (
    InvalidIdentifierError,
    is_valid_id,
    validate_id,
    validate_platform,
)


@pytest.fixture
def client():
    app = create_app()
    app.config.update(TESTING=True)
    return app.test_client()


TRAVERSAL_IDS = [
    "../../../../etc/passwd",
    "../..",
    "..%2f..",
    "....//....//etc",
    "sim_0123456789ab/../../x",
    "..\\..\\.env",
]


# ---- the validator itself ----

@pytest.mark.parametrize("value", TRAVERSAL_IDS)
def test_traversal_sequences_are_rejected(value):
    assert not is_valid_id("simulation_id", value)
    with pytest.raises(InvalidIdentifierError):
        validate_id("simulation_id", value)


@pytest.mark.parametrize(
    "kind,value",
    [
        ("simulation_id", "sim_0123456789ab"),
        ("project_id", "proj_0123456789ab"),
        ("report_id", "report_0123456789ab"),
        ("graph_id", "spiegel_0123456789abcdef"),
    ],
)
def test_ids_matching_the_generators_are_accepted(kind, value):
    """The patterns must accept what the id generators actually produce."""
    assert validate_id(kind, value) == value


@pytest.mark.parametrize(
    "kind,value",
    [
        ("simulation_id", "sim_abc"),            # too short
        ("simulation_id", "sim_0123456789ag"),   # 'g' is not hex
        ("simulation_id", "proj_0123456789ab"),  # wrong prefix
        ("simulation_id", ""),
        ("simulation_id", None),
        ("graph_id", "a/b"),
    ],
)
def test_malformed_ids_are_rejected(kind, value):
    assert not is_valid_id(kind, value)


def test_platform_is_restricted_to_known_values():
    assert validate_platform("twitter") == "twitter"
    assert validate_platform("reddit") == "reddit"
    for bad in ["../../etc/passwd", "", None, "mastodon"]:
        with pytest.raises(InvalidIdentifierError):
            validate_platform(bad)


# ---- enforcement at the HTTP boundary ----

@pytest.mark.parametrize(
    "path",
    [
        "/api/simulation/{id}",
        "/api/simulation/{id}/posts",
        "/api/simulation/{id}/comments",
        "/api/simulation/{id}/profiles",
        "/api/simulation/{id}/config/download",
        "/api/report/{id}",
        "/api/report/{id}/download",
        "/api/graph/project/{id}",
    ],
)
def test_path_traversal_in_url_is_rejected(client, path):
    """
    A traversal id must not reach the handler.

    404 rather than 200-with-content is the assertion that matters: the
    download routes previously passed this straight to send_file.
    """
    response = client.get(path.format(id="..%2f..%2f..%2f.env"))
    assert response.status_code == 404
    assert b"BEGIN" not in response.data


def test_traversal_in_json_body_is_rejected(client):
    """Body-sourced ids reach the same path joins as URL-sourced ones."""
    response = client.post(
        "/api/simulation/prepare",
        json={"simulation_id": "../../../../etc/passwd"},
    )
    assert response.status_code == 404
    assert response.json["success"] is False


def test_unknown_platform_is_rejected(client):
    """platform is interpolated into '{platform}_simulation.db'."""
    response = client.get(
        "/api/simulation/sim_0123456789ab/posts",
        query_string={"platform": "../../../../etc/passwd"},
    )
    assert response.status_code == 404


def test_valid_id_for_a_missing_resource_still_reaches_the_handler(client):
    """
    Validation must not swallow well-formed ids.

    A well-formed id for a resource that does not exist is the handler's
    business, so this asserts the request got that far rather than being
    rejected by the preprocessor.
    """
    response = client.get("/api/simulation/sim_0123456789ab")
    assert response.status_code == 404
    assert response.json["success"] is False
    # The handler's own message, not the validator's.
    assert "Invalid" not in response.json["error"]

"""
Error responses must not carry a stack trace.

Handlers used to return traceback.format_exc() in the JSON body, which exposes
absolute filesystem paths and installed library versions to any caller. The
detail belongs in the server log instead. These tests pin both halves of that:
nothing leaks outward, and the detail is still recorded.
"""

import logging
import re
from pathlib import Path

import pytest

from app import create_app
from app.api import simulation as simulation_api


API_SOURCES = [
    Path(__file__).resolve().parents[1] / "app" / "api" / name
    for name in ("graph.py", "simulation.py", "report.py")
]


@pytest.mark.parametrize("source", API_SOURCES, ids=lambda p: p.name)
def test_no_handler_returns_a_traceback_in_its_payload(source):
    """A source-level check: the pattern must not come back anywhere."""
    text = source.read_text(encoding="utf-8")
    assert '"traceback"' not in text, (
        f"{source.name} returns a traceback to the client"
    )


def test_unhandled_error_response_carries_no_trace(monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("boom at /srv/secret/path.py line 42")

    monkeypatch.setattr(simulation_api.SimulationManager, "list_simulations", explode)

    app = create_app()
    app.config.update(TESTING=True)
    response = app.test_client().get("/api/simulation/list")

    assert response.status_code == 500
    body = response.get_data(as_text=True)
    assert "Traceback" not in body
    assert "File \"" not in body
    assert not re.search(r"[A-Za-z]:\\\\|/site-packages/", body)


def test_the_detail_is_logged_server_side(monkeypatch, caplog):
    """
    Stripping the traceback must not lose it.

    Without this, the fix trades an information leak for an undiagnosable
    failure, which is a worse place to be.
    """
    def explode(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(simulation_api.SimulationManager, "list_simulations", explode)

    app = create_app()
    app.config.update(TESTING=True)

    with caplog.at_level(logging.ERROR, logger="spiegel.api.simulation"):
        app.test_client().get("/api/simulation/list")

    assert any(rec.exc_info for rec in caplog.records), (
        "the exception was not logged with its traceback"
    )

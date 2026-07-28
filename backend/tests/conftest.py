"""
Shared pytest configuration.

Set before any test module imports the app, so the pipeline logger picks the
disabled flag up when its singleton is constructed. Test runs would otherwise
write fixture traffic into local-doc/logs, which is meant to hold real runs.
"""

import os

import pytest

os.environ.setdefault('PIPELINE_LOG_ENABLED', 'false')


@pytest.fixture(autouse=True)
def _isolate_simulation_manager():
    """
    Give each test a fresh SimulationManager.

    It is a process-wide singleton wrapping a write-back cache over the state
    files, which is right in production and wrong in a test run: one test's
    cached state, or an attribute set on the shared instance, is still there for
    the next one. Dropping the instance between tests keeps the failures a test
    reports its own.
    """
    # Imported here rather than at module scope so the environment above is set
    # before anything under app/ is loaded.
    from app.services.simulation_manager import SimulationManager

    SimulationManager._instance = None
    try:
        yield
    finally:
        SimulationManager._instance = None

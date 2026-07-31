"""
The step-2 substeps save their choices (audience size, round count) on the
simulation state.

Both halves are silent when they break: a choice that never reaches disk looks
identical on screen to one that did, and it only shows up as the wrong cast
size or the wrong round count on the next visit.
"""

import json
import os

from app.services.simulation_manager import SimulationManager, SimulationState


def _manager(tmp_path, monkeypatch):
    # The manager is a process-wide singleton with a write-back cache, so the
    # data directory and the cache both have to be reset per test.
    monkeypatch.setattr(SimulationManager, "SIMULATION_DATA_DIR", str(tmp_path))
    manager = SimulationManager()
    manager._simulations.clear()
    return manager


def test_a_saved_user_config_survives_a_reload(tmp_path, monkeypatch):
    manager = _manager(tmp_path, monkeypatch)
    state = manager.create_simulation(project_id="proj-1", graph_id="graph-1")

    state.user_config = {"max_agents": 120}
    manager._save_simulation_state(state)
    # A later substep saves its own key; the earlier one must not be lost.
    state.user_config = {**state.user_config, "max_rounds": 25,
                         "use_custom_rounds": True}
    manager._save_simulation_state(state)

    # Drop the cache: this is what a returning user's request hits.
    manager._simulations.clear()
    reloaded = manager.get_simulation(state.simulation_id)

    assert reloaded.user_config == {
        "max_agents": 120,
        "max_rounds": 25,
        "use_custom_rounds": True,
    }


def test_a_state_file_written_before_user_config_existed_still_loads(
    tmp_path, monkeypatch
):
    manager = _manager(tmp_path, monkeypatch)
    state = manager.create_simulation(project_id="proj-1", graph_id="graph-1")
    state_file = os.path.join(tmp_path, state.simulation_id, "state.json")

    data = json.load(open(state_file, encoding="utf-8"))
    del data["user_config"]
    with open(state_file, "w", encoding="utf-8") as f:
        json.dump(data, f)

    manager._simulations.clear()
    assert manager.get_simulation(state.simulation_id).user_config == {}


def test_the_state_defaults_to_no_saved_choices():
    assert SimulationState(
        simulation_id="s", project_id="p", graph_id="g"
    ).user_config == {}

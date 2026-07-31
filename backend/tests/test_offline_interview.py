"""
Interviews must still work once the OASIS environment has closed.

The live path needs the simulation child process parked in IPC command-wait
mode. That process dies with a backend restart and closes itself on its idle
timeout, while step 5 only unlocks after the report - so the fallback is the
path most real interviews take.
"""

import json
import sqlite3

import pytest

from app.services import offline_interview
from app.services.simulation_runner import SimulationRunner


AGENT_ID = 0
POST_TEXT = "This estate car finally fits the dog and the kids."
COMMENT_TEXT = "Still too expensive for what it is."


class StubLLM:
    """Records the system prompts it was handed and answers in character."""

    def __init__(self):
        self.system_prompts = []

    def chat(self, messages, **kwargs):
        self.system_prompts.append(messages[0]["content"])
        return "I would buy it, but only once the price drops."


def _write_platform_db(sim_dir, platform):
    conn = sqlite3.connect(sim_dir / f"{platform}_simulation.db")
    conn.executescript(
        """
        CREATE TABLE post (post_id INTEGER PRIMARY KEY, user_id INTEGER,
            content TEXT, created_at TEXT, num_likes INTEGER, num_dislikes INTEGER);
        CREATE TABLE comment (comment_id INTEGER PRIMARY KEY, post_id INTEGER,
            user_id INTEGER, content TEXT, created_at TEXT,
            num_likes INTEGER, num_dislikes INTEGER);
        """
    )
    conn.execute(
        "INSERT INTO post VALUES (1, ?, ?, '2026-07-30T05:00:00', 4, 1)",
        (AGENT_ID, POST_TEXT),
    )
    conn.execute(
        "INSERT INTO comment VALUES (1, 1, ?, ?, '2026-07-30T05:01:00', 2, 0)",
        (AGENT_ID, COMMENT_TEXT),
    )
    # Another agent's content must never reach this agent's prompt
    conn.execute(
        "INSERT INTO post VALUES (2, 7, 'Unrelated agent post', '2026-07-30T05:02:00', 0, 0)"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def simulation(tmp_path, monkeypatch):
    """A finished simulation on disk: profiles plus both platform databases."""
    sim_id = "sim_offline_test"
    sim_dir = tmp_path / sim_id
    sim_dir.mkdir()

    (sim_dir / "reddit_profiles.json").write_text(
        json.dumps([{
            "realname": "Petra Novak",
            "username": "petra_n",
            "bio": "Parent of two, commutes daily",
            "persona": "Practical, price sensitive",
            "profession": "teacher",
        }]),
        encoding="utf-8",
    )
    for platform in ("twitter", "reddit"):
        _write_platform_db(sim_dir, platform)

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    return sim_id


@pytest.fixture
def stub_llm(monkeypatch):
    llm = StubLLM()
    monkeypatch.setattr(
        offline_interview.LLMClient, "for_chatbot", classmethod(lambda cls: llm)
    )
    return llm


def test_batch_answers_on_every_available_platform(simulation, stub_llm):
    result = offline_interview.interview_batch(
        simulation, [{"agent_id": AGENT_ID, "prompt": "would you buy the car"}]
    )

    assert result["success"] is True
    assert result["offline"] is True
    # The frontend and the report agent both index by "<platform>_<agent_id>"
    results = result["result"]["results"]
    assert set(results) == {f"twitter_{AGENT_ID}", f"reddit_{AGENT_ID}"}
    for entry in results.values():
        assert entry["response"]


def test_prompt_carries_the_persona_and_only_this_agents_activity(simulation, stub_llm):
    offline_interview.interview_batch(
        simulation,
        [{"agent_id": AGENT_ID, "prompt": "would you buy the car", "platform": "reddit"}],
    )

    assert len(stub_llm.system_prompts) == 1
    prompt = stub_llm.system_prompts[0]
    assert "Petra Novak" in prompt
    assert POST_TEXT in prompt
    assert COMMENT_TEXT in prompt
    assert "Unrelated agent post" not in prompt


def test_single_agent_result_keeps_the_live_dual_platform_shape(simulation, stub_llm):
    result = offline_interview.interview_agent(
        simulation, AGENT_ID, "would you buy the car"
    )

    assert result["success"] is True
    assert set(result["result"]["platforms"]) == {"twitter", "reddit"}


def test_runner_falls_back_when_the_environment_is_gone(simulation, stub_llm):
    """The env is not running here, so no IPC stub is needed for this to pass."""
    result = SimulationRunner.interview_agents_batch(
        simulation_id=simulation,
        interviews=[{"agent_id": AGENT_ID, "prompt": "would you buy the car"}],
    )

    assert result["success"] is True
    assert result["offline"] is True


def test_missing_profiles_is_reported_not_silently_empty(tmp_path, monkeypatch):
    sim_dir = tmp_path / "sim_bare"
    sim_dir.mkdir()
    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="no agent profiles"):
        offline_interview.interview_batch(
            "sim_bare", [{"agent_id": 0, "prompt": "hello"}]
        )

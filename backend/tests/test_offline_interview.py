"""
Interviews must still work once the OASIS environment has closed.

The live path needs the simulation child process parked in IPC command-wait
mode. That process dies with a backend restart and closes itself on its idle
timeout, while step 5 only unlocks after the report - so the fallback is the
path most real interviews take.
"""

import json
import sqlite3
import threading

import pytest

from app.services import offline_interview
from app.services.simulation_runner import SimulationRunner


AGENT_ID = 0
POST_TEXT = "This estate car finally fits the dog and the kids."
COMMENT_TEXT = "Still too expensive for what it is."
SEED_TEXT = "Meet the new Aurora estate: 700 km of range, family sized."
RECALL_TEXT = 'Audience content matching: "the boot is smaller than advertised"'


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
        CREATE TABLE user (user_id INTEGER PRIMARY KEY, name TEXT, user_name TEXT);
        INSERT INTO user VALUES (0, 'Petra Novak', 'petra_n'),
                                (7, 'Someone Else', 'someone'),
                                (99, 'Aurora Motors', 'aurora');
        """
    )
    conn.execute(
        "INSERT INTO post VALUES (1, ?, ?, '2026-07-30T05:00:00', 4, 1)",
        (AGENT_ID, POST_TEXT),
    )
    # The campaign creative, seeded into the feed before round 0
    conn.execute(
        "INSERT INTO post VALUES (3, 99, ?, '2026-07-30T04:00:00', 30, 2)",
        (SEED_TEXT,),
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
    (sim_dir / "simulation_config.json").write_text(
        json.dumps({"event_config": {"initial_posts": [{"content": SEED_TEXT}]}}),
        encoding="utf-8",
    )
    for platform in ("twitter", "reddit"):
        _write_platform_db(sim_dir, platform)

    monkeypatch.setattr(SimulationRunner, "RUN_STATE_DIR", str(tmp_path))
    return sim_id


class StubContentIndex:
    """Stands in for Qdrant: records the searches, returns canned recall."""

    def __init__(self, indexed: int = 42):
        self.indexed = indexed
        self.searches = []
        self.indexed_simulations = []

    def indexed_count(self, simulation_id):
        return self.indexed

    def search_as_text(self, simulation_id, query, limit=10, kind=None, platform=None):
        self.searches.append((simulation_id, query, platform))
        return RECALL_TEXT

    def index_simulation(self, simulation_id, force=False):
        self.indexed_simulations.append(simulation_id)
        return 7


@pytest.fixture(autouse=True)
def content_index(monkeypatch):
    """No test may reach the real embedded Qdrant store or the embedding endpoint."""
    stub = StubContentIndex()
    monkeypatch.setattr(
        "app.services.content_index.ContentIndexService", lambda: stub
    )
    return stub


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


def test_prompt_carries_the_campaign_creative_and_retrieved_discussion(
    simulation, stub_llm, content_index
):
    """The agent must be shown the campaign it is being asked about."""
    offline_interview.interview_batch(
        simulation,
        [{"agent_id": AGENT_ID, "prompt": "would you buy the car", "platform": "reddit"}],
    )

    prompt = stub_llm.system_prompts[0]
    assert SEED_TEXT in prompt
    assert RECALL_TEXT in prompt
    assert content_index.searches == [(simulation, "would you buy the car", "reddit")]
    # The creative is the brand's post, never counted as this agent's activity
    assert f'You posted (2026-07-30T04:00:00' not in prompt


def test_retrieval_runs_once_per_question_not_once_per_agent(
    simulation, stub_llm, content_index
):
    """A global interview is one question times N agents; N embeddings is waste."""
    offline_interview.interview_batch(
        simulation,
        [
            {"agent_id": agent_id, "prompt": "would you buy the car", "platform": "reddit"}
            for agent_id in (0, 0, 0)
        ],
    )

    assert len(stub_llm.system_prompts) == 3
    assert len(content_index.searches) == 1


def test_an_unindexed_simulation_answers_now_and_indexes_in_the_background(
    simulation, stub_llm, content_index
):
    """Embedding a whole feed inline would hang an interactive request."""
    content_index.indexed = 0

    result = offline_interview.interview_batch(
        simulation,
        [{"agent_id": AGENT_ID, "prompt": "would you buy the car", "platform": "reddit"}],
    )

    assert result["success"] is True
    assert content_index.searches == []
    assert RECALL_TEXT not in stub_llm.system_prompts[0]
    # The creative does not come from the index, so it is still there
    assert SEED_TEXT in stub_llm.system_prompts[0]

    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=5)
    assert content_index.indexed_simulations == [simulation]


def test_a_broken_content_index_still_yields_an_answer(simulation, stub_llm, content_index):
    def boom(*args, **kwargs):
        raise RuntimeError("qdrant is down")

    content_index.search_as_text = boom

    result = offline_interview.interview_batch(
        simulation, [{"agent_id": AGENT_ID, "prompt": "would you buy the car"}]
    )

    assert result["success"] is True


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

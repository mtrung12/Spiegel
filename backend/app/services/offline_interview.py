"""
Offline interviews.

The live interview path drives the running OASIS environment over IPC. That
environment does not survive a backend restart, and closes itself after its idle
timeout, while step 5 of the UI only unlocks once the (slow) report is finished -
so by the time anyone interviews an agent, the environment is usually gone.

This module answers as an agent without that process: the persona from the
generated profiles plus everything that agent actually posted and commented in
the platform database, handed to the chatbot LLM. It is a reconstruction, not
the agent's live in-environment memory, so results carry `offline: True`.

Retrieval
---------
An agent's own authored rows are not enough context to answer with. The campaign
creative it was reacting to lives in the seed posts, and the conversation around
it lives in everybody else's posts and comments - so an agent asked "would you
buy this car?" would otherwise answer from persona alone, having never been
shown the car. Each interview therefore also gets:

* the campaign creative for its platform, read straight from the feed database
* the passages of audience discussion most relevant to the question, from the
  Qdrant content index (``content_index``)

The retrieval is per (platform, question), not per agent: a global interview is
150 agents asked one question, and they all need the same context. One embedding
call per distinct question is what keeps that affordable.
"""

import csv
import json
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger

logger = get_logger('spiegel.offline_interview')

PLATFORMS = ("twitter", "reddit")

# Authored items handed to the model per agent, newest first
MAX_ACTIVITY_ITEMS = 20

# Campaign creative shown to the agent, and how much of each post
MAX_SEED_POSTS = 5
SEED_EXCERPT_CHARS = 600

# Audience passages retrieved per question
MAX_RECALL_ITEMS = 5

# ponytail: fixed pool; a global interview is 150 agents x 2 platforms, and one
# call each is what makes that finish inside the request timeout. Move to a
# queue if interviews ever need to outlive a request.
MAX_WORKERS = 8


def _sim_dir(simulation_id: str) -> str:
    from .simulation_runner import SimulationRunner
    return os.path.join(SimulationRunner.RUN_STATE_DIR, simulation_id)


def load_agent_profiles(simulation_id: str) -> List[Dict[str, Any]]:
    """
    Load the generated agent personas for a simulation, indexed by agent_id.

    Prefers the Reddit JSON profiles and falls back to the Twitter CSV, which
    is normalised onto the same shape.
    """
    sim_dir = _sim_dir(simulation_id)
    profiles: List[Dict[str, Any]] = []

    reddit_profile_path = os.path.join(sim_dir, "reddit_profiles.json")
    if os.path.exists(reddit_profile_path):
        try:
            with open(reddit_profile_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.warning(f"failed to read reddit_profiles.json: {e}")

    twitter_profile_path = os.path.join(sim_dir, "twitter_profiles.csv")
    if os.path.exists(twitter_profile_path):
        try:
            with open(twitter_profile_path, 'r', encoding='utf-8') as f:
                for row in csv.DictReader(f):
                    profiles.append({
                        "realname": row.get("name", ""),
                        "username": row.get("username", ""),
                        "bio": row.get("description", ""),
                        "persona": row.get("user_char", ""),
                        "profession": "unknown",
                    })
        except Exception as e:
            logger.warning(f"failed to read twitter_profiles.csv: {e}")

    return profiles


def available_platforms(simulation_id: str) -> List[str]:
    """The platforms this simulation actually has a database for."""
    sim_dir = _sim_dir(simulation_id)
    return [
        p for p in PLATFORMS
        if os.path.exists(os.path.join(sim_dir, f"{p}_simulation.db"))
    ]


def _agent_activity(simulation_id: str, platform: str, agent_id: int) -> List[str]:
    """
    What this agent wrote on one platform, newest first.

    Mirrors the live path's convention that the database user_id is the
    agent_id (see SimulationRunner._get_interview_history_from_db).
    """
    db_path = os.path.join(_sim_dir(simulation_id), f"{platform}_simulation.db")
    if not os.path.exists(db_path):
        return []

    lines: List[str] = []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        for kind, table in (("posted", "post"), ("commented", "comment")):
            try:
                rows = conn.execute(
                    f"""
                    SELECT content, created_at, num_likes, num_dislikes
                    FROM {table}
                    WHERE user_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (agent_id, MAX_ACTIVITY_ITEMS),
                ).fetchall()
            except sqlite3.OperationalError as e:
                logger.warning(f"{platform}: failed to read the {table} table: {e}")
                continue
            for row in rows:
                content = (row["content"] or "").strip()
                if not content:
                    continue
                lines.append(
                    f"- You {kind} ({row['created_at']}, "
                    f"{row['num_likes'] or 0} likes / {row['num_dislikes'] or 0} dislikes): "
                    f"{content}"
                )
    finally:
        conn.close()

    return lines[:MAX_ACTIVITY_ITEMS]


def _campaign_creative(simulation_id: str) -> Dict[str, str]:
    """
    The campaign's own posts, per platform - what the audience was reacting to.

    Read from the same feed databases the sentiment digest reads, which already
    separate seed posts from audience content, so the creative needs no
    heuristics here.
    """
    try:
        from .content_sentiment import ContentSentimentService
        _, seeds = ContentSentimentService._collect(simulation_id)
    except Exception as e:
        logger.warning(f"could not read the campaign creative for {simulation_id}: {e}")
        return {}

    blocks: Dict[str, List[str]] = {}
    for seed in seeds:
        lines = blocks.setdefault(seed.platform, [])
        if len(lines) >= MAX_SEED_POSTS:
            continue
        content = (seed.content or "").strip()
        if not content:
            continue
        if len(content) > SEED_EXCERPT_CHARS:
            content = content[:SEED_EXCERPT_CHARS] + "..."
        lines.append(f'- {seed.author_name}: "{content}"')

    return {platform: "\n".join(lines) for platform, lines in blocks.items()}


def _recall(simulation_id: str, prompt: str, platform: str) -> str:
    """
    Audience discussion relevant to this question, from the content index.

    Never builds the index inline. Embedding a whole finished feed takes minutes,
    and an interview is an interactive request - so an unindexed simulation is
    warmed on a background thread and answered without recall this once, rather
    than left hanging. The runner warms every finished run anyway; this covers
    runs that finished before that existed, or whose warm failed.

    Best effort throughout: the interview is still answerable from persona and
    own activity, so an index or embedding failure degrades the answer rather
    than failing the request.
    """
    if not prompt.strip():
        return ""
    try:
        from .content_index import ContentIndexService
        service = ContentIndexService()
        if service.indexed_count(simulation_id) == 0:
            logger.info(f"content index empty for {simulation_id}; warming in background")
            threading.Thread(
                target=_warm_index, args=(simulation_id,), daemon=True
            ).start()
            return ""
        return service.search_as_text(
            simulation_id, prompt, limit=MAX_RECALL_ITEMS, platform=platform
        )
    except Exception as e:
        logger.warning(f"content recall failed: {platform}, {e}")
        return ""


def _warm_index(simulation_id: str) -> None:
    """Build the content index off the request thread."""
    try:
        from .content_index import ContentIndexService
        count = ContentIndexService().index_simulation(simulation_id)
        logger.info(f"content index warmed from an interview: {simulation_id}, points={count}")
    except Exception as e:
        logger.warning(f"could not warm the content index for {simulation_id}: {e}")


def _persona_block(profile: Dict[str, Any], agent_id: int) -> str:
    name = profile.get("realname") or profile.get("username") or f"Agent_{agent_id}"
    fields = [
        f"Name: {name}",
        f"Occupation: {profile.get('profession', 'unknown')}",
        f"Bio: {profile.get('bio', '')}",
        f"Persona: {profile.get('persona', '')}",
    ]
    topics = profile.get("interested_topics")
    if topics:
        fields.append(f"Interests: {topics}")
    return "\n".join(f for f in fields if f.split(': ', 1)[-1].strip())


def _interview_one(
    simulation_id: str,
    agent_id: int,
    prompt: str,
    platform: str,
    profile: Dict[str, Any],
    llm: LLMClient,
    creative: str = "",
    recall: str = "",
) -> Dict[str, Any]:
    """Answer one question as one agent, on one platform."""
    activity = _agent_activity(simulation_id, platform, agent_id)
    activity_block = (
        "\n".join(activity) if activity
        else "- (you did not post or comment during the campaign)"
    )

    sections = [
        f"Your persona:\n{_persona_block(profile, agent_id)}",
        f"What you did on {platform} during the campaign:\n{activity_block}",
    ]
    if creative:
        sections.append(f"The campaign posts you saw in your feed:\n{creative}")
    if recall:
        sections.append(
            "What was being said around you, relevant to this question "
            f"(other people's words, not yours):\n{recall}"
        )

    system_prompt = (
        f"You are a simulated {platform} user being interviewed after a marketing "
        "campaign ran in your feed. Stay in character, answer in the first person "
        "and in plain text. Never mention that you are an AI or a simulation, do "
        "not call tools, and do not use Markdown headings. Base your answer on "
        "your persona, on the campaign posts below and on what you actually did "
        "during the campaign; if the record does not cover something, answer the "
        "way this persona would. Never speak as anyone else, and never repeat "
        "another person's opinion as your own - the surrounding discussion is "
        "context for what you were exposed to, not your view.\n\n"
        + "\n\n".join(sections)
    )

    try:
        response = llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            temperature=0.8,
        )
    except Exception as e:
        logger.error(f"offline interview failed: agent_id={agent_id}, platform={platform}, error={e}")
        return {
            "agent_id": agent_id,
            "platform": platform,
            "error": str(e),
            "offline": True,
            "timestamp": datetime.now().isoformat(),
        }

    return {
        "agent_id": agent_id,
        "response": response,
        "platform": platform,
        "offline": True,
        "timestamp": datetime.now().isoformat(),
    }


def _plan(
    simulation_id: str,
    interviews: List[Dict[str, Any]],
    platform: Optional[str],
) -> List[tuple]:
    """Expand the interview list into (agent_id, prompt, platform) jobs."""
    platforms = available_platforms(simulation_id)
    jobs = []
    for item in interviews:
        agent_id = item.get("agent_id")
        if agent_id is None:
            continue
        wanted = item.get("platform") or platform
        targets = [wanted] if wanted else platforms
        for target in targets:
            if target in platforms:
                jobs.append((int(agent_id), item.get("prompt", ""), target))
    return jobs


def interview_batch(
    simulation_id: str,
    interviews: List[Dict[str, Any]],
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Offline equivalent of SimulationRunner.interview_agents_batch.

    Returns the same envelope, so callers and the frontend read the results the
    same way, with `offline: True` marking the reconstruction.
    """
    profiles = load_agent_profiles(simulation_id)
    if not profiles:
        raise ValueError(
            f"cannot interview offline: no agent profiles for {simulation_id}"
        )

    jobs = _plan(simulation_id, interviews, platform)
    if not jobs:
        raise ValueError(
            f"cannot interview offline: no platform data for {simulation_id}"
        )

    llm = LLMClient.for_chatbot()

    # Shared context, resolved once per platform / per distinct question rather
    # than once per agent - see the module docstring.
    creative = _campaign_creative(simulation_id)
    recalls = {
        (prompt, target): _recall(simulation_id, prompt, target)
        for prompt, target in {(j[1], j[2]) for j in jobs}
    }

    logger.info(
        f"offline interview: simulation_id={simulation_id}, agents={len(interviews)}, "
        f"calls={len(jobs)}, retrievals={len(recalls)}, "
        f"creative_platforms={sorted(creative)}"
    )

    def run(job):
        agent_id, prompt, target = job
        profile = profiles[agent_id] if agent_id < len(profiles) else {}
        return _interview_one(
            simulation_id, agent_id, prompt, target, profile, llm,
            creative=creative.get(target, ""),
            recall=recalls.get((prompt, target), ""),
        )

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        outcomes = list(pool.map(run, jobs))

    results = {
        f"{outcome['platform']}_{outcome['agent_id']}": outcome
        for outcome in outcomes
    }
    answered = [o for o in outcomes if o.get("response")]

    return {
        "success": bool(answered),
        "interviews_count": len(interviews),
        "offline": True,
        "result": {
            "interviews_count": len(results),
            "results": results,
            "offline": True,
        },
        "error": None if answered else "every offline interview call failed",
        "timestamp": datetime.now().isoformat(),
    }


def interview_agent(
    simulation_id: str,
    agent_id: int,
    prompt: str,
    platform: Optional[str] = None,
) -> Dict[str, Any]:
    """Offline equivalent of SimulationRunner.interview_agent."""
    batch = interview_batch(
        simulation_id,
        [{"agent_id": agent_id, "prompt": prompt}],
        platform=platform,
    )
    results = batch["result"]["results"]

    if platform:
        result = results.get(f"{platform}_{agent_id}", {})
    else:
        result = {
            "agent_id": agent_id,
            "prompt": prompt,
            "offline": True,
            "platforms": {
                outcome["platform"]: outcome for outcome in results.values()
            },
        }

    return {
        "success": batch["success"],
        "agent_id": agent_id,
        "prompt": prompt,
        "offline": True,
        "result": result,
        "error": batch["error"],
        "timestamp": batch["timestamp"],
    }

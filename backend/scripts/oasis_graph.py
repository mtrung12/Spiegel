"""
Local Reddit agent-graph builder.

OASIS's own ``generate_reddit_agent_graph`` hardcodes the agent system message
through ``UserInfo.to_reddit_system_message``, which always states a country -
so an agent whose brief named no market would be handed one anyway, invented at
profile time. It also does not forward the ``user_info_template`` hook that
``SocialAgent`` already accepts.

This is that function with the template supplied: the country clause appears
only when the profile actually has a country. Also fixes the upstream wording,
which tells a Reddit user it is about to be shown "tweets".

Everything else - the agent graph, the sign-up flow, the action space - is
unchanged, so the rest of OASIS is untouched.
"""

import json
from typing import List, Optional, Union

from camel.models import BaseModelBackend, ModelManager
from camel.prompts import TextPrompt
from oasis.social_agent import AgentGraph, SocialAgent
from oasis.social_platform.config import UserInfo
from oasis.social_platform.typing import ActionType


def build_reddit_system_template(include_country: bool) -> TextPrompt:
    """The agent system message, with the country clause only when we have one."""
    demographics = (
        "You are a {gender}, {age} years old, with an MBTI personality type of {mbti}"
    )
    demographics += " from {country}." if include_country else "."

    return TextPrompt("\n".join([
        "",
        "# OBJECTIVE",
        "You're a Reddit user, and I'll present you with some posts. After you see "
        "the posts, choose some actions from the following functions.",
        "",
        "# SELF-DESCRIPTION",
        "Your actions should be consistent with your self-description and personality.",
        "Your name is {name}.",
        "Your have profile: {user_profile}.",
        demographics,
        "",
        "# RESPONSE METHOD",
        "Please perform actions by tool calling.",
        "",
    ]))


async def generate_reddit_agent_graph(
    profile_path: str,
    model: Optional[Union[BaseModelBackend, List[BaseModelBackend], ModelManager]] = None,
    available_actions: Optional[List[ActionType]] = None,
) -> AgentGraph:
    """
    Build the Reddit agent graph from a profile file.

    Drop-in replacement for ``oasis.generate_reddit_agent_graph``.
    """
    agent_graph = AgentGraph()

    with open(profile_path, "r", encoding="utf-8") as file:
        agent_info = json.load(file)

    # One template per file, not per agent: the market is a property of the
    # campaign, so either every profile carries a country or none does.
    include_country = any(a.get("country") for a in agent_info)
    template = build_reddit_system_template(include_country)

    for i, info in enumerate(agent_info):
        profile = {
            "name": info["username"],
            "user_profile": info["persona"],
            "gender": info.get("gender") or "other",
            "age": info.get("age") or 30,
            "mbti": info.get("mbti") or "ISTJ",
        }
        if include_country:
            profile["country"] = info.get("country") or "unspecified"

        user_info = UserInfo(
            name=info["username"],
            description=info["bio"],
            profile=profile,
            recsys_type="reddit",
        )

        agent_graph.add_agent(SocialAgent(
            agent_id=info.get("user_id", i),
            user_info=user_info,
            user_info_template=template,
            agent_graph=agent_graph,
            model=model,
            available_actions=available_actions,
        ))

    return agent_graph

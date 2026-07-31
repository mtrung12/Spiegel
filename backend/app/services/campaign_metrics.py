"""
Campaign KPI engine.

Turns the raw OASIS action log into the marketing numbers a campaign
assessment needs: reach, engagement, virality, sentiment split, share of
voice and a per-segment breakdown.

Everything here is counted from actions.jsonl through SimulationRunner. No
LLM is involved, so the report can quote these figures as measurements
rather than estimates.

Action taxonomy (see Config.OASIS_TWITTER_ACTIONS / OASIS_REDDIT_ACTIONS):
- Being activated at all counts as an impression: the agent was served the
  feed and had the chance to see the campaign.
- Engagement covers every deliberate reaction (like, dislike, comment,
  repost, quote, post, follow).
- Amplification is the subset that redistributes the message (repost, quote).
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Set

from ..utils.logger import get_logger
from .simulation_runner import SimulationRunner

logger = get_logger('spiegel.campaign_metrics')


# ═══════════════════════════════════════════════════════════════
# Action classification
# ═══════════════════════════════════════════════════════════════

# The agent was activated but chose not to react, or only consumed content.
# Still an impression - the campaign reached them.
PASSIVE_ACTIONS: Set[str] = {
    'DO_NOTHING', 'REFRESH', 'TREND', 'SEARCH_POSTS', 'SEARCH_USER',
}

# Redistribution of the message to a new audience.
AMPLIFICATION_ACTIONS: Set[str] = {'REPOST', 'QUOTE_POST'}

# Approval signals.
POSITIVE_ACTIONS: Set[str] = {
    'LIKE_POST', 'LIKE_COMMENT', 'REPOST', 'QUOTE_POST', 'FOLLOW',
}

# Rejection signals.
NEGATIVE_ACTIONS: Set[str] = {
    'DISLIKE_POST', 'DISLIKE_COMMENT', 'MUTE',
}

# Content the agent authored - the source of quotable audience reactions.
CREATION_ACTIONS: Set[str] = {'CREATE_POST', 'CREATE_COMMENT'}

# Everything that counts as a deliberate reaction to the campaign.
ENGAGEMENT_ACTIONS: Set[str] = (
    POSITIVE_ACTIONS | NEGATIVE_ACTIONS | CREATION_ACTIONS
)


def _rate(numerator: float, denominator: float) -> float:
    """Percentage, guarded against division by zero."""
    if not denominator:
        return 0.0
    return round(numerator / denominator * 100, 2)


def _ratio(numerator: float, denominator: float) -> float:
    """Plain ratio, guarded against division by zero."""
    if not denominator:
        return 0.0
    return round(numerator / denominator, 3)


@dataclass
class SegmentMetrics:
    """KPIs for one audience segment."""
    segment: str
    audience_size: int = 0
    reached_agents: int = 0
    engaged_agents: int = 0
    engagement_actions: int = 0
    positive_actions: int = 0
    negative_actions: int = 0
    amplifications: int = 0
    posts_authored: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "segment": self.segment,
            "audience_size": self.audience_size,
            "reached_agents": self.reached_agents,
            "engaged_agents": self.engaged_agents,
            "reach_rate_pct": _rate(self.reached_agents, self.audience_size),
            "engagement_rate_pct": _rate(self.engaged_agents, self.reached_agents),
            "engagement_actions": self.engagement_actions,
            "positive_actions": self.positive_actions,
            "negative_actions": self.negative_actions,
            "net_sentiment": _ratio(
                self.positive_actions - self.negative_actions,
                self.positive_actions + self.negative_actions,
            ),
            "amplifications": self.amplifications,
            "posts_authored": self.posts_authored,
        }


@dataclass
class CampaignKPIs:
    """The full KPI set for one simulated campaign."""
    simulation_id: str

    # Reach / exposure
    audience_size: int = 0
    reached_agents: int = 0
    engaged_agents: int = 0
    total_impressions: int = 0

    # Engagement
    total_actions: int = 0
    engagement_actions: int = 0
    posts_authored: int = 0
    comments_authored: int = 0
    likes: int = 0
    dislikes: int = 0
    follows: int = 0

    # Virality
    amplifications: int = 0
    reposts: int = 0
    quotes: int = 0
    amplifier_agents: int = 0
    cascade_depth_rounds: int = 0
    peak_round: int = 0
    peak_round_actions: int = 0

    # Timing
    rounds_observed: int = 0

    # Breakdowns
    platform_split: Dict[str, int] = field(default_factory=dict)
    action_type_counts: Dict[str, int] = field(default_factory=dict)
    segments: List[SegmentMetrics] = field(default_factory=list)
    share_of_voice: Dict[str, float] = field(default_factory=dict)
    per_round: List[Dict[str, Any]] = field(default_factory=list)
    top_voices: List[Dict[str, Any]] = field(default_factory=list)

    # Derived rates
    @property
    def reach_rate_pct(self) -> float:
        return _rate(self.reached_agents, self.audience_size)

    @property
    def engagement_rate_pct(self) -> float:
        """Share of reached agents that reacted deliberately."""
        return _rate(self.engaged_agents, self.reached_agents)

    @property
    def engagement_per_impression_pct(self) -> float:
        """Engagement actions against total impressions."""
        return _rate(self.engagement_actions, self.total_impressions)

    @property
    def virality_ratio(self) -> float:
        """Amplifications per authored post - the K-factor proxy."""
        return _ratio(self.amplifications, self.posts_authored)

    @property
    def amplification_rate_pct(self) -> float:
        """Share of reached agents that redistributed the message."""
        return _rate(self.amplifier_agents, self.reached_agents)

    @property
    def positive_actions(self) -> int:
        return sum(
            count for action, count in self.action_type_counts.items()
            if action in POSITIVE_ACTIONS
        )

    @property
    def negative_actions(self) -> int:
        return sum(
            count for action, count in self.action_type_counts.items()
            if action in NEGATIVE_ACTIONS
        )

    @property
    def net_sentiment(self) -> float:
        """-1.0 (all rejection) to 1.0 (all approval), from behavioural signals."""
        total = self.positive_actions + self.negative_actions
        return _ratio(self.positive_actions - self.negative_actions, total)

    @property
    def positive_share_pct(self) -> float:
        return _rate(self.positive_actions, self.positive_actions + self.negative_actions)

    @property
    def negative_share_pct(self) -> float:
        return _rate(self.negative_actions, self.positive_actions + self.negative_actions)

    @property
    def passive_share_pct(self) -> float:
        """Share of reached agents that saw the campaign and did nothing."""
        return _rate(self.reached_agents - self.engaged_agents, self.reached_agents)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "simulation_id": self.simulation_id,
            "reach": {
                "audience_size": self.audience_size,
                "reached_agents": self.reached_agents,
                "reach_rate_pct": self.reach_rate_pct,
                "total_impressions": self.total_impressions,
                "rounds_observed": self.rounds_observed,
            },
            "engagement": {
                "engaged_agents": self.engaged_agents,
                "engagement_rate_pct": self.engagement_rate_pct,
                "engagement_actions": self.engagement_actions,
                "engagement_per_impression_pct": self.engagement_per_impression_pct,
                "passive_share_pct": self.passive_share_pct,
                "posts_authored": self.posts_authored,
                "comments_authored": self.comments_authored,
                "likes": self.likes,
                "dislikes": self.dislikes,
                "follows": self.follows,
            },
            "virality": {
                "amplifications": self.amplifications,
                "reposts": self.reposts,
                "quotes": self.quotes,
                "amplifier_agents": self.amplifier_agents,
                "virality_ratio": self.virality_ratio,
                "amplification_rate_pct": self.amplification_rate_pct,
                "cascade_depth_rounds": self.cascade_depth_rounds,
                "peak_round": self.peak_round,
                "peak_round_actions": self.peak_round_actions,
            },
            "sentiment": {
                "positive_actions": self.positive_actions,
                "negative_actions": self.negative_actions,
                "positive_share_pct": self.positive_share_pct,
                "negative_share_pct": self.negative_share_pct,
                "net_sentiment": self.net_sentiment,
            },
            "platform_split": self.platform_split,
            "action_type_counts": self.action_type_counts,
            "share_of_voice": self.share_of_voice,
            "segments": [s.to_dict() for s in self.segments],
            "per_round": self.per_round,
            "top_voices": self.top_voices,
        }

    def to_text(self) -> str:
        """Render the KPIs as the text block the report agent reads."""
        lines: List[str] = []
        lines.append("═══ Measured campaign KPIs (counted from the action log, not estimated) ═══")
        lines.append("")

        lines.append("[Reach / exposure]")
        lines.append(f"- Audience simulated: {self.audience_size} agents")
        lines.append(
            f"- Reached (activated at least once): {self.reached_agents} "
            f"({self.reach_rate_pct}% of the audience)"
        )
        lines.append(f"- Total impressions (agent activations): {self.total_impressions}")
        lines.append(f"- Campaign window observed: {self.rounds_observed} simulated rounds")
        lines.append("")

        lines.append("[Engagement]")
        lines.append(
            f"- Engaged agents: {self.engaged_agents} "
            f"({self.engagement_rate_pct}% of reached agents)"
        )
        lines.append(
            f"- Saw it and stayed passive: {self.passive_share_pct}% of reached agents"
        )
        lines.append(f"- Engagement actions: {self.engagement_actions}")
        lines.append(
            f"- Breakdown: {self.posts_authored} posts, {self.comments_authored} comments, "
            f"{self.likes} likes, {self.dislikes} dislikes, {self.follows} follows"
        )
        lines.append("")

        lines.append("[Virality / share cascade]")
        lines.append(
            f"- Amplifications: {self.amplifications} "
            f"({self.reposts} reposts + {self.quotes} quotes)"
        )
        lines.append(
            f"- Amplifier agents: {self.amplifier_agents} "
            f"({self.amplification_rate_pct}% of reached agents)"
        )
        lines.append(
            f"- Virality ratio (amplifications per authored post): {self.virality_ratio}"
        )
        lines.append(
            f"- Cascade depth: {self.cascade_depth_rounds} consecutive rounds with "
            f"live amplification"
        )
        lines.append(
            f"- Peak round: round {self.peak_round} with {self.peak_round_actions} actions"
        )
        lines.append("")

        lines.append("[Sentiment split - behavioural signal]")
        lines.append(
            f"- Positive: {self.positive_actions} actions ({self.positive_share_pct}%) "
            f"| Negative: {self.negative_actions} actions ({self.negative_share_pct}%)"
        )
        lines.append(f"- Net sentiment score: {self.net_sentiment} (range -1.0 to 1.0)")
        lines.append("")

        if self.platform_split:
            split = ", ".join(f"{k}: {v}" for k, v in sorted(self.platform_split.items()))
            lines.append(f"[Channel split] {split}")
            lines.append("")

        if self.share_of_voice:
            lines.append("[Share of voice - percentage of authored content per segment]")
            for segment, share in sorted(
                self.share_of_voice.items(), key=lambda kv: kv[1], reverse=True
            ):
                lines.append(f"- {segment}: {share}%")
            lines.append("")

        if self.segments:
            lines.append("[Segment breakdown]")
            for seg in self.segments:
                d = seg.to_dict()
                lines.append(
                    f"- {d['segment']}: {d['audience_size']} agents, "
                    f"reach {d['reach_rate_pct']}%, engagement {d['engagement_rate_pct']}%, "
                    f"net sentiment {d['net_sentiment']}, "
                    f"{d['amplifications']} amplifications, {d['posts_authored']} posts"
                )
            lines.append("")

        if self.top_voices:
            lines.append("[Loudest voices - agents driving the conversation]")
            for voice in self.top_voices:
                lines.append(
                    f"- {voice['agent_name']} ({voice['segment']}): "
                    f"{voice['total_actions']} actions, {voice['posts_authored']} posts, "
                    f"{voice['amplifications']} amplifications"
                )
            lines.append("")

        if self.per_round:
            lines.append("[Round-by-round curve: round | actions | active agents | amplifications]")
            for row in self.per_round[:CampaignMetricsService.MAX_ROUNDS_IN_TEXT]:
                lines.append(
                    f"- R{row['round_num']}: {row['total_actions']} actions, "
                    f"{row['active_agents_count']} active, "
                    f"{row['amplifications']} amplifications"
                )
            lines.append("")

        lines.append(
            "[Note] Purchase intent and specific objections are not countable from the "
            "action log. Read them from what the agents actually wrote."
        )

        return "\n".join(lines)


class CampaignMetricsService:
    """
    Computes campaign KPIs from a finished (or running) simulation.

    Reads the same action log the frontend timeline uses, so the numbers in
    the report always match what the UI shows.
    """

    # How many rounds of the curve to include in the text rendering
    MAX_ROUNDS_IN_TEXT = 40
    # How many loud voices to surface
    TOP_VOICES_COUNT = 8

    @classmethod
    def _load_segment_map(cls, simulation_id: str) -> Dict[int, str]:
        """
        Map agent_id -> audience segment.

        The segment is the entity type the persona was generated from, which
        is what the config generator uses to describe the audience.
        """
        config_path = os.path.join(
            SimulationRunner.RUN_STATE_DIR, simulation_id, "simulation_config.json"
        )
        if not os.path.exists(config_path):
            return {}

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"failed to read simulation config; segments fall back to Unknown: {e}")
            return {}

        segment_map: Dict[int, str] = {}
        for agent in config.get("agent_configs", []):
            agent_id = agent.get("agent_id")
            if agent_id is None:
                continue
            segment_map[agent_id] = agent.get("entity_type") or "Unknown"
        return segment_map

    @classmethod
    def _configured_audience_size(cls, simulation_id: str, segment_map: Dict[int, str]) -> int:
        """Audience size as configured, so agents that never activated still count."""
        return len(segment_map)

    @classmethod
    def compute(cls, simulation_id: str) -> CampaignKPIs:
        """
        Compute the KPI set for one simulation.

        Args:
            simulation_id: Simulation ID

        Returns:
            CampaignKPIs, with zeroed counters when nothing has been logged yet
        """
        segment_map = cls._load_segment_map(simulation_id)
        timeline = SimulationRunner.get_timeline(simulation_id)
        agent_stats = SimulationRunner.get_agent_stats(simulation_id)

        kpis = CampaignKPIs(simulation_id=simulation_id)

        # ── Audience and reach ──────────────────────────────────────
        acting_agent_ids = {s["agent_id"] for s in agent_stats}
        configured_size = cls._configured_audience_size(simulation_id, segment_map)
        # An agent that never activated still belongs to the audience, so the
        # configured roster wins whenever it is available.
        kpis.audience_size = max(configured_size, len(acting_agent_ids))
        kpis.reached_agents = len(acting_agent_ids)

        # ── Action totals, from the per-agent stats ─────────────────
        engaged_agent_ids: Set[int] = set()
        amplifier_agent_ids: Set[int] = set()
        action_type_counts: Dict[str, int] = {}
        platform_split: Dict[str, int] = {}

        segments: Dict[str, SegmentMetrics] = {}
        for agent_id, segment in segment_map.items():
            if segment not in segments:
                segments[segment] = SegmentMetrics(segment=segment)
            segments[segment].audience_size += 1

        top_voices: List[Dict[str, Any]] = []

        for stats in agent_stats:
            agent_id = stats["agent_id"]
            segment = segment_map.get(agent_id, "Unknown")
            seg = segments.setdefault(segment, SegmentMetrics(segment=segment))
            if agent_id not in segment_map:
                # An agent the config never described still occupies the segment
                seg.audience_size += 1

            seg.reached_agents += 1

            agent_engagements = 0
            agent_amplifications = 0
            agent_posts = 0

            for action_type, count in stats.get("action_types", {}).items():
                action_type_counts[action_type] = action_type_counts.get(action_type, 0) + count

                if action_type in ENGAGEMENT_ACTIONS:
                    agent_engagements += count
                    seg.engagement_actions += count
                if action_type in POSITIVE_ACTIONS:
                    seg.positive_actions += count
                if action_type in NEGATIVE_ACTIONS:
                    seg.negative_actions += count
                if action_type in AMPLIFICATION_ACTIONS:
                    agent_amplifications += count
                    seg.amplifications += count
                if action_type == 'CREATE_POST':
                    agent_posts += count
                    seg.posts_authored += count

            if agent_engagements:
                engaged_agent_ids.add(agent_id)
                seg.engaged_agents += 1
            if agent_amplifications:
                amplifier_agent_ids.add(agent_id)

            platform_split["twitter"] = platform_split.get("twitter", 0) + stats.get("twitter_actions", 0)
            platform_split["reddit"] = platform_split.get("reddit", 0) + stats.get("reddit_actions", 0)

            top_voices.append({
                "agent_id": agent_id,
                "agent_name": stats.get("agent_name", ""),
                "segment": segment,
                "total_actions": stats.get("total_actions", 0),
                "engagement_actions": agent_engagements,
                "posts_authored": agent_posts,
                "amplifications": agent_amplifications,
            })

        kpis.engaged_agents = len(engaged_agent_ids)
        kpis.amplifier_agents = len(amplifier_agent_ids)
        kpis.action_type_counts = dict(sorted(action_type_counts.items()))
        kpis.platform_split = {k: v for k, v in platform_split.items() if v}

        kpis.total_actions = sum(action_type_counts.values())
        kpis.total_impressions = kpis.total_actions  # One activation = one served feed
        kpis.engagement_actions = sum(
            count for action, count in action_type_counts.items()
            if action in ENGAGEMENT_ACTIONS
        )
        kpis.posts_authored = action_type_counts.get('CREATE_POST', 0)
        kpis.comments_authored = action_type_counts.get('CREATE_COMMENT', 0)
        kpis.likes = (
            action_type_counts.get('LIKE_POST', 0)
            + action_type_counts.get('LIKE_COMMENT', 0)
        )
        kpis.dislikes = (
            action_type_counts.get('DISLIKE_POST', 0)
            + action_type_counts.get('DISLIKE_COMMENT', 0)
        )
        kpis.follows = action_type_counts.get('FOLLOW', 0)
        kpis.reposts = action_type_counts.get('REPOST', 0)
        kpis.quotes = action_type_counts.get('QUOTE_POST', 0)
        kpis.amplifications = kpis.reposts + kpis.quotes

        # ── Share of voice: who authored the conversation ───────────
        total_authored = sum(s.posts_authored for s in segments.values())
        if total_authored:
            kpis.share_of_voice = {
                s.segment: _rate(s.posts_authored, total_authored)
                for s in segments.values()
                if s.posts_authored
            }

        kpis.segments = sorted(
            segments.values(), key=lambda s: s.engagement_actions, reverse=True
        )

        top_voices.sort(key=lambda v: v["total_actions"], reverse=True)
        kpis.top_voices = top_voices[:cls.TOP_VOICES_COUNT]

        # ── Round curve and cascade ─────────────────────────────────
        kpis.rounds_observed = len(timeline)
        cls._fill_round_metrics(kpis, timeline)

        return kpis

    @classmethod
    def _fill_round_metrics(
        cls, kpis: CampaignKPIs, timeline: List[Dict[str, Any]]
    ) -> None:
        """Derive the round curve, the peak and the cascade depth."""
        per_round: List[Dict[str, Any]] = []
        current_streak = 0
        best_streak = 0

        for row in timeline:
            action_types = row.get("action_types", {})
            amplifications = sum(
                count for action, count in action_types.items()
                if action in AMPLIFICATION_ACTIONS
            )
            engagements = sum(
                count for action, count in action_types.items()
                if action in ENGAGEMENT_ACTIONS
            )

            per_round.append({
                "round_num": row.get("round_num", 0),
                "total_actions": row.get("total_actions", 0),
                "active_agents_count": row.get("active_agents_count", 0),
                "engagement_actions": engagements,
                "amplifications": amplifications,
            })

            if amplifications > 0:
                current_streak += 1
                best_streak = max(best_streak, current_streak)
            else:
                current_streak = 0

            if row.get("total_actions", 0) > kpis.peak_round_actions:
                kpis.peak_round_actions = row.get("total_actions", 0)
                kpis.peak_round = row.get("round_num", 0)

        kpis.cascade_depth_rounds = best_streak
        # A round where nobody acted logs nothing, so it is absent from the
        # timeline. The curve needs it as a zero bar, otherwise the x axis
        # silently skips rounds and reads as a shorter run than it was.
        if per_round:
            seen = {row["round_num"] for row in per_round}
            last = max(seen)
            per_round.extend(
                {
                    "round_num": n,
                    "total_actions": 0,
                    "active_agents_count": 0,
                    "engagement_actions": 0,
                    "amplifications": 0,
                }
                for n in range(min(seen), last + 1)
                if n not in seen
            )
            per_round.sort(key=lambda row: row["round_num"])
        # Full curve; only the text rendering is capped (LLM prompt size).
        kpis.per_round = per_round

    @classmethod
    def compute_as_text(cls, simulation_id: str) -> str:
        """Compute the KPIs and render them for the report agent."""
        try:
            return cls.compute(simulation_id).to_text()
        except Exception as e:
            logger.error(f"failed to compute campaign KPIs: {simulation_id}, error={e}")
            return (
                f"Campaign KPIs could not be computed for this simulation: {e}. "
                "Fall back to the qualitative evidence from the graph and the agents."
            )

    @classmethod
    def compute_as_dict(cls, simulation_id: str) -> Dict[str, Any]:
        """Compute the KPIs as a JSON-serializable dict, for the API."""
        return cls.compute(simulation_id).to_dict()

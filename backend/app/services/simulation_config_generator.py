"""
Simulation configuration generator.
Uses the LLM to derive detailed simulation parameters from the simulation
requirement, the source documents and the graph, so no parameter has to be set
by hand.

Generation is split into steps to avoid one oversized response failing:
1. Time configuration
2. Event configuration
3. Agent configuration, in batches
4. Platform configuration
"""

import json
import math
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field, asdict
from datetime import datetime

from openai import OpenAI

from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, t
from ..utils.openai_chat_compat import create_chat_completion, extract_chat_completion_text
from .zep_entity_reader import EntityNode, ZepEntityReader

logger = get_logger('mirofish.simulation_config')

# Daily-rhythm reference for China (Beijing time)
CHINA_TIMEZONE_CONFIG = {
    # Overnight band (almost nobody active)
    "dead_hours": [0, 1, 2, 3, 4, 5],
    # Morning band (waking up)
    "morning_hours": [6, 7, 8],
    # Working hours
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    # Evening peak (most active)
    "peak_hours": [19, 20, 21, 22],
    # Late-night band (activity falling off)
    "night_hours": [23],
    # Activity multipliers
    "activity_multipliers": {
        "dead": 0.05,      # Overnight, almost nobody
        "morning": 0.4,    # Morning, ramping up
        "work": 0.7,       # Working hours, moderate
        "peak": 1.5,       # Evening peak
        "night": 0.5       # Late night, falling off
    }
}


@dataclass
class AgentActivityConfig:
    """Activity configuration for a single agent."""
    agent_id: int
    entity_uuid: str
    entity_name: str
    entity_type: str
    
    # Activity level (0.0-1.0)
    activity_level: float = 0.5  # Overall activity level
    
    # Posting frequency (expected posts per hour)
    posts_per_hour: float = 1.0
    comments_per_hour: float = 2.0
    
    # Active hours (24-hour clock, 0-23)
    active_hours: List[int] = field(default_factory=lambda: list(range(8, 23)))
    
    # Response speed (delay reacting to a hot event, in simulated minutes)
    response_delay_min: int = 5
    response_delay_max: int = 60
    
    # Sentiment bias (-1.0 to 1.0, negative to positive)
    sentiment_bias: float = 0.0
    
    # Stance towards the topic
    stance: str = "neutral"  # supportive, opposing, neutral, observer
    
    # Influence weight (drives how likely other agents are to see this agent's posts)
    influence_weight: float = 1.0


@dataclass  
class TimeSimulationConfig:
    """Time simulation configuration (modelled on a China-based daily rhythm)."""
    # Total simulated duration, in simulated hours
    total_simulation_hours: int = 72  # Defaults to 72 simulated hours (3 days)
    
    # Simulated minutes per round - defaults to 60 (1 hour) to speed time up
    minutes_per_round: int = 60
    
    # Range of agents activated per hour
    agents_per_hour_min: int = 5
    agents_per_hour_max: int = 20
    
    # Peak band (19:00-22:00, the most active hours in China)
    peak_hours: List[int] = field(default_factory=lambda: [19, 20, 21, 22])
    peak_activity_multiplier: float = 1.5
    
    # Trough band (00:00-05:00, almost nobody active)
    off_peak_hours: List[int] = field(default_factory=lambda: [0, 1, 2, 3, 4, 5])
    off_peak_activity_multiplier: float = 0.05  # Overnight activity is minimal
    
    # Morning band
    morning_hours: List[int] = field(default_factory=lambda: [6, 7, 8])
    morning_activity_multiplier: float = 0.4
    
    # Working hours
    work_hours: List[int] = field(default_factory=lambda: [9, 10, 11, 12, 13, 14, 15, 16, 17, 18])
    work_activity_multiplier: float = 0.7


@dataclass
class EventConfig:
    """Event configuration."""
    # Initial events triggered when the simulation starts
    initial_posts: List[Dict[str, Any]] = field(default_factory=list)
    
    # Scheduled events, triggered at a specific time
    scheduled_events: List[Dict[str, Any]] = field(default_factory=list)
    
    # Hot topic keywords
    hot_topics: List[str] = field(default_factory=list)
    
    # Direction public opinion is steered in
    narrative_direction: str = ""


@dataclass
class PlatformConfig:
    """Platform-specific configuration."""
    platform: str  # twitter or reddit
    
    # Recommendation algorithm weights
    recency_weight: float = 0.4  # Recency
    popularity_weight: float = 0.3  # Popularity
    relevance_weight: float = 0.3  # Relevance
    
    # Virality threshold (interactions needed before a post spreads)
    viral_threshold: int = 10
    
    # Echo chamber strength (how tightly similar views cluster)
    echo_chamber_strength: float = 0.5


@dataclass
class SimulationParameters:
    """The complete set of simulation parameters."""
    # Basics
    simulation_id: str
    project_id: str
    graph_id: str
    simulation_requirement: str
    
    # Time configuration
    time_config: TimeSimulationConfig = field(default_factory=TimeSimulationConfig)
    
    # Agent configurations
    agent_configs: List[AgentActivityConfig] = field(default_factory=list)
    
    # Event configuration
    event_config: EventConfig = field(default_factory=EventConfig)
    
    # Platform configuration
    twitter_config: Optional[PlatformConfig] = None
    reddit_config: Optional[PlatformConfig] = None
    
    # LLM configuration
    llm_model: str = ""
    llm_base_url: str = ""
    
    # Generation metadata
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    generation_reasoning: str = ""  # The LLM's reasoning
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to a dict."""
        time_dict = asdict(self.time_config)
        return {
            "simulation_id": self.simulation_id,
            "project_id": self.project_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "time_config": time_dict,
            "agent_configs": [asdict(a) for a in self.agent_configs],
            "event_config": asdict(self.event_config),
            "twitter_config": asdict(self.twitter_config) if self.twitter_config else None,
            "reddit_config": asdict(self.reddit_config) if self.reddit_config else None,
            "llm_model": self.llm_model,
            "llm_base_url": self.llm_base_url,
            "generated_at": self.generated_at,
            "generation_reasoning": self.generation_reasoning,
        }
    
    def to_json(self, indent: int = 2) -> str:
        """Serialize to a JSON string."""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)


class SimulationConfigGenerator:
    """
    Simulation configuration generator.

    Uses the LLM to analyse the simulation requirement, the source documents and
    the graph entities, then derives the best simulation parameters.

    Generation is split into steps:
    1. Time and event configuration (lightweight)
    2. Agent configuration, in batches of 10-20
    3. Platform configuration
    """
    
    # Maximum context length, in characters
    MAX_CONTEXT_LENGTH = 50000
    # Agents generated per batch
    AGENTS_PER_BATCH = 15
    
    # Context truncation length per step, in characters
    TIME_CONFIG_CONTEXT_LENGTH = 10000   # Time configuration
    EVENT_CONFIG_CONTEXT_LENGTH = 8000   # Event configuration
    ENTITY_SUMMARY_LENGTH = 300          # Entity summary
    AGENT_SUMMARY_LENGTH = 300           # Entity summary inside the agent config step
    ENTITIES_PER_TYPE_DISPLAY = 20       # Entities shown per type
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def generate_config(
        self,
        simulation_id: str,
        project_id: str,
        graph_id: str,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode],
        enable_twitter: bool = True,
        enable_reddit: bool = True,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> SimulationParameters:
        """
        Generate the complete simulation configuration, one step at a time.

        Args:
            simulation_id: Simulation ID
            project_id: Project ID
            graph_id: Graph ID
            simulation_requirement: Description of the simulation requirement
            document_text: Source document text
            entities: The filtered entities
            enable_twitter: Enable Twitter
            enable_reddit: Enable Reddit
            progress_callback: Progress callback (current_step, total_steps, message)

        Returns:
            SimulationParameters: the full parameter set
        """
        logger.info(f"开始智能生成模拟配置: simulation_id={simulation_id}, 实体数={len(entities)}")
        
        # Work out the total number of steps
        num_batches = math.ceil(len(entities) / self.AGENTS_PER_BATCH)
        total_steps = 3 + num_batches  # time config + event config + N agent batches + platform config
        current_step = 0
        
        def report_progress(step: int, message: str):
            nonlocal current_step
            current_step = step
            if progress_callback:
                progress_callback(step, total_steps, message)
            logger.info(f"[{step}/{total_steps}] {message}")
        
        # 1. Build the base context
        context = self._build_context(
            simulation_requirement=simulation_requirement,
            document_text=document_text,
            entities=entities
        )
        
        reasoning_parts = []
        
        # ========== Step 1: time configuration ==========
        report_progress(1, t('progress.generatingTimeConfig'))
        num_entities = len(entities)
        time_config_result = self._generate_time_config(context, num_entities)
        time_config = self._parse_time_config(time_config_result, num_entities)
        reasoning_parts.append(f"{t('progress.timeConfigLabel')}: {time_config_result.get('reasoning', t('common.success'))}")
        
        # ========== Step 2: event configuration ==========
        report_progress(2, t('progress.generatingEventConfig'))
        event_config_result = self._generate_event_config(context, simulation_requirement, entities)
        event_config = self._parse_event_config(event_config_result)
        reasoning_parts.append(f"{t('progress.eventConfigLabel')}: {event_config_result.get('reasoning', t('common.success'))}")
        
        # ========== Steps 3-N: agent configuration, in batches ==========
        all_agent_configs = []
        for batch_idx in range(num_batches):
            start_idx = batch_idx * self.AGENTS_PER_BATCH
            end_idx = min(start_idx + self.AGENTS_PER_BATCH, len(entities))
            batch_entities = entities[start_idx:end_idx]
            
            report_progress(
                3 + batch_idx,
                t('progress.generatingAgentConfig', start=start_idx + 1, end=end_idx, total=len(entities))
            )
            
            batch_configs = self._generate_agent_configs_batch(
                context=context,
                entities=batch_entities,
                start_idx=start_idx,
                simulation_requirement=simulation_requirement
            )
            all_agent_configs.extend(batch_configs)
        
        reasoning_parts.append(t('progress.agentConfigResult', count=len(all_agent_configs)))
        
        # ========== Assign a publisher agent to each initial post ==========
        logger.info("为初始帖子分配合适的发布者 Agent...")
        event_config = self._assign_initial_post_agents(event_config, all_agent_configs)
        assigned_count = len([p for p in event_config.initial_posts if p.get("poster_agent_id") is not None])
        reasoning_parts.append(t('progress.postAssignResult', count=assigned_count))
        
        # ========== Final step: platform configuration ==========
        report_progress(total_steps, t('progress.generatingPlatformConfig'))
        twitter_config = None
        reddit_config = None
        
        if enable_twitter:
            twitter_config = PlatformConfig(
                platform="twitter",
                recency_weight=0.4,
                popularity_weight=0.3,
                relevance_weight=0.3,
                viral_threshold=10,
                echo_chamber_strength=0.5
            )
        
        if enable_reddit:
            reddit_config = PlatformConfig(
                platform="reddit",
                recency_weight=0.3,
                popularity_weight=0.4,
                relevance_weight=0.3,
                viral_threshold=15,
                echo_chamber_strength=0.6
            )
        
        # Assemble the final parameters
        params = SimulationParameters(
            simulation_id=simulation_id,
            project_id=project_id,
            graph_id=graph_id,
            simulation_requirement=simulation_requirement,
            time_config=time_config,
            agent_configs=all_agent_configs,
            event_config=event_config,
            twitter_config=twitter_config,
            reddit_config=reddit_config,
            llm_model=self.model_name,
            llm_base_url=self.base_url,
            generation_reasoning=" | ".join(reasoning_parts)
        )
        
        logger.info(f"模拟配置生成完成: {len(params.agent_configs)} 个Agent配置")
        
        return params
    
    def _build_context(
        self,
        simulation_requirement: str,
        document_text: str,
        entities: List[EntityNode]
    ) -> str:
        """Build the LLM context, truncated to the maximum length."""
        
        # Entity summary
        entity_summary = self._summarize_entities(entities)
        
        # Assemble the context
        context_parts = [
            f"## Campaign brief and target audience\n{simulation_requirement}",
            f"\n## Audience segments and entities ({len(entities)})\n{entity_summary}",
        ]
        
        current_length = sum(len(p) for p in context_parts)
        remaining_length = self.MAX_CONTEXT_LENGTH - current_length - 500  # Leave a 500-character margin
        
        if remaining_length > 0 and document_text:
            doc_text = document_text[:remaining_length]
            if len(document_text) > remaining_length:
                doc_text += "\n...(document truncated)"
            context_parts.append(
                f"\n## Campaign material (creative, messaging, product and market background)\n{doc_text}"
            )
        
        return "\n".join(context_parts)
    
    def _summarize_entities(self, entities: List[EntityNode]) -> str:
        """Summarize the entities."""
        lines = []
        
        # Group by type
        by_type: Dict[str, List[EntityNode]] = {}
        for e in entities:
            t = e.get_entity_type() or "Unknown"
            if t not in by_type:
                by_type[t] = []
            by_type[t].append(e)
        
        for entity_type, type_entities in by_type.items():
            lines.append(f"\n### {entity_type} ({len(type_entities)})")
            # Use the configured display count and summary length
            display_count = self.ENTITIES_PER_TYPE_DISPLAY
            summary_len = self.ENTITY_SUMMARY_LENGTH
            for e in type_entities[:display_count]:
                summary_preview = (e.summary[:summary_len] + "...") if len(e.summary) > summary_len else e.summary
                lines.append(f"- {e.name}: {summary_preview}")
            if len(type_entities) > display_count:
                lines.append(f"  ... and {len(type_entities) - display_count} more")
        
        return "\n".join(lines)
    
    def _call_llm_with_retry(self, prompt: str, system_prompt: str) -> Dict[str, Any]:
        """Call the LLM with retries, repairing malformed JSON along the way."""
        import re
        
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                response = create_chat_completion(
                    self.client,
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt}
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.7 - (attempt * 0.1),  # Lower the temperature on each retry
                    # No max_tokens: let the model use its own limit
                )
                
                content = extract_chat_completion_text(response)
                finish_reason = response.choices[0].finish_reason
                
                # Detect a truncated response
                if finish_reason == 'length':
                    logger.warning(f"LLM输出被截断 (attempt {attempt+1})")
                    content = self._fix_truncated_json(content)
                
                # Try to parse the JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.warning(f"JSON解析失败 (attempt {attempt+1}): {str(e)[:80]}")
                    
                    # Try to repair the JSON
                    fixed = self._try_fix_config_json(content)
                    if fixed:
                        return fixed
                    
                    last_error = e
                    
            except Exception as e:
                logger.warning(f"LLM调用失败 (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                import time
                time.sleep(2 * (attempt + 1))
        
        raise last_error or Exception("LLM调用失败")
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repair truncated JSON."""
        content = content.strip()
        
        # Count the unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check for an unterminated string
        if content and content[-1] not in '",}]':
            content += '"'
        
        # Close the brackets
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_config_json(self, content: str) -> Optional[Dict[str, Any]]:
        """Try to repair the config JSON."""
        import re
        
        # Repair truncation first
        content = self._fix_truncated_json(content)
        
        # Extract the JSON body
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # Strip newlines out of string literals
            def fix_string(match):
                s = match.group(0)
                s = s.replace('\n', ' ').replace('\r', ' ')
                s = re.sub(r'\s+', ' ', s)
                return s
            
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string, json_str)
            
            try:
                return json.loads(json_str)
            except:
                # Try again with every control character removed
                json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                json_str = re.sub(r'\s+', ' ', json_str)
                try:
                    return json.loads(json_str)
                except:
                    pass
        
        return None
    
    def _generate_time_config(self, context: str, num_entities: int) -> Dict[str, Any]:
        """Generate the time configuration."""
        # Use the configured context truncation length
        context_truncated = context[:self.TIME_CONFIG_CONTEXT_LENGTH]
        
        # Cap at a share of the total agent count
        max_agents_allowed = max(1, int(num_entities * 0.9))
        
        prompt = f"""Generate the media-flighting schedule for the campaign below: how long the
campaign runs in the simulated market, and how much of the target audience is
exposed to it per hour.

{context_truncated}

## Task
Produce the time configuration as JSON.

### Campaign window (total_simulation_hours)
Match it to the campaign type described in the brief:
- Launch burst / flash promotion / event tie-in: 24-48 hours
- Standard social flight: 72 hours
- Awareness or always-on campaign: 96-168 hours
A campaign window that is too short truncates the share cascade and understates
virality; too long and the tail is all noise.

### Exposure rate (agents_per_hour_min / agents_per_hour_max)
This is the media delivery rate - how many of the {num_entities} audience agents
see the campaign each simulated hour.
- A heavy paid push saturates the audience fast: set max near the upper bound
- An organic or low-budget campaign trickles out: keep max low so reach builds
  slowly and depends on sharing rather than on spend
- The two values bracket the hourly rate; the daily rhythm below modulates it

### Daily rhythm (guidance only - adapt to the target audience in the brief):
- Infer the target audience's timezone and daily rhythm from the brief. The
  examples below are for UTC+8.
- 00:00-05:00 is nearly dead (activity multiplier 0.05)
- 06:00-08:00 ramps up (activity multiplier 0.4)
- 09:00-18:00 working hours, moderate activity (activity multiplier 0.7)
- 19:00-22:00 is the evening peak (activity multiplier 1.5)
- Activity falls off after 23:00 (activity multiplier 0.5)
- **Important**: adjust the bands to who the campaign actually targets.
  - A student or Gen-Z audience peaks at 21:00-23:00
  - Working professionals peak at commute times and in the evening
  - A B2B audience is active only during working hours, and barely at weekends
  - Parents of young children have a short late-evening window

### Return JSON (no markdown)

Example:
{{
    "total_simulation_hours": 72,
    "minutes_per_round": 60,
    "agents_per_hour_min": 5,
    "agents_per_hour_max": 50,
    "peak_hours": [19, 20, 21, 22],
    "off_peak_hours": [0, 1, 2, 3, 4, 5],
    "morning_hours": [6, 7, 8],
    "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
    "reasoning": "Why this flighting fits the campaign"
}}

Field reference:
- total_simulation_hours (int): campaign window, 24-168 hours
- minutes_per_round (int): minutes per round, 30-120; 60 is recommended
- agents_per_hour_min (int): minimum audience agents exposed per hour (range: 1-{max_agents_allowed})
- agents_per_hour_max (int): maximum audience agents exposed per hour (range: 1-{max_agents_allowed})
- peak_hours (int array): peak band, matched to the target audience
- off_peak_hours (int array): trough band, usually late night and early morning
- morning_hours (int array): morning band
- work_hours (int array): working-hours band
- reasoning (string): a short note on why this flighting was chosen"""

        system_prompt = "You are a media planner configuring a campaign test in a simulated market. Return pure JSON. The flighting must match the campaign window and the daily rhythm of the target audience in the brief."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}"

        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"时间配置LLM生成失败: {e}, 使用默认配置")
            return self._get_default_time_config(num_entities)
    
    def _get_default_time_config(self, num_entities: int) -> Dict[str, Any]:
        """Return the default time configuration (China-based daily rhythm)."""
        return {
            "total_simulation_hours": 72,
            "minutes_per_round": 60,  # One simulated hour per round, to speed time up
            "agents_per_hour_min": max(1, num_entities // 15),
            "agents_per_hour_max": max(5, num_entities // 5),
            "peak_hours": [19, 20, 21, 22],
            "off_peak_hours": [0, 1, 2, 3, 4, 5],
            "morning_hours": [6, 7, 8],
            "work_hours": [9, 10, 11, 12, 13, 14, 15, 16, 17, 18],
            "reasoning": "Using the default 72-hour campaign window on a China-based daily rhythm (1 hour per round)"
        }
    
    def _parse_time_config(self, result: Dict[str, Any], num_entities: int) -> TimeSimulationConfig:
        """Parse the time configuration, clamping agents_per_hour to the agent count."""
        # Read the raw values
        agents_per_hour_min = result.get("agents_per_hour_min", max(1, num_entities // 15))
        agents_per_hour_max = result.get("agents_per_hour_max", max(5, num_entities // 5))
        
        # Validate and clamp to the total agent count
        if agents_per_hour_min > num_entities:
            logger.warning(f"agents_per_hour_min ({agents_per_hour_min}) 超过总Agent数 ({num_entities})，已修正")
            agents_per_hour_min = max(1, num_entities // 10)
        
        if agents_per_hour_max > num_entities:
            logger.warning(f"agents_per_hour_max ({agents_per_hour_max}) 超过总Agent数 ({num_entities})，已修正")
            agents_per_hour_max = max(agents_per_hour_min + 1, num_entities // 2)
        
        # Make sure min < max
        if agents_per_hour_min >= agents_per_hour_max:
            agents_per_hour_min = max(1, agents_per_hour_max // 2)
            logger.warning(f"agents_per_hour_min >= max，已修正为 {agents_per_hour_min}")
        
        return TimeSimulationConfig(
            total_simulation_hours=result.get("total_simulation_hours", 72),
            minutes_per_round=result.get("minutes_per_round", 60),  # One simulated hour per round by default
            agents_per_hour_min=agents_per_hour_min,
            agents_per_hour_max=agents_per_hour_max,
            peak_hours=result.get("peak_hours", [19, 20, 21, 22]),
            off_peak_hours=result.get("off_peak_hours", [0, 1, 2, 3, 4, 5]),
            off_peak_activity_multiplier=0.05,  # Overnight, almost nobody
            morning_hours=result.get("morning_hours", [6, 7, 8]),
            morning_activity_multiplier=0.4,
            work_hours=result.get("work_hours", list(range(9, 19))),
            work_activity_multiplier=0.7,
            peak_activity_multiplier=1.5
        )
    
    def _generate_event_config(
        self, 
        context: str, 
        simulation_requirement: str,
        entities: List[EntityNode]
    ) -> Dict[str, Any]:
        """Generate the event configuration."""
        
        # Collect the available entity types for the LLM to choose from
        entity_types_available = list(set(
            e.get_entity_type() or "Unknown" for e in entities
        ))
        
        # List a few representative entity names per type
        type_examples = {}
        for e in entities:
            etype = e.get_entity_type() or "Unknown"
            if etype not in type_examples:
                type_examples[etype] = []
            if len(type_examples[etype]) < 3:
                type_examples[etype].append(e.name)
        
        type_info = "\n".join([
            f"- {t}: {', '.join(examples)}" 
            for t, examples in type_examples.items()
        ])
        
        # Use the configured context truncation length
        context_truncated = context[:self.EVENT_CONFIG_CONTEXT_LENGTH]
        
        prompt = f"""Launch the campaign below into the simulated market. Your job is to write the
seed content the audience will actually encounter in their feed.

Campaign brief and target audience: {simulation_requirement}

{context_truncated}

## Available account types and examples
{type_info}

## Task
Produce the campaign launch configuration as JSON:
- hot_topics: the campaign's message pillars, product name, tagline and the
  hashtags or phrases the audience would use when talking about it
- narrative_direction: how the conversation around this campaign is expected to
  develop across the flight
- initial_posts: the seed content, described below

### The seed posts are the campaign itself
These are what the audience reacts to, so they decide the whole test. Write
them as real feed content, not as a description of the campaign.

Compose the seed set from these roles, in this priority:
1. **The campaign creative itself** (1-3 posts) - the brand's own launch post.
   Use the actual headline, tagline, offer, price point and call to action from
   the campaign material above. Write it in the brand's voice as it would ship.
   If the material contains several creative executions, seed each one so the
   simulation reveals which lands best.
2. **Paid and owned amplification** (0-2 posts) - the same message carried by a
   media partner, retail channel or brand ambassador account, phrased the way
   that account actually writes.
3. **Organic first contact** (1-2 posts) - an ordinary audience member's
   unfiltered first reaction to seeing the ad. Do NOT make this uniformly
   positive; first reactions to advertising are frequently indifferent or
   sceptical, and a seed set that only cheers produces a worthless test.

Rules for the seed content:
- Quote the campaign's real message, claims, offer and price from the material
  above. Do not invent a different product or a different promise.
- Keep each post the length that platform's users actually write.
- Do NOT write the audience's verdict into the seed posts. The point of the
  simulation is to discover the verdict, not to assert it.
- Do NOT seed engagement counts, sentiment claims or predicted outcomes.

**Important**: poster_type must be chosen from the "available account types"
above so each seed post can be assigned to a suitable agent. The brand's own
creative should come from the advertiser, company or organisation type; media
amplification from MediaOutlet; organic reactions from an individual consumer
type.

Return JSON (no markdown):
{{
    "hot_topics": ["message pillar / tagline / hashtag", ...],
    "narrative_direction": "<how the conversation around the campaign is expected to develop>",
    "initial_posts": [
        {{"content": "the actual post text as it would appear in the feed", "poster_type": "account type (must come from the available types)"}},
        ...
    ],
    "reasoning": "<short explanation of the seed strategy>"
}}"""

        system_prompt = "You are a campaign strategist writing the launch content for a marketing campaign test. Return pure JSON. poster_type must match one of the available account types exactly."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}\nIMPORTANT: The 'poster_type' field value MUST be in English PascalCase exactly matching the available entity types. Only 'content', 'narrative_direction', 'hot_topics' and 'reasoning' fields should use the specified language."

        try:
            return self._call_llm_with_retry(prompt, system_prompt)
        except Exception as e:
            logger.warning(f"事件配置LLM生成失败: {e}, 使用默认配置")
            return {
                "hot_topics": [],
                "narrative_direction": "",
                "initial_posts": [],
                "reasoning": "Using the default configuration"
            }
    
    def _parse_event_config(self, result: Dict[str, Any]) -> EventConfig:
        """Parse the event configuration result."""
        return EventConfig(
            initial_posts=result.get("initial_posts", []),
            scheduled_events=[],
            hot_topics=result.get("hot_topics", []),
            narrative_direction=result.get("narrative_direction", "")
        )
    
    def _assign_initial_post_agents(
        self,
        event_config: EventConfig,
        agent_configs: List[AgentActivityConfig]
    ) -> EventConfig:
        """
        Assign a suitable publisher agent to each initial post.

        Each post's poster_type is matched to the most appropriate agent_id.
        """
        if not event_config.initial_posts:
            return event_config
        
        # Index the agents by entity type
        agents_by_type: Dict[str, List[AgentActivityConfig]] = {}
        for agent in agent_configs:
            etype = agent.entity_type.lower()
            if etype not in agents_by_type:
                agents_by_type[etype] = []
            agents_by_type[etype].append(agent)
        
        # Type alias table (the LLM may emit a different spelling)
        type_aliases = {
            "official": ["official", "university", "governmentagency", "government"],
            "university": ["university", "official"],
            "mediaoutlet": ["mediaoutlet", "media", "publisher", "influencer"],
            "student": ["student", "person", "consumer"],
            "professor": ["professor", "expert", "teacher", "analyst"],
            "alumni": ["alumni", "person", "consumer"],
            "organization": ["organization", "ngo", "company", "group", "brand"],
            "person": ["person", "student", "alumni", "consumer", "customer"],
            # Marketing-side account types
            "brand": ["brand", "advertiser", "company", "organization"],
            "advertiser": ["advertiser", "brand", "company", "organization"],
            "company": ["company", "brand", "organization"],
            "retailer": ["retailer", "company", "brand", "organization"],
            "influencer": ["influencer", "publicfigure", "mediaoutlet", "person"],
            "publicfigure": ["publicfigure", "influencer", "expert"],
            "consumer": ["consumer", "customer", "person", "student", "alumni"],
            "customer": ["customer", "consumer", "person"],
            "competitor": ["competitor", "company", "brand", "organization"],
        }
        
        # Track the agent index used per type so the same agent is not reused
        used_indices: Dict[str, int] = {}
        
        updated_posts = []
        for post in event_config.initial_posts:
            poster_type = post.get("poster_type", "").lower()
            content = post.get("content", "")
            
            # Look for a matching agent
            matched_agent_id = None
            
            # 1. Direct match
            if poster_type in agents_by_type:
                agents = agents_by_type[poster_type]
                idx = used_indices.get(poster_type, 0) % len(agents)
                matched_agent_id = agents[idx].agent_id
                used_indices[poster_type] = idx + 1
            else:
                # 2. Match through the aliases
                for alias_key, aliases in type_aliases.items():
                    if poster_type in aliases or alias_key == poster_type:
                        for alias in aliases:
                            if alias in agents_by_type:
                                agents = agents_by_type[alias]
                                idx = used_indices.get(alias, 0) % len(agents)
                                matched_agent_id = agents[idx].agent_id
                                used_indices[alias] = idx + 1
                                break
                    if matched_agent_id is not None:
                        break
            
            # 3. Still nothing: fall back to the most influential agent
            if matched_agent_id is None:
                logger.warning(f"未找到类型 '{poster_type}' 的匹配 Agent，使用影响力最高的 Agent")
                if agent_configs:
                    # Sort by influence and take the highest
                    sorted_agents = sorted(agent_configs, key=lambda a: a.influence_weight, reverse=True)
                    matched_agent_id = sorted_agents[0].agent_id
                else:
                    matched_agent_id = 0
            
            updated_posts.append({
                "content": content,
                "poster_type": post.get("poster_type", "Unknown"),
                "poster_agent_id": matched_agent_id
            })
            
            logger.info(f"初始帖子分配: poster_type='{poster_type}' -> agent_id={matched_agent_id}")
        
        event_config.initial_posts = updated_posts
        return event_config
    
    def _generate_agent_configs_batch(
        self,
        context: str,
        entities: List[EntityNode],
        start_idx: int,
        simulation_requirement: str
    ) -> List[AgentActivityConfig]:
        """Generate agent configurations for one batch."""
        
        # Build the entity payload (using the configured summary length)
        entity_list = []
        summary_len = self.AGENT_SUMMARY_LENGTH
        for i, e in enumerate(entities):
            entity_list.append({
                "agent_id": start_idx + i,
                "entity_name": e.name,
                "entity_type": e.get_entity_type() or "Unknown",
                "summary": e.summary[:summary_len] if e.summary else ""
            })
        
        prompt = f"""Generate the media behaviour configuration for each audience member below: how
often they are online, how fast they react to campaign content, and how
predisposed they are towards the advertised brand.

Campaign brief and target audience: {simulation_requirement}

## Audience members
```json
{json.dumps(entity_list, ensure_ascii=False, indent=2)}
```

## Task
Produce an activity configuration per audience member. Note:
- **Timing must match the target audience's daily rhythm**: the values below are
  a UTC+8 reference; adjust them to the campaign's actual target market
- **Brands, advertisers and official bodies** (Company/Brand/University/
  GovernmentAgency): low activity (0.1-0.3), active during working hours (9-17),
  slow to respond (60-240 min), high influence (2.5-3.0)
- **Media, publishers and influencers** (MediaOutlet/Influencer): medium
  activity (0.4-0.6), active all day (8-23), fast to respond (5-30 min), high
  influence (2.0-2.5)
- **Individual consumers** (Student/Person/Alumni/Consumer): high activity
  (0.6-0.9), mostly active in the evening (18-23), fast to respond (1-15 min),
  low influence (0.8-1.2)
- **Experts, reviewers and category analysts**: medium activity (0.4-0.6),
  medium-high influence (1.5-2.0) - their verdict carries disproportionate
  weight in a purchase decision

**sentiment_bias and stance are the audience's PRIOR disposition to the brand,
not their reaction to this campaign.** Derive them from the entity's existing
relationship with the brand and category:
- A loyal existing customer starts supportive with a positive bias
- A competitor's customer or a burned former buyer starts opposing with a
  negative bias
- Most of the audience has no strong prior: neutral, bias near 0
- Trade press and review communities are usually observer with bias near 0

Do NOT set the whole audience supportive. A test where everyone already likes
the brand cannot tell the marketing team anything.

Return JSON (no markdown):
{{
    "agent_configs": [
        {{
            "agent_id": <must match the input>,
            "activity_level": <0.0-1.0>,
            "posts_per_hour": <posting frequency>,
            "comments_per_hour": <commenting frequency>,
            "active_hours": [<active hours, matching the audience's daily rhythm>],
            "response_delay_min": <minimum response delay in minutes>,
            "response_delay_max": <maximum response delay in minutes>,
            "sentiment_bias": <-1.0 to 1.0, prior disposition to the brand>,
            "stance": "<supportive/opposing/neutral/observer>",
            "influence_weight": <influence weight>
        }},
        ...
    ]
}}"""

        system_prompt = "You are a consumer behaviour analyst configuring an audience for a campaign test. Return pure JSON. The configuration must match the daily rhythm and brand priors of the target audience in the campaign brief."
        system_prompt = f"{system_prompt}\n\n{get_language_instruction()}\nIMPORTANT: The 'stance' field value MUST be one of the English strings: 'supportive', 'opposing', 'neutral', 'observer'. All JSON field names and numeric values must remain unchanged. Only natural language text fields should use the specified language."

        try:
            result = self._call_llm_with_retry(prompt, system_prompt)
            llm_configs = {cfg["agent_id"]: cfg for cfg in result.get("agent_configs", [])}
        except Exception as e:
            logger.warning(f"Agent配置批次LLM生成失败: {e}, 使用规则生成")
            llm_configs = {}
        
        # Build the AgentActivityConfig objects
        configs = []
        for i, entity in enumerate(entities):
            agent_id = start_idx + i
            cfg = llm_configs.get(agent_id, {})
            
            # The LLM produced nothing for this agent: fall back to the rules
            if not cfg:
                cfg = self._generate_agent_config_by_rule(entity)
            
            config = AgentActivityConfig(
                agent_id=agent_id,
                entity_uuid=entity.uuid,
                entity_name=entity.name,
                entity_type=entity.get_entity_type() or "Unknown",
                activity_level=cfg.get("activity_level", 0.5),
                posts_per_hour=cfg.get("posts_per_hour", 0.5),
                comments_per_hour=cfg.get("comments_per_hour", 1.0),
                active_hours=cfg.get("active_hours", list(range(9, 23))),
                response_delay_min=cfg.get("response_delay_min", 5),
                response_delay_max=cfg.get("response_delay_max", 60),
                sentiment_bias=cfg.get("sentiment_bias", 0.0),
                stance=cfg.get("stance", "neutral"),
                influence_weight=cfg.get("influence_weight", 1.0)
            )
            configs.append(config)
        
        return configs
    
    def _generate_agent_config_by_rule(self, entity: EntityNode) -> Dict[str, Any]:
        """Rule-based fallback for one agent configuration (China-based daily rhythm)."""
        entity_type = (entity.get_entity_type() or "Unknown").lower()
        
        if entity_type in ["university", "governmentagency", "ngo"]:
            # Official bodies: active during working hours, low frequency, high influence
            return {
                "activity_level": 0.2,
                "posts_per_hour": 0.1,
                "comments_per_hour": 0.05,
                "active_hours": list(range(9, 18)),  # 9:00-17:59
                "response_delay_min": 60,
                "response_delay_max": 240,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 3.0
            }
        elif entity_type in ["mediaoutlet"]:
            # Media: active all day, medium frequency, high influence
            return {
                "activity_level": 0.5,
                "posts_per_hour": 0.8,
                "comments_per_hour": 0.3,
                "active_hours": list(range(7, 24)),  # 7:00-23:59
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "observer",
                "influence_weight": 2.5
            }
        elif entity_type in ["professor", "expert", "official"]:
            # Experts and professors: active during work and evening, medium frequency
            return {
                "activity_level": 0.4,
                "posts_per_hour": 0.3,
                "comments_per_hour": 0.5,
                "active_hours": list(range(8, 22)),  # 8:00-21:59
                "response_delay_min": 15,
                "response_delay_max": 90,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 2.0
            }
        elif entity_type in ["student"]:
            # Students: mostly evening, high frequency
            return {
                "activity_level": 0.8,
                "posts_per_hour": 0.6,
                "comments_per_hour": 1.5,
                "active_hours": [8, 9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Late morning + evening
                "response_delay_min": 1,
                "response_delay_max": 15,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 0.8
            }
        elif entity_type in ["alumni"]:
            # Alumni: mostly evening
            return {
                "activity_level": 0.6,
                "posts_per_hour": 0.4,
                "comments_per_hour": 0.8,
                "active_hours": [12, 13, 19, 20, 21, 22, 23],  # Lunch break + evening
                "response_delay_min": 5,
                "response_delay_max": 30,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
        else:
            # Ordinary people: evening peak
            return {
                "activity_level": 0.7,
                "posts_per_hour": 0.5,
                "comments_per_hour": 1.2,
                "active_hours": [9, 10, 11, 12, 13, 18, 19, 20, 21, 22, 23],  # Daytime + evening
                "response_delay_min": 2,
                "response_delay_max": 20,
                "sentiment_bias": 0.0,
                "stance": "neutral",
                "influence_weight": 1.0
            }
    


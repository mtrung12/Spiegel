"""
OASIS agent profile generator.
Converts entities from the knowledge graph into the agent profile format the OASIS
simulation platform expects.

Design notes:
1. Graph retrieval is used to enrich each node a second time
2. The prompts are tuned to produce very detailed personas
3. Individual entities and abstract group entities are handled differently
"""

import json
import random
import time
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field, replace
from datetime import datetime

from openai import OpenAI
from ..config import Config
from ..utils.logger import get_logger
from ..utils.locale import get_language_instruction, get_locale, set_locale, t
from ..utils.openai_chat_compat import create_chat_completion, extract_chat_completion_text
from ..utils.pipeline_logger import pipeline_log
from ..utils.graphiti_graph import search_graph
from ..utils.ontology import is_cloneable_kind, is_person_kind
from .agent_population import (
    INDIVIDUAL_ENTITY_TYPES as _INDIVIDUAL_ENTITY_TYPES,
    MAX_AGENTS,
    AgentSlot,
    entity_kind,
    plan_population,
)
from .graph_entity_reader import EntityNode

logger = get_logger('spiegel.oasis_profile')


def _usage_dict(response: Any) -> Dict[str, Any]:
    """Pull the token counts off a completion, when the provider reports them."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    return {
        key: getattr(usage, key, None)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if getattr(usage, key, None) is not None
    }


def _coerce_to_str(value: Any) -> str:
    """Coerce a value to a plain string.

    Handles dict, list, and other non-string types that may be returned
    by LLM JSON parsing.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ('text', 'value', 'description', 'content', 'summary', 'name'):
            if key in value:
                candidate = _coerce_to_str(value[key])
                if candidate:
                    return candidate
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (list, tuple)):
        str_items = [_coerce_to_str(item) for item in value]
        str_items = [item for item in str_items if item]
        return ', '.join(str_items)
    return str(value)


def _coerce_to_str_list(value: Any) -> List[str]:
    """Coerce a value to a list of strings.

    Handles nested structures that may be returned by LLM JSON parsing.
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        result: List[str] = []
        for item in value:
            if isinstance(item, (list, tuple)):
                result.extend(_coerce_to_str_list(item))
            else:
                text = _coerce_to_str(item)
                if text:
                    result.append(text)
        return result
    text = _coerce_to_str(value)
    return [text] if text else []


@dataclass
class OasisAgentProfile:
    """OASIS agent profile data structure."""
    # Common fields
    user_id: int
    user_name: str
    name: str
    bio: str
    persona: str

    # Optional fields - Reddit style
    karma: int = 1000
    
    # Optional fields - Twitter style
    friend_count: int = 100
    follower_count: int = 150
    statuses_count: int = 500
    
    # Extra persona information
    age: Optional[int] = None
    gender: Optional[str] = None
    mbti: Optional[str] = None
    country: Optional[str] = None
    profession: Optional[str] = None
    interested_topics: List[str] = field(default_factory=list)
    
    # Source entity information
    source_entity_uuid: Optional[str] = None
    source_entity_type: Optional[str] = None
    
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d"))
    
    def __post_init__(self):
        """Normalize structured LLM fields once at the profile boundary."""
        self.bio = _coerce_to_str(self.bio) or self.name
        self.persona = _coerce_to_str(self.persona) or (
            f"{self.name} is a participant in social discussions."
        )
        self.country = _coerce_to_str(self.country) or None
        self.profession = _coerce_to_str(self.profession) or None
        self.gender = _coerce_to_str(self.gender) or None
        self.mbti = _coerce_to_str(self.mbti) or None
        self.interested_topics = _coerce_to_str_list(self.interested_topics)

    def to_reddit_format(self) -> Dict[str, Any]:
        """Convert to the Reddit platform format."""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # The OASIS library requires the field to be named username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "created_at": self.created_at,
        }
        
        # Attach the extra persona information, if present
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_twitter_format(self) -> Dict[str, Any]:
        """Convert to the Twitter platform format."""
        profile = {
            "user_id": self.user_id,
            "username": self.user_name,  # The OASIS library requires the field to be named username (no underscore)
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "created_at": self.created_at,
        }
        
        # Attach the extra persona information
        if self.age:
            profile["age"] = self.age
        if self.gender:
            profile["gender"] = self.gender
        if self.mbti:
            profile["mbti"] = self.mbti
        if self.country:
            profile["country"] = self.country
        if self.profession:
            profile["profession"] = self.profession
        if self.interested_topics:
            profile["interested_topics"] = self.interested_topics
        
        return profile
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to the full dict format."""
        return {
            "user_id": self.user_id,
            "user_name": self.user_name,
            "name": self.name,
            "bio": self.bio,
            "persona": self.persona,
            "karma": self.karma,
            "friend_count": self.friend_count,
            "follower_count": self.follower_count,
            "statuses_count": self.statuses_count,
            "age": self.age,
            "gender": self.gender,
            "mbti": self.mbti,
            "country": self.country,
            "profession": self.profession,
            "interested_topics": self.interested_topics,
            "source_entity_uuid": self.source_entity_uuid,
            "source_entity_type": self.source_entity_type,
            "created_at": self.created_at,
        }


class OasisProfileGenerator:
    """
    OASIS profile generator.

    Converts entities from the knowledge graph into the agent profiles an OASIS
    simulation needs.

    Design notes:
    1. Graph retrieval supplies richer context
    2. Personas are very detailed (basics, career history, personality, social
       media behaviour, ...)
    3. Individual entities and abstract group entities are handled differently
    """
    
    # MBTI types
    MBTI_TYPES = [
        "INTJ", "INTP", "ENTJ", "ENTP",
        "INFJ", "INFP", "ENFJ", "ENFP",
        "ISTJ", "ISFJ", "ESTJ", "ESFJ",
        "ISTP", "ISFP", "ESTP", "ESFP"
    ]
    
    # Common countries
    COUNTRIES = [
        "China", "US", "UK", "Japan", "Germany", "France", 
        "Canada", "Australia", "Brazil", "India", "South Korea"
    ]
    
    # Individual entity types (get a concrete buyer persona). Defined in
    # agent_population so the population planner and the persona prompts agree
    # on what counts as a natural person.
    INDIVIDUAL_ENTITY_TYPES = _INDIVIDUAL_ENTITY_TYPES

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model_name: Optional[str] = None,
        graph_id: Optional[str] = None,
        corpus_distribution: Optional[Dict[str, Any]] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model_name = model_name or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY is not configured")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
        
        # Audience priors harvested from public discussion. Optional: without
        # them every persona is generated from the graph entity alone, which is
        # the behaviour that predates the corpus.
        self.corpus_distribution = corpus_distribution

        # {entity_type: kind} over ENTITY_KINDS, set by the caller from the
        # ontology and derive_population_hints. Empty means classify by name.
        self.entity_kinds: Dict[str, str] = {}

        # Graph to retrieve richer entity context from. Absent means profiles
        # are written from the entity's own attributes alone.
        self.graph_id = graph_id


    def generate_profile_from_entity(
        self,
        entity: EntityNode,
        user_id: int,
        use_llm: bool = True,
        theme: Optional[Dict[str, Any]] = None
    ) -> OasisAgentProfile:
        """
        Build an OASIS agent profile from a graph entity.

        Args:
            entity: The graph entity node
            user_id: User ID (used by OASIS)
            use_llm: Whether to build the detailed persona with the LLM
            theme: Audience prior allocated to this agent, if any. The entity
                supplies who they are; the theme supplies what they already
                think about the category.

        Returns:
            OasisAgentProfile
        """
        entity_type = entity.get_entity_type() or "Entity"

        # Basics
        name = entity.name
        user_name = self._generate_username(name)

        with pipeline_log.step(
            'OasisProfileGenerator', 'generate_profile',
            target=f"{entity_type}:{name}",
            user_id=user_id,
            use_llm=use_llm,
            entity_uuid=entity.uuid,
        ) as step:
            # Build the context
            context = self._build_entity_context(entity)
            step.input_text(context)
            step.note('entity context built', chars=len(context))

            if use_llm:
                # Build the detailed persona with the LLM
                profile_data = self._generate_profile_with_llm(
                    entity_name=name,
                    entity_type=entity_type,
                    entity_summary=entity.summary,
                    entity_attributes=entity.attributes,
                    context=context,
                    theme=theme
                )
                step.metric(source='llm', theme=(theme or {}).get('theme', ''))
            else:
                # Build a basic persona from the rules
                profile_data = self._generate_profile_rule_based(
                    entity_name=name,
                    entity_type=entity_type,
                    entity_summary=entity.summary,
                    entity_attributes=entity.attributes
                )
                step.metric(source='rule_based')

            step.output(profile=profile_data)
            step.metric(
                persona_chars=len(str(profile_data.get('persona') or '')),
                interests=len(profile_data.get('interested_topics') or []),
            )

        return OasisAgentProfile(
            user_id=user_id,
            user_name=user_name,
            name=name,
            bio=profile_data.get("bio", f"{entity_type}: {name}"),
            persona=profile_data.get("persona", entity.summary or f"A {entity_type} named {name}."),
            karma=profile_data.get("karma", random.randint(500, 5000)),
            friend_count=profile_data.get("friend_count", random.randint(50, 500)),
            follower_count=profile_data.get("follower_count", random.randint(100, 1000)),
            statuses_count=profile_data.get("statuses_count", random.randint(100, 2000)),
            age=profile_data.get("age"),
            gender=profile_data.get("gender"),
            mbti=profile_data.get("mbti"),
            country=profile_data.get("country"),
            profession=profile_data.get("profession"),
            interested_topics=profile_data.get("interested_topics", []),
            source_entity_uuid=entity.uuid,
            source_entity_type=entity_type,
        )
    
    def allocate_themes(self, count: int) -> List[Optional[Dict[str, Any]]]:
        """
        Assign one audience prior to each of ``count`` agents, by share.

        The distribution says 22.8% of public discussion is about price; this
        turns that into 22.8% of the agent pool carrying price as their standing
        concern. Telling a model "23% should care about price" does not reliably
        produce 23% - allocating the slots does.

        Largest-remainder apportionment, so the seats add up exactly and a theme
        with a real share is never rounded out of existence. Themes are assigned
        round-robin across the agent list rather than in blocks, so a truncated
        or partially failed run still covers the spread.

        Returns:
            A list of ``count`` entries, each a theme dict or None. Every entry
            is None when there is no distribution to apportion.
        """
        themes = (self.corpus_distribution or {}).get('themes') or []
        if not themes or count <= 0:
            return [None] * max(count, 0)

        total_share = sum(t.get('share_pct', 0) for t in themes)
        if total_share <= 0:
            return [None] * count

        # Exact seat counts, then hand the leftovers to the largest remainders.
        exact = [(t, t.get('share_pct', 0) / total_share * count) for t in themes]
        seats = [(theme, int(value)) for theme, value in exact]
        assigned = sum(n for _, n in seats)

        remainders = sorted(
            range(len(exact)),
            key=lambda i: exact[i][1] - int(exact[i][1]),
            reverse=True,
        )
        for i in remainders[:count - assigned]:
            theme, n = seats[i]
            seats[i] = (theme, n + 1)

        # Deal the seats round-robin: one from each theme in turn, so the first
        # agents generated already span the spread rather than all sharing the
        # single largest theme.
        queues = [[theme] * n for theme, n in seats if n > 0]
        allocation: List[Optional[Dict[str, Any]]] = []
        while queues and len(allocation) < count:
            for queue in queues:
                if queue:
                    allocation.append(queue.pop())
                    if len(allocation) >= count:
                        break
            queues = [q for q in queues if q]

        allocation.extend([None] * (count - len(allocation)))
        return allocation

    @staticmethod
    def _render_theme_prior(theme: Optional[Dict[str, Any]]) -> str:
        """Render one allocated theme as a prompt block."""
        if not theme:
            return ""

        lines = [
            "",
            "## This person's standing view of the category",
            f"Before any campaign ran, {theme.get('share_pct', 0)}% of public discussion "
            f"in this category was about: {theme.get('theme', '')} "
            f"(mostly {theme.get('dominant_sentiment', 'neutral')}).",
            "This persona is one of the people who holds that view. Real things "
            "people wrote about it:",
        ]
        for example in (theme.get('examples') or [])[:2]:
            quote = str(example.get('text', '')).replace('\n', ' ').strip()
            if quote:
                lines.append(f'  - "{quote}"')
        lines.append(
            "Build this into their category memory and their objections, in their "
            "own words - do not quote the above verbatim. It is what they already "
            "believed, not a reaction to the campaign."
        )
        return '\n'.join(lines)

    def _generate_username(self, name: str) -> str:
        """Generate a username."""
        # Strip special characters and lowercase
        username = name.lower().replace(" ", "_")
        username = ''.join(c for c in username if c.isalnum() or c == '_')
        
        # Append a random suffix to avoid collisions
        suffix = random.randint(100, 999)
        return f"{username}_{suffix}"
    
    def _search_graph_for_entity(self, entity: EntityNode) -> Dict[str, Any]:
        """
        Retrieve richer information about an entity via hybrid graph search.

        Args:
            entity: The entity node

        Returns:
            A dict of facts, node_summaries and context
        """
        entity_name = entity.name
        
        results = {
            "facts": [],
            "node_summaries": [],
            "context": ""
        }
        
        # A graph_id is required before searching
        if not self.graph_id:
            logger.debug("Skipping graph retrieval: graph_id is not set")
            return results

        query = t('progress.graphSearchQuery', name=entity_name)

        try:
            # One combined hybrid search rather than the two parallel calls the
            # Cloud client needed: edges and nodes come back from a single
            # round trip to the same database.
            search_result = search_graph(
                graph_id=self.graph_id,
                query=query,
                limit=30,
                scope="both",
            )

            results["facts"] = list({
                edge.fact for edge in search_result.edges if edge.fact
            })

            all_summaries = set()
            for node in search_result.nodes:
                if node.summary:
                    all_summaries.add(node.summary)
                if node.name and node.name != entity_name:
                    all_summaries.add(f"Related entity: {node.name}")
            results["node_summaries"] = list(all_summaries)

            # Assemble the combined context
            context_parts = []
            if results["facts"]:
                context_parts.append("Facts:\n" + "\n".join(f"- {f}" for f in results["facts"][:20]))
            if results["node_summaries"]:
                context_parts.append("Related entities:\n" + "\n".join(f"- {s}" for s in results["node_summaries"][:10]))
            results["context"] = "\n\n".join(context_parts)
            
            logger.info(f"hybrid retrieval complete: {entity_name}, {len(results['facts'])} facts, {len(results['node_summaries'])} related nodes")

        except Exception:
            # Enriched context is an improvement on the profile, not a
            # requirement for it - the entity's own attributes and edges are
            # already in hand. A retrieval failure degrades the persona rather
            # than failing the whole population run.
            logger.warning(f"graph retrieval failed ({entity_name})", exc_info=True)

        return results
    
    def _build_entity_context(self, entity: EntityNode) -> str:
        """
        Build the full context for an entity.

        Covers:
        1. The entity's own edges (facts)
        2. Detail on the connected nodes
        3. Whatever hybrid graph search turns up
        """
        context_parts = []
        
        # 1. Entity attributes
        if entity.attributes:
            attrs = []
            for key, value in entity.attributes.items():
                if value and str(value).strip():
                    attrs.append(f"- {key}: {value}")
            if attrs:
                context_parts.append("### Entity attributes\n" + "\n".join(attrs))
        
        # 2. Related edges (facts and relationships)
        existing_facts = set()
        if entity.related_edges:
            relationships = []
            for edge in entity.related_edges:  # No cap on the count
                fact = edge.get("fact", "")
                edge_name = edge.get("edge_name", "")
                direction = edge.get("direction", "")
                
                if fact:
                    relationships.append(f"- {fact}")
                    existing_facts.add(fact)
                elif edge_name:
                    if direction == "outgoing":
                        relationships.append(f"- {entity.name} --[{edge_name}]--> (related entity)")
                    else:
                        relationships.append(f"- (related entity) --[{edge_name}]--> {entity.name}")
            
            if relationships:
                context_parts.append("### Related facts and relationships\n" + "\n".join(relationships))
        
        # 3. Detail on the connected nodes
        if entity.related_nodes:
            related_info = []
            for node in entity.related_nodes:  # No cap on the count
                node_name = node.get("name", "")
                node_labels = node.get("labels", [])
                node_summary = node.get("summary", "")
                
                # Drop the default labels
                custom_labels = [l for l in node_labels if l not in ["Entity", "Node"]]
                label_str = f" ({', '.join(custom_labels)})" if custom_labels else ""
                
                if node_summary:
                    related_info.append(f"- **{node_name}**{label_str}: {node_summary}")
                else:
                    related_info.append(f"- **{node_name}**{label_str}")
            
            if related_info:
                context_parts.append("### Connected entities\n" + "\n".join(related_info))
        
        # 4. Pull richer information from hybrid graph search
        graph_results = self._search_graph_for_entity(entity)
        
        if graph_results.get("facts"):
            # Deduplicate against the facts we already have
            new_facts = [f for f in graph_results["facts"] if f not in existing_facts]
            if new_facts:
                context_parts.append("### Facts retrieved from the graph\n" + "\n".join(f"- {f}" for f in new_facts[:15]))
        
        if graph_results.get("node_summaries"):
            context_parts.append("### Related nodes retrieved from the graph\n" + "\n".join(f"- {s}" for s in graph_results["node_summaries"][:10]))
        
        return "\n\n".join(context_parts)
    
    def _entity_kind(self, entity_type: str) -> str:
        """
        The campaign's classification of this entity type, one of ENTITY_KINDS.

        Prefers ``entity_kinds`` - the per-campaign map the caller set -
        because the ontology names its types per campaign and no fixed list
        contains "GenZstudent".
        """
        return entity_kind(entity_type, self.entity_kinds)

    def _is_individual_entity(self, entity_type: str) -> bool:
        """Whether this entity type represents a natural person."""
        return is_person_kind(self._entity_kind(entity_type))

    def _generate_profile_with_llm(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str,
        theme: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build a very detailed persona with the LLM.

        The entity's kind decides the shape. Both axes matter: a person is
        written up differently from an organisation, and a named actor is
        written up differently from a representative one - the named actor has
        a real public record to stay true to, the representative one has none
        to invent.
        """

        kind = self._entity_kind(entity_type)
        is_individual = is_person_kind(kind)
        # A specific kind is one named actor with a real public record; a
        # general one is a representative member of a class, invented to fit.
        specific = not is_cloneable_kind(kind)

        builder = (self._build_individual_persona_prompt if is_individual
                   else self._build_group_persona_prompt)
        prompt = builder(
            entity_name, entity_type, entity_summary, entity_attributes, context,
            specific=specific,
        )

        prompt += self._render_theme_prior(theme)

        # Retry until it succeeds or the attempt budget runs out
        max_attempts = 3
        last_error = None
        
        for attempt in range(max_attempts):
            try:
                messages = [
                    {"role": "system", "content": self._get_system_prompt(is_individual)},
                    {"role": "user", "content": prompt}
                ]
                temperature = 0.7 - (attempt * 0.1)  # Lower the temperature on each retry
                call_started = time.perf_counter()
                response = create_chat_completion(
                    self.client,
                    model=self.model_name,
                    messages=messages,
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    # No max_tokens: let the model use its own limit
                )

                content = extract_chat_completion_text(response)

                # Detect truncation (finish_reason is not 'stop')
                finish_reason = response.choices[0].finish_reason

                pipeline_log.llm_call(
                    'OasisProfileGenerator', 'llm.persona',
                    model=self.model_name,
                    messages=messages,
                    params={'temperature': temperature, 'response_format': 'json_object'},
                    response_text=content,
                    duration_ms=(time.perf_counter() - call_started) * 1000,
                    target=f"{entity_type}:{entity_name}",
                    usage=_usage_dict(response),
                    attempts=attempt + 1,
                    extra_metrics={
                        'finish_reason': finish_reason,
                        'is_individual': is_individual,
                        'kind': kind,
                    },
                )

                if finish_reason == 'length':
                    logger.warning(f"LLM output was truncated (attempt {attempt+1}), attempting repair...")
                    content = self._fix_truncated_json(content)
                
                # Try to parse the JSON
                try:
                    result = json.loads(content)
                    
                    # Fill in the required fields
                    if "bio" not in result or not result["bio"]:
                        result["bio"] = entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}"
                    if "persona" not in result or not result["persona"]:
                        result["persona"] = entity_summary or f"{entity_name} is a {entity_type}."
                    
                    return result
                    
                except json.JSONDecodeError as je:
                    logger.warning(f"JSON parse failed (attempt {attempt+1}): {str(je)[:80]}")
                    
                    # Try to repair the JSON
                    result = self._try_fix_json(content, entity_name, entity_type, entity_summary)
                    repaired = bool(result.get("_fixed"))
                    pipeline_log.action(
                        'OasisProfileGenerator', 'persona_json_repair',
                        status='ok' if repaired else 'error',
                        target=f"{entity_type}:{entity_name}",
                        metrics={'attempt': attempt + 1, 'repaired': repaired},
                        error=str(je),
                    )
                    if repaired:
                        del result["_fixed"]
                        return result

                    last_error = je
                    
            except Exception as e:
                logger.warning(f"LLM call failed (attempt {attempt+1}): {str(e)[:80]}")
                last_error = e
                time.sleep(1 * (attempt + 1))  # Exponential backoff
        
        logger.warning(f"LLM profile generation failed after {max_attempts} attempts: {last_error}; falling back to rules")
        return self._generate_profile_rule_based(
            entity_name, entity_type, entity_summary, entity_attributes
        )
    
    def _fix_truncated_json(self, content: str) -> str:
        """Repair JSON truncated by the max_tokens limit."""
        import re
        
        # If the JSON was cut off, try to close it
        content = content.strip()
        
        # Count the unclosed brackets
        open_braces = content.count('{') - content.count('}')
        open_brackets = content.count('[') - content.count(']')
        
        # Check for an unterminated string. Rough heuristic: if the last
        # character is not a quote, comma or closing bracket, a string was cut off.
        if content and content[-1] not in '",}]':
            # Close the string
            content += '"'
        
        # Close the brackets
        content += ']' * open_brackets
        content += '}' * open_braces
        
        return content
    
    def _try_fix_json(self, content: str, entity_name: str, entity_type: str, entity_summary: str = "") -> Dict[str, Any]:
        """Try to repair malformed JSON."""
        import re
        
        # 1. Repair truncation first
        content = self._fix_truncated_json(content)
        
        # 2. Extract the JSON body
        json_match = re.search(r'\{[\s\S]*\}', content)
        if json_match:
            json_str = json_match.group()
            
            # 3. Fix newlines inside string literals by rewriting every
            #    string value
            def fix_string_newlines(match):
                s = match.group(0)
                # Replace literal newlines with spaces
                s = s.replace('\n', ' ').replace('\r', ' ')
                # Collapse runs of whitespace
                s = re.sub(r'\s+', ' ', s)
                return s
            
            # Match the JSON string values
            json_str = re.sub(r'"[^"\\]*(?:\\.[^"\\]*)*"', fix_string_newlines, json_str)
            
            # 4. Try to parse
            try:
                result = json.loads(json_str)
                result["_fixed"] = True
                return result
            except json.JSONDecodeError as e:
                # 5. Still failing: try a more aggressive repair
                try:
                    # Strip every control character
                    json_str = re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', json_str)
                    # Collapse all runs of whitespace
                    json_str = re.sub(r'\s+', ' ', json_str)
                    result = json.loads(json_str)
                    result["_fixed"] = True
                    return result
                except:
                    pass
        
        # 6. Fall back to salvaging individual fields from the raw text
        bio_match = re.search(r'"bio"\s*:\s*"([^"]*)"', content)
        persona_match = re.search(r'"persona"\s*:\s*"([^"]*)', content)  # May have been cut off
        
        bio = bio_match.group(1) if bio_match else (entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}")
        persona = persona_match.group(1) if persona_match else (entity_summary or f"{entity_name} is a {entity_type}.")
        
        # Something usable was salvaged: mark the result as repaired
        if bio_match or persona_match:
            logger.info(f"Salvaged partial fields from malformed JSON")
            return {
                "bio": bio,
                "persona": persona,
                "_fixed": True
            }
        
        # 7. Nothing worked: return a minimal structure
        logger.warning(f"JSON repair failed, returning a minimal structure")
        return {
            "bio": entity_summary[:200] if entity_summary else f"{entity_type}: {entity_name}",
            "persona": entity_summary or f"{entity_name} is a {entity_type}."
        }
    
    def _get_system_prompt(self, is_individual: bool) -> str:
        """Return the system prompt."""
        if is_individual:
            base_prompt = (
                "You are a consumer insights researcher who builds audience segment personas "
                "for marketing campaign testing. Produce detailed, believable buyer personas "
                "grounded in real demographics, needs, brand attitudes, purchase behaviour and "
                "media habits, staying as close to the real audience as possible. These personas "
                "will be dropped into a simulated social feed to react to campaign creative, so "
                "their buying motivations and their scepticism must both be concrete enough to "
                "drive a realistic reaction. You must return valid JSON, and no string value may "
                "contain an unescaped newline."
            )
        else:
            base_prompt = (
                "You are a consumer insights researcher who profiles the organisations, brands "
                "and media outlets that shape how a marketing campaign is received. Produce a "
                "detailed, believable account profile covering the organisation's role in the "
                "category, its stance towards the brand being advertised, and how it talks about "
                "products publicly. This account will be dropped into a simulated social feed to "
                "react to campaign creative. You must return valid JSON, and no string value may "
                "contain an unescaped newline."
            )
        return f"{base_prompt}\n\n{get_language_instruction()}"
    
    def _build_individual_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str,
        specific: bool = False,
    ) -> str:
        """
        Build the detailed persona prompt for a natural person.

        ``specific`` distinguishes ONE named person, who has a real public
        record the persona must stay true to, from a representative member of
        a class, who has none and must be invented to fit the class.
        """

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no additional context"

        if specific:
            framing = """Generate a detailed PERSONA for this ONE NAMED REAL PERSON, who will see a
marketing campaign in their social feed and react to it publicly. This is a
particular individual, not a type of person: build the persona from what is
actually known about them - their role, their public positions, what they have
said about this category before - and mark anything you must infer as
inference. Do not invent a biography that contradicts the record below."""
            # One named person has a real age; there is nothing to sample.
            demographics_rule = """Return "age" as an integer: this person's actual age, or your best estimate
from the record. Do NOT return gender, MBTI or country - those are filled in
separately and anything you return for them is discarded."""
        else:
            framing = """Generate a detailed AUDIENCE SEGMENT PERSONA for this entity: a buyer who will
see a marketing campaign in their social feed and react to it. This stands for
a class of people, not one named person, so invent one representative member
who is typical of the class. Stay as close to the real audience as possible."""
            demographics_rule = """Do NOT return age, gender, MBTI or country. Those are sampled from the
population distribution, not decided per person, and anything you return for
them is discarded. Write the persona so it stays true whatever age and
personality type this member of the class turns out to have."""

        return f"""{framing}

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

Return JSON with the following fields:

1. bio: social media bio, around 200 characters
2. persona: detailed buyer persona (around 2000 characters of plain text), covering:
   - Demographics (age, income band, occupation, education, household, location)
   - Needs and pain points (what problem in their life this product category
     addresses, what they currently do instead, what frustrates them about it)
   - Brand attitude (which brands in this category they trust or avoid and why,
     how loyal they are, how they react to being marketed to)
   - Purchase behaviour (price sensitivity, how they research before buying,
     who influences the decision, how long they deliberate, what triggers a
     purchase, what kills one)
   - Media habits (which platforms they use and when, what content they stop
     scrolling for, whether they share things publicly, what they consider
     worth reposting versus quietly ignoring)
   - Personality (MBTI type, core traits, how they express approval and
     annoyance in public)
   - Voice (posting frequency, tone, language quirks, catchphrases)
   - Category memory (a key part of the persona: their history with this
     product category and this brand - past purchases, past disappointments,
     what they have already said publicly about it)
3. profession: occupation
4. interested_topics: array of topics and product categories they follow

{demographics_rule}

Important:
- Every field value must be a string or a number, with no newline characters
- persona must read as one continuous passage
- {get_language_instruction()}
- The content must stay consistent with the entity information
- Give this persona a specific, non-neutral relationship to the product
  category. A persona with no clear buying motivation and no clear objection
  produces a useless campaign test.
"""

    def _build_group_persona_prompt(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any],
        context: str,
        specific: bool = True,
    ) -> str:
        """
        Build the detailed persona prompt for an organisation.

        ``specific`` distinguishes ONE named organisation, which has a real
        history to stay true to, from a class of firms, for which inventing a
        founding date and a formal name would be fabrication.
        """

        attrs_str = json.dumps(entity_attributes, ensure_ascii=False) if entity_attributes else "none"
        context_str = context[:3000] if context else "no additional context"

        if specific:
            framing = """Generate a detailed social media account profile for this ONE NAMED
organisation, brand, media outlet or community group - one of the collective
voices that shapes how a marketing campaign is received in this category. Stay
as close to the real organisation as possible: its actual history, remit and
public positions."""
            basics = ("Organisation basics (formal name, nature of the body, "
                      "founding background, main remit)")
        else:
            framing = """Generate a detailed social media account profile for a REPRESENTATIVE
organisation of this class - not one named firm, but a typical member of the
class, of the kind that shapes how a marketing campaign is received in this
category. Invent a plausible account of this kind rather than describing any
real company, and do not attach a real company's name, history or figures to
it."""
            basics = ("Organisation basics (the kind of body this is, its size band, "
                      "its position in the market - incumbent, challenger or niche - "
                      "and its main remit; no invented founding dates or real names)")

        return f"""{framing}

Entity name: {entity_name}
Entity type: {entity_type}
Entity summary: {entity_summary}
Entity attributes: {attrs_str}

Context:
{context_str}

Return JSON with the following fields:

1. bio: official account bio, around 200 characters, professional in tone
2. persona: detailed account description (around 2000 characters of plain text), covering:
   - {basics}
   - Role in the category (competitor, retail channel, trade press, consumer
     watchdog, review community, industry body, or the advertiser itself)
   - Audience it speaks to (who follows this account, and how much they trust it)
   - Account positioning (account type, core purpose, what it exists to say)
   - Stance towards the advertised brand and towards marketing in general
     (does it amplify campaigns, scrutinise claims, stay neutral, or compete?)
   - How it talks about products (does it quote prices, test claims, run
     comparisons, publish complaints?)
   - Voice (language traits, stock phrases, topics it avoids)
   - Content patterns (content types, posting frequency, active hours)
   - Institutional memory (a key part of the profile: this organisation's
     history with the advertised brand and category, and what it has already
     said publicly about them)
3. profession: description of the organisation's remit
4. interested_topics: array of focus areas

Do NOT return age, gender, MBTI or country. An institutional account is
assigned those automatically, and anything you return for them is discarded.

Important:
- Every field value must be a string or a number; null is not allowed
- persona must read as one continuous passage with no newline characters
- {get_language_instruction()}
- The account's voice must match its official identity, and its reaction to a
  campaign must follow from its role in the category rather than being neutral
  by default"""
    
    # One row per firm, positional - the field names live here, not repeated
    # 20 times in the model's output. See _generate_company_variants.
    _COMPANY_VARIANT_FIELDS = ("name", "position", "angle")

    def _generate_company_variants(
        self,
        entity_name: str,
        entity_type: str,
        base_persona: str,
        count: int,
    ) -> List[Dict[str, str]]:
        """
        Differentiate ``count`` firms off one ``general_company`` archetype.

        A general_company entity ("car companies") stands for a class, so its
        clones would otherwise be N copies of one persona - and unlike a person
        they have no age, gender or MBTI to vary them. This is the one extra
        LLM call that makes them distinct firms.

        Output tokens are the whole cost here, so the response is a positional
        array, not a list of objects: three short strings per firm with the
        field names carried by ``_COMPANY_VARIANT_FIELDS`` instead of repeated
        on every row. That is roughly 25 output tokens per firm against ~550
        for a full persona each, and about a third less than the same rows sent
        as JSON objects.

        Returns:
            Exactly ``count`` variants, or an empty list. A short response is
            cycled out to length and a failed one degrades to the shared
            archetype; neither raises.
        """
        if count <= 1:
            return []

        prompt = f"""Class of organisations: {entity_name} ({entity_type})

Archetype they all belong to:
{base_persona[:1200]}

Invent {count} DISTINCT firms of this class that would each hold a social
account and react to a campaign in this category. Vary size, market position
and attitude - include leaders and strugglers, defenders and undercutters.

Return compact JSON, one array per firm, in this exact field order:
["name", "position", "angle"]

- name: plausible invented company name, not a real one
- position: size and market position, at most 5 words
- angle: how it talks about rivals' campaigns, at most 12 words

No prose, no markdown, no keys, no trailing commentary. Exactly {count} rows.
{{"v":[["Northgate Motors","mid-size regional challenger","undercuts on price, quotes range figures"]]}}"""

        try:
            call_started = time.perf_counter()
            messages = [
                {"role": "system", "content":
                    "You differentiate a class of companies into distinct firms. "
                    "Return pure compact JSON and nothing else."},
                {"role": "user", "content": prompt},
            ]
            response = create_chat_completion(
                self.client,
                model=self.model_name,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.9,          # variety is the entire point
                # Three short strings per firm, plus room for the wrapper.
                max_tokens=200 + count * 40,
            )
            content = extract_chat_completion_text(response)
            pipeline_log.llm_call(
                'OasisProfileGenerator', 'llm.company_variants',
                model=self.model_name,
                messages=messages,
                params={'temperature': 0.9, 'response_format': 'json_object'},
                response_text=content,
                duration_ms=(time.perf_counter() - call_started) * 1000,
                target=f"{entity_type}:{entity_name}",
                usage=_usage_dict(response),
                extra_metrics={'requested': count},
            )
            rows = json.loads(content).get("v") or []
        except Exception as e:
            logger.warning(
                f"company variants failed for {entity_name}, falling back to the "
                f"shared archetype: {type(e).__name__}: {str(e)[:80]}"
            )
            return []

        variants: List[Dict[str, str]] = []
        for row in rows:
            if not isinstance(row, (list, tuple)):
                continue
            variant = {
                field: _coerce_to_str(row[i]).strip() if i < len(row) else ""
                for i, field in enumerate(self._COMPANY_VARIANT_FIELDS)
            }
            if variant.get("name"):
                variants.append(variant)

        if not variants:
            logger.warning(f"company variants for {entity_name} parsed to nothing")
            return []

        # A model that returned 17 rows for 20 firms must not leave three slots
        # unfilled, and one that returned 23 must not shift the rest.
        if len(variants) < count:
            variants = [variants[i % len(variants)] for i in range(count)]
        return variants[:count]

    def _generate_profile_rule_based(
        self,
        entity_name: str,
        entity_type: str,
        entity_summary: str,
        entity_attributes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Build a basic persona from the rules."""
        
        # The entity type decides the persona shape
        entity_type_lower = entity_type.lower()
        
        if entity_type_lower in ["student", "alumni"]:
            return {
                "bio": f"{entity_type} with interests in academics and social issues.",
                "persona": f"{entity_name} is a {entity_type.lower()} who is actively engaged in academic and social discussions. They enjoy sharing perspectives and connecting with peers.",
                "age": random.randint(18, 30),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": "Student",
                "interested_topics": ["Education", "Social Issues", "Technology"],
            }
        
        elif entity_type_lower in ["publicfigure", "expert", "faculty"]:
            return {
                "bio": f"Expert and thought leader in their field.",
                "persona": f"{entity_name} is a recognized {entity_type.lower()} who shares insights and opinions on important matters. They are known for their expertise and influence in public discourse.",
                "age": random.randint(35, 60),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(["ENTJ", "INTJ", "ENTP", "INTP"]),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_attributes.get("occupation", "Expert"),
                "interested_topics": ["Politics", "Economics", "Culture & Society"],
            }
        
        elif entity_type_lower in ["mediaoutlet", "socialmediaplatform"]:
            return {
                "bio": f"Official account for {entity_name}. News and updates.",
                "persona": f"{entity_name} is a media entity that reports news and facilitates public discourse. The account shares timely updates and engages with the audience on current events.",
                "age": 30,  # Notional age for an institutional account
                "gender": "other",  # Institutions use other
                "mbti": "ISTJ",  # Institutional voice: rigorous and conservative
                "country": "China",
                "profession": "Media",
                "interested_topics": ["General News", "Current Events", "Public Affairs"],
            }
        
        elif entity_type_lower in ["university", "governmentagency", "ngo", "organization"]:
            return {
                "bio": f"Official account of {entity_name}.",
                "persona": f"{entity_name} is an institutional entity that communicates official positions, announcements, and engages with stakeholders on relevant matters.",
                "age": 30,  # Notional age for an institutional account
                "gender": "other",  # Institutions use other
                "mbti": "ISTJ",  # Institutional voice: rigorous and conservative
                "country": "China",
                "profession": entity_type,
                "interested_topics": ["Public Policy", "Community", "Official Announcements"],
            }
        
        else:
            # Default persona
            return {
                "bio": entity_summary[:150] if entity_summary else f"{entity_type}: {entity_name}",
                "persona": entity_summary or f"{entity_name} is a {entity_type.lower()} participating in social discussions.",
                "age": random.randint(25, 50),
                "gender": random.choice(["male", "female"]),
                "mbti": random.choice(self.MBTI_TYPES),
                "country": random.choice(self.COUNTRIES),
                "profession": entity_type,
                "interested_topics": ["General", "Social Issues"],
            }
    
    def generate_profiles_from_entities(
        self,
        entities: List[EntityNode],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit",
        max_agents: int = MAX_AGENTS,
    ) -> List[OasisAgentProfile]:
        """
        Build the agent profiles for a set of entities.

        Convenience wrapper: plans the population itself (so the agent cap
        applies), then generates. Callers that need the slots downstream - to
        keep agent_ids aligned with the activity configs - should call
        ``plan_population`` and ``generate_profiles_from_slots`` directly.
        """
        slots = plan_population(entities, max_agents=max_agents)
        return self.generate_profiles_from_slots(
            slots=slots,
            use_llm=use_llm,
            progress_callback=progress_callback,
            graph_id=graph_id,
            parallel_count=parallel_count,
            realtime_output_path=realtime_output_path,
            output_platform=output_platform,
        )

    def generate_profiles_from_slots(
        self,
        slots: List[AgentSlot],
        use_llm: bool = True,
        progress_callback: Optional[callable] = None,
        graph_id: Optional[str] = None,
        parallel_count: int = 5,
        realtime_output_path: Optional[str] = None,
        output_platform: str = "reddit"
    ) -> List[OasisAgentProfile]:
        """
        Build one agent profile per planned slot.

        The persona is generated once per *entity*, not once per slot: clones
        of an entity share its persona and differ by their sampled age, gender,
        MBTI and country, and by the audience prior allocated to each of them.
        So the LLM cost tracks the number of entities in the graph, while the
        cast size is whatever ``plan_population`` decided.

        Args:
            slots: Planned population from ``plan_population``
            use_llm: Whether to build detailed personas with the LLM
            progress_callback: Progress callback (current, total, message)
            graph_id: Graph ID used for retrieval
            parallel_count: Number generated in parallel, default 5
            realtime_output_path: Incremental save path; when given, the file is
                rewritten after every persona
            output_platform: Output format ("reddit" or "twitter")

        Returns:
            The agent profiles, ordered by ``slot.user_id``
        """
        import concurrent.futures
        from contextvars import copy_context
        from threading import Lock

        # Set the graph_id used for retrieval
        if graph_id:
            self.graph_id = graph_id

        total_slots = len(slots)
        if not total_slots:
            return []

        # Group the slots by their source entity: one persona call per entity,
        # however many agents come off it.
        unique_entities: Dict[str, EntityNode] = {}
        slots_by_entity: Dict[str, List[AgentSlot]] = {}
        for slot in slots:
            key = getattr(slot.entity, 'uuid', None) or f"{slot.entity_type}:{slot.entity.name}"
            unique_entities.setdefault(key, slot.entity)
            slots_by_entity.setdefault(key, []).append(slot)
        entity_keys = list(unique_entities)

        total = len(entity_keys)  # Progress is reported over personas, not slots
        profiles = [None] * total_slots  # Preallocated so slot order is preserved
        completed_count = [0]  # A list so the closure can mutate it
        lock = Lock()

        # Apportion the audience priors across the whole cast, up front, so the
        # composition holds regardless of the order the workers finish in.
        theme_allocation = self.allocate_themes(total_slots)
        allocated = sum(1 for t in theme_allocation if t)
        if allocated:
            logger.info(
                f"allocated audience priors to {allocated}/{total_slots} agents "
                f"across {len({t['theme'] for t in theme_allocation if t})} themes"
            )

        # Helper that writes the file incrementally
        def save_profiles_realtime():
            """Write the profiles generated so far out to the file."""
            if not realtime_output_path:
                return
            
            with lock:
                # Keep only the profiles generated so far
                existing_profiles = [p for p in profiles if p is not None]
                if not existing_profiles:
                    return
                
                try:
                    if output_platform == "reddit":
                        # Reddit JSON format
                        profiles_data = [p.to_reddit_format() for p in existing_profiles]
                        with open(realtime_output_path, 'w', encoding='utf-8') as f:
                            json.dump(profiles_data, f, ensure_ascii=False, indent=2)
                    else:
                        # Twitter CSV format
                        import csv
                        profiles_data = [p.to_twitter_format() for p in existing_profiles]
                        if profiles_data:
                            fieldnames = list(profiles_data[0].keys())
                            with open(realtime_output_path, 'w', encoding='utf-8', newline='') as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(profiles_data)
                except Exception as e:
                    logger.warning(f"failed to save profiles incrementally: {e}")
        
        # Capture locale before spawning thread pool workers
        current_locale = get_locale()

        # {entity key: one variant per slot}, filled by the worker below for
        # general_company entities only. Each key is written by exactly one
        # worker before that worker's result is materialized, so no lock.
        company_variants: Dict[str, List[Dict[str, str]]] = {}

        def materialize(key: str, base: OasisAgentProfile) -> None:
            """Fan one entity's persona out over every slot that came off it."""
            variants = company_variants.get(key)
            for slot in slots_by_entity[key]:
                theme = theme_allocation[slot.user_id] if slot.user_id < len(theme_allocation) else None
                variant = variants[slot.variant_index % len(variants)] if variants else None
                profiles[slot.user_id] = self._profile_for_slot(base, slot, theme, variant)

        def generate_single_persona(key: str, entity: EntityNode) -> tuple:
            """Worker that generates the persona shared by one entity's slots."""
            set_locale(current_locale)
            entity_type = entity.get_entity_type() or "Entity"
            first_slot = slots_by_entity[key][0]

            try:
                profile = self.generate_profile_from_entity(
                    entity=entity,
                    user_id=first_slot.user_id,
                    use_llm=use_llm,
                    # The first slot's prior grounds the persona itself; every
                    # other slot gets its own appended in _profile_for_slot.
                    theme=(theme_allocation[first_slot.user_id]
                           if first_slot.user_id < len(theme_allocation) else None)
                )

                # A class of firms has no age or MBTI to tell its clones apart,
                # so one extra call splits the archetype into distinct firms.
                # Cheap: three short strings each, not a persona each.
                entity_slots = slots_by_entity[key]
                if (use_llm and len(entity_slots) > 1
                        and self._entity_kind(entity_type) == "general_company"):
                    company_variants[key] = self._generate_company_variants(
                        entity.name, entity_type, profile.persona, len(entity_slots)
                    )

                # Echo the generated persona to the console and the log
                self._print_generated_profile(entity.name, entity_type, profile)

                return key, profile, None

            except Exception as e:
                logger.error(f"failed to generate a profile for entity {entity.name}: {str(e)}")
                # Fall back to a basic profile
                fallback_profile = OasisAgentProfile(
                    user_id=first_slot.user_id,
                    user_name=self._generate_username(entity.name),
                    name=entity.name,
                    bio=f"{entity_type}: {entity.name}",
                    persona=entity.summary or f"A participant in social discussions.",
                    source_entity_uuid=entity.uuid,
                    source_entity_type=entity_type,
                )
                return key, fallback_profile, str(e)

        clone_count = total_slots - total
        logger.info(
            f"generating {total} personas in parallel (concurrency: {parallel_count}) "
            f"for {total_slots} agents ({clone_count} clones, one extra call per "
            f"cloned general_company entity)..."
        )
        print(f"\n{'='*60}")
        print(f"generating agent profiles - {total} entities -> {total_slots} agents, "
              f"concurrency: {parallel_count}")
        print(f"{'='*60}\n")

        # Run on a thread pool
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel_count) as executor:
            # Submit every task. Each worker runs inside its own copy of the
            # caller's context so the pipeline run and stage carry over into
            # the pool threads, which do not inherit context by default.
            future_to_entity = {
                executor.submit(copy_context().run, generate_single_persona, key, unique_entities[key]): key
                for key in entity_keys
            }

            # Collect the results
            for future in concurrent.futures.as_completed(future_to_entity):
                key = future_to_entity[future]
                entity = unique_entities[key]
                entity_type = entity.get_entity_type() or "Entity"

                try:
                    result_key, profile, error = future.result()
                    materialize(result_key, profile)

                    with lock:
                        completed_count[0] += 1
                        current = completed_count[0]
                    
                    # Write out incrementally
                    save_profiles_realtime()
                    
                    if progress_callback:
                        progress_callback(
                            current, 
                            total, 
                            f"completed {current}/{total}: {entity.name} ({entity_type})"
                        )
                    
                    if error:
                        logger.warning(f"[{current}/{total}] {entity.name} using a fallback profile: {error}")
                    else:
                        logger.info(f"[{current}/{total}] generated profile: {entity.name} ({entity_type})")

                except Exception as e:
                    logger.error(f"error while processing entity {entity.name}: {str(e)}")
                    with lock:
                        completed_count[0] += 1
                    materialize(key, OasisAgentProfile(
                        user_id=slots_by_entity[key][0].user_id,
                        user_name=self._generate_username(entity.name),
                        name=entity.name,
                        bio=f"{entity_type}: {entity.name}",
                        persona=entity.summary or "A participant in social discussions.",
                        source_entity_uuid=entity.uuid,
                        source_entity_type=entity_type,
                    ))
                    # Write out incrementally, even for a fallback persona
                    save_profiles_realtime()

        print(f"\n{'='*60}")
        print(f"profile generation complete: {len([p for p in profiles if p])} agents "
              f"from {total} personas")
        print(f"{'='*60}\n")

        return profiles

    def _profile_for_slot(
        self,
        base: OasisAgentProfile,
        slot: AgentSlot,
        theme: Optional[Dict[str, Any]] = None,
        variant: Optional[Dict[str, str]] = None,
    ) -> OasisAgentProfile:
        """
        Stamp one slot's sampled attributes onto its entity's persona.

        The demographics come from the population plan, not from the model, and
        the slot's own audience prior is appended so two clones of one entity
        walk into the campaign already disagreeing about the category.

        Two exceptions:
        - a specific_individual is a named real person with a real age, so the
          persona call's answer beats a draw from the type's age band
        - a general_company slot carries a variant, which gives it its own firm
          name and market position on top of the shared archetype
        """
        persona = base.persona
        prior = self._theme_sentence(theme)
        if prior and prior not in persona:
            persona = f"{persona} {prior}"

        name, user_name = base.name, f"{base.user_name}_{slot.user_id}"
        if variant and variant.get("name"):
            name = variant["name"]
            user_name = f"{self._generate_username(name)}_{slot.user_id}"
            persona = f"{persona} {self._variant_sentence(variant)}"

        age = slot.age
        if slot.kind == "specific_individual" and base.age:
            try:
                age = int(base.age)
            except (TypeError, ValueError):
                pass

        return replace(
            base,
            user_id=slot.user_id,
            name=name,
            # The base username carries a random suffix; the slot id is what
            # actually guarantees uniqueness across a 500-agent cast.
            user_name=user_name,
            persona=persona,
            age=age,
            gender=slot.gender,
            mbti=slot.mbti,
            country=slot.country,
        )

    @staticmethod
    def _demographics_sentence(profile: OasisAgentProfile) -> str:
        """The sampled attributes as one sentence, for platforms with no slot for them."""
        parts = []
        if profile.gender and profile.gender != "other":
            parts.append(f"You are {profile.gender}")
        if profile.age:
            parts.append(f"{profile.age} years old")
        if profile.mbti:
            parts.append(f"MBTI type {profile.mbti}")
        if profile.country:
            parts.append(f"based in {profile.country}")
        return f"{', '.join(parts)}." if parts else ""

    @staticmethod
    def _variant_sentence(variant: Dict[str, str]) -> str:
        """The firm's own identity, layered over the class archetype."""
        parts = [f"You are {variant['name']}"]
        if variant.get("position"):
            parts.append(f"a {variant['position']} in this category")
        sentence = ", ".join(parts) + "."
        if variant.get("angle"):
            sentence += f" How you talk about rivals' campaigns: {variant['angle']}."
        return sentence

    @staticmethod
    def _theme_sentence(theme: Optional[Dict[str, Any]]) -> str:
        """One persona-facing line stating the prior this agent already holds."""
        if not theme or not theme.get('theme'):
            return ""
        return (
            f"Your standing view of this category, held long before this campaign "
            f"appeared: {theme['theme']} "
            f"(you feel {theme.get('dominant_sentiment', 'neutral')} about it)."
        )

    def _print_generated_profile(self, entity_name: str, entity_type: str, profile: OasisAgentProfile):
        """Echo a generated persona to the console in full, without truncation."""
        separator = "-" * 70
        
        # Build the full output, untruncated
        topics_str = ', '.join(profile.interested_topics) if profile.interested_topics else 'none'
        
        output_lines = [
            f"\n{separator}",
            t('progress.profileGenerated', name=entity_name, type=entity_type),
            f"{separator}",
            f"Username: {profile.user_name}",
            f"",
            f"[Bio]",
            f"{profile.bio}",
            f"",
            f"[Persona]",
            f"{profile.persona}",
            f"",
            f"[Attributes]",
            f"Age: {profile.age} | Gender: {profile.gender} | MBTI: {profile.mbti}",
            f"Profession: {profile.profession} | Country: {profile.country}",
            f"Interests: {topics_str}",
            separator
        ]
        
        output = "\n".join(output_lines)
        
        # Console only; the logger no longer echoes the full content
        print(output)
    
    def save_profiles(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """
        Write the profiles out in the format the platform requires.

        OASIS format requirements:
        - Twitter: CSV
        - Reddit: JSON

        Args:
            profiles: The profiles
            file_path: Output path
            platform: Platform ("reddit" or "twitter")
        """
        if platform == "twitter":
            self._save_twitter_csv(profiles, file_path)
        else:
            self._save_reddit_json(profiles, file_path)
    
    def _save_twitter_csv(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Write the Twitter profiles as CSV, per the OASIS specification.

        CSV fields required by OASIS Twitter:
        - user_id: user ID (0-based, following the CSV order)
        - name: the user's real name
        - username: the in-system username
        - user_char: the detailed persona, injected into the LLM system prompt
          to drive agent behaviour
        - description: a short public bio shown on the profile page

        user_char vs description:
        - user_char: internal, goes into the LLM system prompt and decides how
          the agent thinks and acts
        - description: external, the bio other users can see
        """
        import csv
        
        # Force the .csv extension
        if not file_path.endswith('.csv'):
            file_path = file_path.replace('.json', '.csv')
        
        with open(file_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            
            # Write the header OASIS expects
            headers = ['user_id', 'name', 'username', 'user_char', 'description']
            writer.writerow(headers)
            
            # Write the data rows
            for idx, profile in enumerate(profiles):
                # user_char: the full persona (bio + persona), used in the LLM system prompt
                user_char = profile.bio
                if profile.persona and profile.persona != profile.bio:
                    user_char = f"{profile.bio} {profile.persona}"
                # The Twitter system message has no demographics slot, so the
                # sampled attributes only reach the agent through user_char.
                user_char = f"{user_char} {self._demographics_sentence(profile)}".strip()
                # Replace newlines with spaces so the CSV stays well formed
                user_char = user_char.replace('\n', ' ').replace('\r', ' ')
                
                # description: the short bio shown externally
                description = profile.bio.replace('\n', ' ').replace('\r', ' ')
                
                row = [
                    idx,                    # user_id: 0-based sequential ID
                    profile.name,           # name: real name
                    profile.user_name,      # username
                    user_char,              # user_char: full persona (internal, for the LLM)
                    description             # description: short bio (shown externally)
                ]
                writer.writerow(row)
        
        logger.info(f"saved {len(profiles)} Twitter profiles to {file_path} (OASIS CSV format)")
    
    def _normalize_gender(self, gender: Optional[str]) -> str:
        """
        Normalize the gender field into the English values OASIS requires.

        OASIS accepts: male, female, other
        """
        if not gender:
            return "other"
        
        gender_lower = gender.lower().strip()
        
        # Chinese values are kept here on purpose: when the zh locale is
        # active the LLM answers in Chinese, and those answers still have to
        # normalize onto the English values OASIS requires.
        gender_map = {
            "男": "male",
            "女": "female",
            "机构": "other",
            "其他": "other",
            # Already English
            "male": "male",
            "female": "female",
            "other": "other",
        }
        
        return gender_map.get(gender_lower, "other")
    
    def _save_reddit_json(self, profiles: List[OasisAgentProfile], file_path: str):
        """
        Write the Reddit profiles as JSON.

        Uses the same shape as to_reddit_format() so OASIS can read it. The
        user_id field is mandatory - it is what OASIS agent_graph.get_agent()
        matches on.

        Required fields:
        - user_id: user ID (integer, matched against poster_agent_id in initial_posts)
        - username
        - name: display name
        - bio
        - persona: the detailed persona
        - age: integer
        - gender: "male", "female" or "other"
        - mbti: MBTI type
        - country
        """
        data = []
        for idx, profile in enumerate(profiles):
            # Same shape as to_reddit_format()
            item = {
                "user_id": profile.user_id if profile.user_id is not None else idx,  # Critical: user_id must be present
                "username": profile.user_name,
                "name": profile.name,
                "bio": profile.bio[:150],
                "persona": profile.persona,
                "karma": profile.karma if profile.karma else 1000,
                "created_at": profile.created_at,
                # Fields OASIS requires - always give them a default
                "age": profile.age if profile.age else 30,
                "gender": self._normalize_gender(profile.gender),
                "mbti": profile.mbti if profile.mbti else "ISTJ",
                # Deliberately null when the brief named no market: the agent
                # system message then omits the country clause entirely rather
                # than asserting a nationality nobody asked for.
                "country": profile.country or None,
            }
            
            # Optional fields
            if profile.profession:
                item["profession"] = profile.profession
            if profile.interested_topics:
                item["interested_topics"] = profile.interested_topics
            
            data.append(item)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"saved {len(profiles)} Reddit profiles to {file_path} (JSON format, includes user_id)")
    
    # Old method name kept as an alias for backwards compatibility
    def save_profiles_to_json(
        self,
        profiles: List[OasisAgentProfile],
        file_path: str,
        platform: str = "reddit"
    ):
        """[Deprecated] Use save_profiles() instead."""
        logger.warning("save_profiles_to_json is deprecated; use save_profiles instead")
        self.save_profiles(profiles, file_path, platform)

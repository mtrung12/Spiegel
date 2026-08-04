"""
Plan the agent population for one simulation.

Two jobs, both of which used to cost an LLM call per agent:

1. Size the population. Entities come out of the graph in whatever number the
   source material happened to yield; the simulation needs a bounded, evenly
   spread cast. Too many entities are truncated, too few are cloned.
2. Sample the demographics. Age, gender, MBTI and country are population
   statistics, not judgements about an individual entity, so they are drawn
   from distributions rather than invented one agent at a time.

The persona text is NOT produced here - that stays one LLM call per entity in
OasisProfileGenerator. Clones of one entity share its persona and differ by
their sampled attributes and their allocated theme prior.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..utils.logger import get_logger
from ..utils.ontology import (
    is_cloneable_kind,
    is_person_kind,
    normalize_entity_kind,
)
from ..utils.pipeline_logger import llm_caller

logger = get_logger('spiegel.agent_population')

# Hard ceiling on the simulated cast. Raising it costs no extra LLM calls at
# profile time, but it does widen the per-hour exposure range the time-config
# prompt is given (see MAX_AGENTS note in simulation_config_generator).
MAX_AGENTS = 500

# How the cloned slots split between generic companies and generic people:
# 10/90 of whatever is left once every specific (named) entity has taken its
# one agent. See Config.GENERAL_COMPANY_SHARE for where the number comes from
# and why it is a knob rather than a constant.
#
# Read at call time, not import time, so a test or a run can override it.
def _company_share() -> float:
    from ..config import Config
    return max(0.0, min(1.0, float(Config.GENERAL_COMPANY_SHARE)))

# Entity types that stand for a class of people, so N copies of one are
# meaningful. Everything else - a named brand, a named journalist, the
# advertiser itself - is a singleton: 40 copies of one CEO is nonsense.
#
# Only a fallback. The ontology invents its own type names per campaign
# ("GenZstudent", "TeslaLoyalist", "Evskeptic"), none of which are in any
# fixed list, so the authoritative answer comes from ``derive_population_hints``
# and lands here as the ``kinds`` map. Note that "Organization" is deliberately
# absent: it is the ontology's fallback org type, which is almost always the
# advertiser or a media outlet, and cloning it produced 250 copies of one brand.
CLONEABLE_ENTITY_TYPES = {
    "person", "consumer", "customer", "buyer",
    "shopper", "student", "alumni", "prospect",
}

# The ontology's own generic org type names. Unlike a campaign-invented name,
# these really do mean "one organisation with one account" - almost always the
# advertiser or a media outlet - so they stay singletons when unclassified.
FALLBACK_COMPANY_ENTITY_TYPES = {
    "organization", "organisation", "company", "brand", "entity",
    "mediaoutlet", "socialmediaplatform", "university",
    "governmentagency", "ngo",
}

# What an unclassified, campaign-invented type name becomes. See entity_kind().
_UNCLASSIFIED_KIND = "general_individual"

# entity_kind() runs once per entity, so the warning is deduped to one line per
# type name rather than 500 identical ones.
_warned_unclassified: set = set()

# Entity types that represent a natural person (and so get an age, a gender
# and a real MBTI). Single source of truth - OasisProfileGenerator should
# import this rather than keep its own copy.
INDIVIDUAL_ENTITY_TYPES = {
    "student", "alumni", "professor", "person", "publicfigure",
    "expert", "faculty", "official", "journalist", "activist",
    "consumer", "customer", "buyer", "shopper", "influencer",
    "reviewer", "creator", "prospect",
}

# Published global frequency estimates, in percent. Sums to 100.
# ponytail: one global table. Swap for a market-specific one when a campaign
# targets a single country and the difference starts to matter.
MBTI_DISTRIBUTION: Dict[str, float] = {
    "INTJ": 2.1, "INTP": 3.3, "ENTJ": 1.8, "ENTP": 3.2,
    "INFJ": 1.5, "INFP": 4.4, "ENFJ": 2.5, "ENFP": 8.1,
    "ISTJ": 11.6, "ISFJ": 13.8, "ESTJ": 8.7, "ESFJ": 12.0,
    "ISTP": 5.4, "ISFP": 8.8, "ESTP": 4.3, "ESFP": 8.5,
}
_MBTI_TYPES = list(MBTI_DISTRIBUTION)
_MBTI_WEIGHTS = list(MBTI_DISTRIBUTION.values())

# Fallback age bands, used for an entity type the age-range call did not cover.
DEFAULT_AGE_RANGE = (25, 55)
INSTITUTIONAL_AGE = 30

def entity_kind(entity_type: str, kinds: Optional[Dict[str, str]] = None) -> str:
    """
    Classify one entity type, preferring the per-campaign answer.

    ``kinds`` is the ontology's own ``kind`` field where the ontology carried
    one, topped up by ``derive_population_hints`` for the types it did not.
    Falls back to the fixed name lists when neither covered the type, which is
    also the path ontologies built before the field existed take.
    """
    key = (entity_type or "").lower()
    if kinds:
        kind = normalize_entity_kind(kinds.get(entity_type) or kinds.get(key))
        if kind:
            return kind
    if key in CLONEABLE_ENTITY_TYPES:
        return "general_individual"
    if key in INDIVIDUAL_ENTITY_TYPES:
        return "specific_individual"
    # Unclassified. "Organization" is the ontology's own fallback org type, so
    # it really is the advertiser or a media outlet - one account, never cloned.
    if key in FALLBACK_COMPANY_ENTITY_TYPES:
        return "specific_company"
    # Everything else here is a campaign-invented name ("GenZstudent",
    # "Evskeptic") that no list contains and no classifier reached. Treating it
    # as a named brand collapses the whole cast to the entity count, because a
    # specific kind is never cloned: 23 entities produced 23 agents against a
    # cap of 200. A class of people is what these names almost always are, and
    # it is also the recoverable mistake - an over-cloned segment still
    # simulates, an under-populated cast does not.
    if key not in _warned_unclassified:
        _warned_unclassified.add(key)
        logger.warning(
            "entity type %r reached no classifier (ontology kind absent and "
            "population hints empty); treating it as %s. A named brand here "
            "would be over-cloned - check the ontology carries a 'kind'.",
            entity_type, _UNCLASSIFIED_KIND,
        )
    return _UNCLASSIFIED_KIND


@dataclass
class AgentSlot:
    """One agent to be built: which entity it comes from, plus its sampled attributes."""

    user_id: int
    entity: Any
    entity_type: str
    variant_index: int          # 0 for the first agent off an entity, 1.. for clones
    is_clone: bool
    age: int
    gender: str                 # "male" | "female" | "other"
    mbti: str
    country: Optional[str]           # None when the brief names no market
    kind: str = "specific_company"   # one of ENTITY_KINDS, see entity_kind()

    @property
    def is_individual(self) -> bool:
        # Both individual kinds are natural persons, whether this slot is the
        # one named journalist or one of 300 members of a class, so both get a
        # person's demographics and a person's persona prompt.
        return is_person_kind(self.kind)


# ── Sampling ────────────────────────────────────────────────────

def sample_mbti(rng: random.Random) -> str:
    return rng.choices(_MBTI_TYPES, weights=_MBTI_WEIGHTS, k=1)[0]


def sample_gender(is_individual: bool, rng: random.Random) -> str:
    # ponytail: 50/50. Add a per-type skew only if a brief ever needs one.
    if not is_individual:
        return "other"
    return rng.choice(["male", "female"])


def sample_age(entity_type: str, age_ranges: Dict[str, Tuple[int, int]],
               is_individual: bool, rng: random.Random) -> int:
    if not is_individual:
        return INSTITUTIONAL_AGE
    lo, hi = age_ranges.get(entity_type, age_ranges.get(entity_type.lower(), DEFAULT_AGE_RANGE))
    lo, hi = int(lo), int(hi)
    if hi < lo:
        lo, hi = hi, lo
    return rng.randint(lo, hi)


def sample_country(markets: Sequence[str], rng: random.Random) -> Optional[str]:
    """None when the brief names no market - the caller then omits the clause."""
    if not markets:
        return None
    if len(markets) == 1:
        return markets[0]
    return rng.choice(list(markets))


# ── Population sizing ───────────────────────────────────────────

def _entity_type(entity: Any) -> str:
    getter = getattr(entity, "get_entity_type", None)
    return (getter() if callable(getter) else None) or getattr(entity, "entity_type", None) or "Entity"


def _stratified_truncate(entities: List[Any], keep: int, rng: random.Random) -> List[Any]:
    """
    Cut down to ``keep`` entities without wiping out a whole type.

    A flat random sample can drop every MediaOutlet in the graph, which
    silently degrades the simulation rather than failing it. Round-robin
    across types keeps every type represented while staying random within one.
    """
    if keep >= len(entities):
        return list(entities)

    by_type: Dict[str, List[Any]] = defaultdict(list)
    for entity in entities:
        by_type[_entity_type(entity)].append(entity)
    for bucket in by_type.values():
        rng.shuffle(bucket)

    types = sorted(by_type)               # sorted so a seed reproduces exactly
    rng.shuffle(types)
    kept: List[Any] = []
    while len(kept) < keep:
        progressed = False
        for entity_type in types:
            bucket = by_type[entity_type]
            if not bucket:
                continue
            kept.append(bucket.pop())
            progressed = True
            if len(kept) == keep:
                break
        if not progressed:                # every bucket drained
            break
    return kept


def _spread(pool: List[Any], budget: int, rng: random.Random) -> List[Tuple[Any, int]]:
    """
    Split ``budget`` slots over ``pool`` as evenly as possible.

    The leftover from the integer division goes to a random subset, so the
    extra agent is not always handed to whoever sorts first. Assumes
    ``budget >= len(pool)``; the caller handles the tighter case.
    """
    if not pool:
        return []
    base, leftover = divmod(budget, len(pool))
    bonus = set(rng.sample(range(len(pool)), leftover))
    return [(e, base + (1 if i in bonus else 0)) for i, e in enumerate(pool)]


def _allocate_clones(entities: List[Any], budget: int, rng: random.Random,
                     kinds: Optional[Dict[str, str]] = None) -> List[Tuple[Any, int]]:
    """
    Spread ``budget`` agent slots over the entities, as evenly as possible.

    Specific types (a named brand, a named person) always get exactly one -
    there is only one of them in the world. Everything left over is the clone
    budget, and it splits 10/90 between ``general_company`` and
    ``general_individual``: the audience is what the simulation measures, and
    generic firms are the background it reacts against.

    A budget the general types cannot absorb is left unspent, so the cast comes
    out smaller than the cap rather than filled with 400 copies of one firm.
    """
    singletons, firms, people = [], [], []
    for entity in entities:
        kind = entity_kind(_entity_type(entity), kinds)
        if not is_cloneable_kind(kind):
            singletons.append(entity)
        elif is_person_kind(kind):
            people.append(entity)
        else:
            firms.append(entity)

    if len(singletons) >= budget:
        return [(e, 1) for e in _stratified_truncate(singletons, budget, rng)]

    remaining = budget - len(singletons)
    counts = [(e, 1) for e in singletons]

    pool = firms + people
    if not pool:
        return counts
    if remaining < len(pool):
        # Not even one slot each: keep a stratified subset, one agent apiece.
        return counts + [(e, 1) for e in _stratified_truncate(pool, remaining, rng)]

    # 10% of the clone budget to the generic firms, 90% to the audience - but
    # never less than one slot per firm, so a graph with more firm entities
    # than the share allows still represents each of them once.
    # remaining >= len(firms) + len(people), so the ceiling never bites into
    # that floor.
    firm_budget = 0
    if firms:
        firm_budget = max(len(firms),
                          min(round(remaining * _company_share()),
                              remaining - len(people)))

    counts += _spread(firms, firm_budget, rng)
    counts += _spread(people, remaining - firm_budget, rng)
    return counts


def planned_population_size(entities: Iterable[Any], max_agents: int = MAX_AGENTS,
                            kinds: Optional[Dict[str, str]] = None) -> int:
    """
    How many agents ``plan_population`` would produce, without sampling any.

    Lets a caller report the cast size up front - the count is decided by the
    entity mix and the cap, not by any of the sampling.

    Runs the real allocation rather than re-deriving it: the general_company
    cap means the answer is no longer just "the cap, or the entity count", and
    two copies of that rule would drift apart.
    """
    entities = list(entities)
    if not entities or max_agents <= 0:
        return 0
    if len(entities) >= max_agents:
        return max_agents
    return sum(n for _, n in _allocate_clones(entities, max_agents, random.Random(0), kinds))


def plan_population(
    entities: Iterable[Any],
    *,
    max_agents: int = MAX_AGENTS,
    age_ranges: Optional[Dict[str, Tuple[int, int]]] = None,
    markets: Sequence[str] = (),
    kinds: Optional[Dict[str, str]] = None,
    seed: Optional[int] = None,
) -> List[AgentSlot]:
    """
    Turn the graph's entities into exactly ``min(len(entities_expanded), max_agents)``
    agent slots.

    More entities than ``max_agents``: truncated, one agent each.
    Fewer: the general types are cloned up to the cap, the specific ones stay
    at one agent each.

    Args:
        entities: Entity nodes from the graph
        max_agents: Hard ceiling on the cast
        age_ranges: ``{entity_type: (lo, hi)}``, from the one-shot age-range call
        markets: Countries the brief names. Empty means "brief names none", and
            every slot gets ``country=None``
        kinds: ``{entity_type: kind}`` over ENTITY_KINDS, from the same one-shot
            call. Decides what gets cloned and what gets a person's
            demographics; without it the fixed name lists in ``entity_kind``
            apply
        seed: Fixes the sampling, for tests and reproducible runs

    Returns:
        The slots, shuffled, with ``user_id`` assigned 0..n-1 in list order.
    """
    rng = random.Random(seed)
    entities = list(entities)
    age_ranges = age_ranges or {}
    if not entities or max_agents <= 0:
        return []

    if len(entities) >= max_agents:
        allocation = [(e, 1) for e in _stratified_truncate(entities, max_agents, rng)]
    else:
        allocation = _allocate_clones(entities, max_agents, rng, kinds)

    slots: List[AgentSlot] = []
    for entity, count in allocation:
        entity_type = _entity_type(entity)
        kind = entity_kind(entity_type, kinds)
        is_individual = is_person_kind(kind)
        for variant_index in range(count):
            slots.append(AgentSlot(
                user_id=-1,                      # assigned below, after the shuffle
                entity=entity,
                entity_type=entity_type,
                variant_index=variant_index,
                is_clone=variant_index > 0,
                age=sample_age(entity_type, age_ranges, is_individual, rng),
                gender=sample_gender(is_individual, rng),
                mbti=sample_mbti(rng),
                country=sample_country(markets, rng),
                kind=kind,
            ))

    # Interleave the clones so downstream round-robin passes (theme priors,
    # initial-post assignment) do not walk one entity's block at a time.
    rng.shuffle(slots)
    for user_id, slot in enumerate(slots):
        slot.user_id = user_id
    return slots


# ── Population hints (one LLM call for the whole run) ───────────

# How much of the brief the hints call reads. The market and the audience are
# always stated early.
_BRIEF_CHAR_LIMIT = 8000


def derive_population_hints(
    entity_types: Sequence[str],
    brief: str,
    llm_client: Any = None,
) -> Tuple[Dict[str, Tuple[int, int]], List[str], Dict[str, str]]:
    """
    One LLM call for the whole run: an age band per entity type, the markets
    the brief names, and what each type actually stands for.

    This replaces asking the model for a concrete age and country per agent.
    A model is good at "students in this category are 18-24"; it is not a
    random number generator, and paying per agent for one was the waste.

    The kind matters as much as the age band: the ontology names its types per
    campaign, so nothing downstream can tell "GenZstudent" (a mass audience,
    clone it) from "Tesla" (one brand, never clone it) by name alone.

    Returns:
        ``({entity_type: (lo, hi)}, [market, ...], {entity_type: kind})``. All
        may be empty - the caller then falls back to DEFAULT_AGE_RANGE, omits
        the country clause entirely and classifies by name. A failed call is
        not fatal.
    """
    types = [t for t in dict.fromkeys(entity_types) if t]
    if not types:
        return {}, [], {}

    prompt = f"""Below is a marketing campaign brief, followed by the audience segment
types we modelled from it.

---
{(brief or '')[:_BRIEF_CHAR_LIMIT]}
---

Segment types: {', '.join(types)}

Return three things:
- "age_ranges": for each segment type, the plausible age band of a real member
  of that segment in this category, as [min, max]. Use the campaign's actual
  target audience, not a generic population. Institutional segments (brands,
  media, retailers) still need a band; give [30, 30].
- "markets": the countries this campaign explicitly targets, as named in the
  brief. Return [] when the brief names no country - do not guess one from the
  language it is written in.
- "kinds": for each segment type, exactly one of:
  - "specific_individual": ONE named natural person - a named CEO, a named
    journalist, a named creator. There is only one of them in the world.
  - "specific_company": ONE named organisation, brand, media outlet, agency or
    platform, the advertiser itself included. It posts from one official
    account.
  - "general_individual": a class of ordinary people, of whom the campaign
    reaches thousands (students, loyalists, sceptics, commuters, first-time
    buyers, "CEOs" as a group). We simulate many independent members of it.
  - "general_company": a class of organisations rather than one named firm
    (car companies, EV startups, regional dealerships, trade press in general).
  Two independent questions: is it ONE named actor or a class of them, and is
  it a person or an organisation. Judge what the type stands for in this
  campaign, not what its name resembles.

Return JSON only:
{{"age_ranges": {{"Student": [18, 24]}}, "markets": ["Vietnam"],
  "kinds": {{"Student": "general_individual"}}}}"""

    try:
        client = llm_client
        if client is None:
            from ..utils.llm_client import LLMClient
            client = LLMClient()
        with llm_caller('AgentPopulation', f"{len(types)} segment types"):
            result = client.chat_json(
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You size audience populations for market research. Return "
                            "pure JSON. Never invent a market the brief does not name."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.0,
                max_tokens=2048,
                max_attempts=2,
            )
    except Exception as e:
        logger.warning(
            "population hints failed, falling back to default age bands "
            "and no market: %s: %s", type(e).__name__, str(e)[:120],
        )
        return {}, [], {}

    age_ranges: Dict[str, Tuple[int, int]] = {}
    for entity_type, band in (result.get("age_ranges") or {}).items():
        try:
            lo, hi = int(band[0]), int(band[1])
        except (TypeError, ValueError, IndexError, KeyError):
            continue
        lo, hi = max(13, min(lo, hi)), min(95, max(lo, hi))
        if lo <= hi:
            age_ranges[str(entity_type)] = (lo, hi)

    markets = [
        str(m).strip() for m in (result.get("markets") or [])
        if isinstance(m, (str, int)) and str(m).strip()
    ][:8]

    kinds: Dict[str, str] = {}
    for entity_type, kind in (result.get("kinds") or {}).items():
        normalized = normalize_entity_kind(kind)
        if normalized:
            kinds[str(entity_type)] = normalized

    logger.info(
        "population hints: %d age bands, markets=%s, %d/%d types classified "
        "(%d cloneable)",
        len(age_ranges), markets or "none named", len(kinds), len(types),
        sum(1 for k in kinds.values() if is_cloneable_kind(k)),
    )
    return age_ranges, markets, kinds


# ── Self-check ──────────────────────────────────────────────────

def _demo() -> None:
    from collections import Counter

    class _E:
        def __init__(self, name, entity_type):
            self.name, self._t = name, entity_type
        def get_entity_type(self):
            return self._t

    assert abs(sum(MBTI_DISTRIBUTION.values()) - 100.0) < 1e-9

    rng = random.Random(0)
    n = 200_000
    drawn = Counter(sample_mbti(rng) for _ in range(n))
    for mbti, pct in MBTI_DISTRIBUTION.items():
        assert abs(drawn[mbti] / n * 100 - pct) < 0.4, (mbti, drawn[mbti] / n * 100)

    # Too many entities: truncated to the cap, every type still represented.
    many = ([_E(f"p{i}", "Person") for i in range(700)]
            + [_E(f"m{i}", "MediaOutlet") for i in range(40)]
            + [_E("BrandCo", "Company")])
    slots = plan_population(many, seed=1)
    assert len(slots) == MAX_AGENTS, len(slots)
    assert {s.entity_type for s in slots} == {"Person", "MediaOutlet", "Company"}
    assert all(not s.is_clone for s in slots)

    # A campaign-specific type name matches no fixed list, so the hints decide.
    tesla = ([_E("Gen Z / Students", "GenZstudent"),
              _E("college students", "GenZstudent"),
              _E("Evskeptic", "Evskeptic"),
              _E("Tesla", "Organization"),
              _E("TeslaMotors", "Organization")])
    branded = plan_population(tesla, seed=1)
    # Without hints the invented segment names are still cloned up to the cap -
    # defaulting them to a named brand collapsed the cast to the entity count.
    # "Organization" is the ontology's own generic org type, so it stays a
    # singleton: one account for Tesla, one for TeslaMotors.
    assert len(branded) == MAX_AGENTS, len(branded)
    assert len([s for s in branded if s.entity_type == "Organization"]) == 2
    kinds = {"GenZstudent": "general_individual", "Evskeptic": "general_individual",
             "Organization": "specific_company"}
    shaped = plan_population(tesla, kinds=kinds, seed=1)
    assert len(shaped) == MAX_AGENTS
    brand_slots = [s for s in shaped if s.entity_type == "Organization"]
    assert len(brand_slots) == 2, len(brand_slots)
    assert all(s.is_individual for s in shaped if s.kind == "general_individual")

    # The legacy three-way kind still classifies the same way.
    legacy = plan_population(tesla, kinds={"GenZstudent": "segment",
                                           "Evskeptic": "segment",
                                           "Organization": "institution"}, seed=1)
    assert [(s.entity.name, s.kind) for s in legacy] == [
        (s.entity.name, s.kind) for s in shaped]

    # The clone budget splits 10/90, and a firm is never given a person's
    # demographics.
    mixed = ([_E("Gen Z / Students", "GenZstudent"),
              _E("EV sceptics", "Evskeptic"),
              _E("car makers", "CarCompany"),
              _E("EV startups", "CarCompany"),
              _E("Tesla", "Organization")])
    mixed_kinds = {"GenZstudent": "general_individual",
                   "Evskeptic": "general_individual",
                   "CarCompany": "general_company",
                   "Organization": "specific_company"}
    cast = plan_population(mixed, kinds=mixed_kinds, seed=1)
    assert len(cast) == MAX_AGENTS, len(cast)
    clone_budget = MAX_AGENTS - 1                      # the one named brand
    expected_firms = round(clone_budget * 0.10)
    firm_slots = [s for s in cast if s.kind == "general_company"]
    assert len(firm_slots) == expected_firms, len(firm_slots)
    assert len([s for s in cast if s.kind == "general_individual"]) == (
        clone_budget - expected_firms)
    assert all(s.gender == "other" and s.age == INSTITUTIONAL_AGE for s in firm_slots)
    assert len([s for s in cast if s.entity_type == "Organization"]) == 1

    # More firm entities than their 10% share: one slot each, never zero.
    crowded = ([_E(f"firm{i}", "CarCompany") for i in range(80)]
               + [_E("Gen Z / Students", "GenZstudent")])
    crowded_cast = plan_population(crowded, kinds=mixed_kinds, seed=1)
    assert len([s for s in crowded_cast if s.kind == "general_company"]) == 80
    assert len(crowded_cast) == MAX_AGENTS

    # Only firms are cloneable: they take their 10% and the rest goes unspent
    # rather than filling the cast with copies of one generic firm.
    three_firms = [_E(f"firm{i}", "CarCompany") for i in range(3)]
    expected = round(MAX_AGENTS * 0.10)
    assert len(plan_population(three_firms, kinds=mixed_kinds, seed=1)) == expected
    assert planned_population_size(three_firms, MAX_AGENTS, mixed_kinds) == expected

    # A specific kind is always exactly one agent, however big the budget.
    named = [_E("Jensen Huang", "Ceo"), _E("Tesla", "Organization"),
             _E("Gen Z / Students", "GenZstudent")]
    named_kinds = {"Ceo": "specific_individual", "Organization": "specific_company",
                   "GenZstudent": "general_individual"}
    named_cast = plan_population(named, kinds=named_kinds, seed=1)
    assert len([s for s in named_cast if s.entity_type == "Ceo"]) == 1
    assert len([s for s in named_cast if s.entity_type == "Organization"]) == 1
    # A named person is still a person: real demographics, not the org defaults.
    ceo = next(s for s in named_cast if s.entity_type == "Ceo")
    assert ceo.is_individual and ceo.gender in ("male", "female")
    brand = next(s for s in named_cast if s.entity_type == "Organization")
    assert not brand.is_individual and brand.gender == "other"

    # Too few: cloned up to the cap, singletons never cloned.
    few = ([_E(f"p{i}", "Person") for i in range(12)]
           + [_E("BrandCo", "Company"), _E("TradePress", "MediaOutlet")])
    slots = plan_population(few, seed=1)
    assert len(slots) == MAX_AGENTS, len(slots)
    assert [s.user_id for s in slots] == list(range(MAX_AGENTS))
    singleton_slots = [s for s in slots if s.entity_type in {"Company", "MediaOutlet"}]
    assert len(singleton_slots) == 2 and all(not s.is_clone for s in singleton_slots)
    per_entity = Counter(id(s.entity) for s in slots)
    person_counts = {c for e, c in per_entity.items()
                     if any(s.entity_type == "Person" for s in slots if id(s.entity) == e)}
    assert max(person_counts) - min(person_counts) <= 1, person_counts   # evenly spread

    # Institutional slots are not given a person's demographics.
    assert all(s.gender == "other" and s.age == INSTITUTIONAL_AGE
               for s in slots if not s.is_individual)

    # Country follows the brief, not the model's imagination.
    assert all(s.country is None for s in plan_population(few, seed=1))
    one = plan_population(few, markets=["Vietnam"], seed=1)
    assert all(s.country == "Vietnam" for s in one)
    two = plan_population(few, markets=["Vietnam", "Thailand"], seed=1)
    assert {s.country for s in two} == {"Vietnam", "Thailand"}

    # Age ranges are honoured.
    aged = plan_population(few, age_ranges={"Person": (18, 24)}, seed=1)
    assert all(18 <= s.age <= 24 for s in aged if s.entity_type == "Person")

    # Same seed, same population.
    assert ([(s.entity.name, s.mbti, s.age) for s in plan_population(few, seed=7)]
            == [(s.entity.name, s.mbti, s.age) for s in plan_population(few, seed=7)])

    # Degenerate inputs.
    assert plan_population([], seed=1) == []
    assert len(plan_population(few, max_agents=3, seed=1)) == 3

    # Hints: a broken response degrades to defaults rather than failing the run.
    class _BadClient:
        def chat_json(self, **kwargs):
            raise RuntimeError("no LLM here")

    assert derive_population_hints(["Person"], "brief", _BadClient()) == ({}, [], {})

    class _Client:
        def chat_json(self, **kwargs):
            return {"age_ranges": {"Person": [18, 24], "Broken": ["x"], "Wide": [200, 4]},
                    "markets": ["Vietnam", "", 7],
                    "kinds": {"Person": "General_Individual", "Broken": "nonsense",
                              "Legacy": "institution"}}

    ranges, markets, kinds = derive_population_hints(
        ["Person", "Broken", "Wide"], "brief", _Client())
    assert ranges == {"Person": (18, 24), "Wide": (13, 95)}, ranges
    assert markets == ["Vietnam", "7"], markets
    assert kinds == {"Person": "general_individual",
                     "Legacy": "specific_company"}, kinds
    # An unclassified type falls back to the fixed lists first.
    assert entity_kind("Student", kinds) == "general_individual"
    assert entity_kind("Journalist", kinds) == "specific_individual"
    # The ontology's own generic org names stay singletons; anything else the
    # campaign invented is a class of people, so the cast still fills.
    assert entity_kind("Organization", kinds) == "specific_company"
    assert entity_kind("MediaOutlet", kinds) == "specific_company"
    assert entity_kind("Wide", kinds) == "general_individual"

    print("agent_population self-check passed")


if __name__ == "__main__":
    _demo()

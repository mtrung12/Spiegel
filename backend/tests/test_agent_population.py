"""
Population planning: the agent cap, the clone expansion, and what carries over.

The invariant worth pinning is that the cast is bounded and self-consistent -
exactly one profile and exactly one activity config per planned slot, with
agent_ids that line up. If those drift apart, the simulation posts as the wrong
agent and every number in the report is attributed to the wrong segment.

The LLM calls are stubbed; what is under test is the arithmetic and the
plumbing around them.
"""

import pytest

from app.config import Config
from app.services.agent_population import (
    INSTITUTIONAL_AGE,
    MAX_AGENTS,
    MBTI_DISTRIBUTION,
    entity_kind,
    plan_population,
    planned_population_size,
    sample_mbti,
)
from app.services.oasis_profile_generator import OasisAgentProfile, OasisProfileGenerator
from app.services.simulation_config_generator import (
    AgentActivityConfig,
    SimulationConfigGenerator,
)


class _Entity:
    """Stand-in for a Zep EntityNode."""

    def __init__(self, name, entity_type, uuid=None):
        self.name = name
        self.summary = f"{name} is a {entity_type}."
        self.attributes = {}
        self.uuid = uuid or f"uuid-{name}"
        self._type = entity_type

    def get_entity_type(self):
        return self._type


def _entities(persons=10, media=2):
    return (
        [_Entity(f"person{i}", "Person") for i in range(persons)]
        + [_Entity(f"media{i}", "MediaOutlet") for i in range(media)]
    )


def _generator(distribution=None):
    generator = OasisProfileGenerator.__new__(OasisProfileGenerator)
    generator.corpus_distribution = distribution
    generator.graph_id = None
    generator.zep_client = None
    return generator


def _stub_persona(generator, persona="A shopper who buys on price."):
    def generate_profile_from_entity(entity, user_id, use_llm=True, theme=None):
        return OasisAgentProfile(
            user_id=user_id,
            user_name=f"{entity.name}_000",
            name=entity.name,
            bio=f"bio of {entity.name}",
            persona=persona,
            age=99, gender="male", mbti="XXXX", country="Narnia",  # must be overwritten
            source_entity_uuid=entity.uuid,
            source_entity_type=entity.get_entity_type(),
        )

    generator.generate_profile_from_entity = generate_profile_from_entity
    generator._print_generated_profile = lambda *a, **k: None
    return generator


# -- the cap ----------------------------------------------------------------

def test_more_entities_than_the_cap_are_truncated():
    slots = plan_population(_entities(persons=900, media=40), seed=1)

    assert len(slots) == MAX_AGENTS
    assert all(not s.is_clone for s in slots)
    # Truncation must not wipe out a whole segment type.
    assert {s.entity_type for s in slots} == {"Person", "MediaOutlet"}


def test_fewer_entities_are_cloned_up_to_the_cap():
    slots = plan_population(_entities(persons=10, media=2), seed=1)

    assert len(slots) == MAX_AGENTS
    assert [s.user_id for s in slots] == list(range(MAX_AGENTS))
    # Singleton types are never cloned; only the mass-audience type is.
    media = [s for s in slots if s.entity_type == "MediaOutlet"]
    assert len(media) == 2 and all(not s.is_clone for s in media)


def test_an_explicit_cap_is_respected_in_both_directions():
    assert len(plan_population(_entities(persons=100), max_agents=25, seed=1)) == 25
    assert len(plan_population(_entities(persons=4, media=1), max_agents=25, seed=1)) == 25


@pytest.mark.parametrize("entities,cap", [
    (_entities(persons=900, media=40), MAX_AGENTS),   # truncated
    (_entities(persons=10, media=2), MAX_AGENTS),     # cloned
    (_entities(persons=10, media=2), 60),             # cloned to a custom cap
    (_entities(persons=0, media=5), 200),             # nothing cloneable
    ([], 200),
])
def test_the_size_preview_matches_what_planning_produces(entities, cap):
    # The prepare response quotes this before any planning runs, so a mismatch
    # shows the user a cast size they will not get.
    assert planned_population_size(entities, cap) == len(
        plan_population(entities, max_agents=cap, seed=1)
    )


# -- the four kinds ---------------------------------------------------------

_KINDS = {
    "Ceo": "specific_individual",
    "Tesla": "specific_company",
    "GenZstudent": "general_individual",
    "CarCompany": "general_company",
}


def _one_of_each(firms=2, people=2):
    return (
        [_Entity("Jensen Huang", "Ceo"), _Entity("Tesla", "Tesla")]
        + [_Entity(f"firm{i}", "CarCompany") for i in range(firms)]
        + [_Entity(f"student{i}", "GenZstudent") for i in range(people)]
    )


def test_a_specific_kind_is_always_exactly_one_agent():
    # There is one Jensen Huang and one Tesla, whatever the budget says.
    slots = plan_population(_one_of_each(), max_agents=MAX_AGENTS,
                            kinds=_KINDS, seed=1)

    assert len(slots) == MAX_AGENTS
    assert len([s for s in slots if s.entity_type == "Ceo"]) == 1
    assert len([s for s in slots if s.entity_type == "Tesla"]) == 1


def test_a_named_person_still_gets_a_persons_demographics():
    # specific vs general decides cloning; individual vs company decides this.
    slots = plan_population(_one_of_each(), max_agents=50, kinds=_KINDS, seed=1)

    ceo = next(s for s in slots if s.entity_type == "Ceo")
    brand = next(s for s in slots if s.entity_type == "Tesla")
    assert ceo.is_individual and ceo.gender in ("male", "female")
    assert not brand.is_individual
    assert (brand.gender, brand.age) == ("other", INSTITUTIONAL_AGE)


def test_the_clone_budget_splits_ten_ninety():
    # The split is taken over what is left once the named entities have each
    # taken their one agent, not over the whole cap.
    slots = plan_population(_one_of_each(firms=2, people=2), max_agents=MAX_AGENTS,
                            kinds=_KINDS, seed=1)

    clone_budget = MAX_AGENTS - 2                     # Jensen Huang and Tesla
    expected_firms = round(clone_budget * Config.GENERAL_COMPANY_SHARE)
    firms = [s for s in slots if s.kind == "general_company"]

    assert len(firms) == expected_firms
    assert len([s for s in slots if s.kind == "general_individual"]) == (
        clone_budget - expected_firms
    )
    # A firm has no age, gender or MBTI to vary, so it keeps the org defaults.
    assert all(s.gender == "other" and s.age == INSTITUTIONAL_AGE for s in firms)


def test_more_firms_than_their_share_still_get_one_slot_each():
    # The 10% is a share, not a cap: a graph that turned up 80 firm entities
    # still represents every one of them rather than dropping 30.
    entities = ([_Entity(f"firm{i}", "CarCompany") for i in range(80)]
                + [_Entity("students", "GenZstudent")])
    slots = plan_population(entities, max_agents=MAX_AGENTS, kinds=_KINDS, seed=1)

    firms = [s for s in slots if s.kind == "general_company"]
    assert len(firms) == 80 and all(not s.is_clone for s in firms)
    assert len(slots) == MAX_AGENTS


def test_firms_alone_leave_the_rest_of_the_budget_unspent():
    # Nothing to spend the other 90% on: a small cast beats 450 copies of one
    # generic firm.
    entities = [_Entity(f"firm{i}", "CarCompany") for i in range(3)]
    slots = plan_population(entities, max_agents=MAX_AGENTS, kinds=_KINDS, seed=1)

    expected = round(MAX_AGENTS * Config.GENERAL_COMPANY_SHARE)
    assert len(slots) == expected
    assert planned_population_size(entities, MAX_AGENTS, _KINDS) == expected


def test_the_company_share_is_a_knob(monkeypatch):
    # Read at call time so a B2B or trade-press-heavy category can be retuned
    # without a code change.
    entities = _one_of_each(firms=2, people=2)
    monkeypatch.setattr(Config, "GENERAL_COMPANY_SHARE", 0.40)
    slots = plan_population(entities, max_agents=MAX_AGENTS, kinds=_KINDS, seed=1)

    assert len([s for s in slots if s.kind == "general_company"]) == round(498 * 0.40)
    assert len(slots) == MAX_AGENTS


def test_the_legacy_three_way_kind_still_classifies():
    # Ontologies saved before the split carry segment/individual/institution.
    legacy = {"GenZstudent": "segment", "Ceo": "individual", "Tesla": "institution"}
    assert entity_kind("GenZstudent", legacy) == "general_individual"
    assert entity_kind("Ceo", legacy) == "specific_individual"
    assert entity_kind("Tesla", legacy) == "specific_company"


@pytest.mark.parametrize("entities,cap", [
    (_one_of_each(), MAX_AGENTS),
    (_one_of_each(firms=12, people=0), MAX_AGENTS),
    ([_Entity(f"firm{i}", "CarCompany") for i in range(3)], MAX_AGENTS),
    (_one_of_each(), 6),
])
def test_the_size_preview_matches_planning_for_the_four_kinds(entities, cap):
    assert planned_population_size(entities, cap, _KINDS) == len(
        plan_population(entities, max_agents=cap, kinds=_KINDS, seed=1)
    )


def test_mbti_follows_the_published_distribution():
    import random
    from collections import Counter

    rng = random.Random(0)
    draws = Counter(sample_mbti(rng) for _ in range(100_000))
    for mbti, pct in MBTI_DISTRIBUTION.items():
        assert abs(draws[mbti] / 1000 - pct) < 0.6, mbti


# -- profiles ---------------------------------------------------------------

def test_one_profile_per_slot_with_the_sampled_attributes():
    entities = _entities(persons=6, media=1)
    slots = plan_population(entities, max_agents=40, markets=["Vietnam"],
                            age_ranges={"Person": (25, 34)}, seed=3)
    profiles = _stub_persona(_generator()).generate_profiles_from_slots(
        slots=slots, use_llm=False, parallel_count=4,
    )

    assert len(profiles) == len(slots) == 40
    assert all(p is not None for p in profiles)
    assert [p.user_id for p in profiles] == list(range(40))
    # Uniqueness is what keeps two clones from colliding at platform sign-up.
    assert len({p.user_name for p in profiles}) == 40

    by_id = {p.user_id: p for p in profiles}
    for slot in slots:
        profile = by_id[slot.user_id]
        assert (profile.age, profile.gender, profile.mbti, profile.country) == (
            slot.age, slot.gender, slot.mbti, slot.country
        )
    assert all(25 <= p.age <= 34 for p in profiles if p.source_entity_type == "Person")


def test_no_country_is_asserted_when_the_brief_names_no_market():
    slots = plan_population(_entities(persons=3), max_agents=10, seed=3)
    profiles = _stub_persona(_generator()).generate_profiles_from_slots(
        slots=slots, use_llm=False, parallel_count=2,
    )

    assert all(p.country is None for p in profiles)
    assert all(item["country"] is None for item in
               [p.to_reddit_format() for p in profiles] if "country" in item)


def test_clones_share_a_persona_but_carry_their_own_prior():
    distribution = {'themes': [
        {'theme': 'price too high', 'share_pct': 50.0,
         'dominant_sentiment': 'negative', 'examples': []},
        {'theme': 'saves weeknight time', 'share_pct': 50.0,
         'dominant_sentiment': 'positive', 'examples': []},
    ]}
    slots = plan_population([_Entity("shopper", "Person")], max_agents=10, seed=3)
    profiles = _stub_persona(_generator(distribution)).generate_profiles_from_slots(
        slots=slots, use_llm=False, parallel_count=2,
    )

    assert len(profiles) == 10
    # One entity, one persona call - but the priors differ, so the clones do
    # not walk into the campaign already agreeing with each other.
    assert all("A shopper who buys on price." in p.persona for p in profiles)
    assert {'price too high' in p.persona for p in profiles} == {True, False}


def test_cloned_firms_get_their_own_identity_from_one_variant_call():
    # A firm has no age, gender or MBTI to vary, so without this every clone of
    # "car companies" is the same account posting twenty times.
    calls = []

    def fake_variants(entity_name, entity_type, base_persona, count):
        calls.append((entity_name, count))
        return [{"name": f"Firm{i}", "position": "regional challenger",
                 "angle": "undercuts on price"} for i in range(count)]

    generator = _stub_persona(_generator(), persona="A carmaker in this category.")
    generator._generate_company_variants = fake_variants
    generator._entity_kind = lambda entity_type: "general_company"
    generator._generate_username = lambda name: name.lower()

    # One firm entity takes 10% of the budget, so 80 buys it 8 slots.
    slots = plan_population([_Entity("car companies", "CarCompany")],
                            max_agents=80, kinds=_KINDS, seed=3)
    assert len(slots) == 8
    profiles = generator.generate_profiles_from_slots(
        slots=slots, use_llm=True, parallel_count=2,
    )

    # One call for the whole class, not one per firm.
    assert calls == [("car companies", len(slots))]
    assert len({p.name for p in profiles}) == len(slots)
    assert len({p.user_name for p in profiles}) == len(slots)
    # The class archetype is still underneath every one of them.
    assert all("A carmaker in this category." in p.persona for p in profiles)
    assert all("undercuts on price" in p.persona for p in profiles)


def test_a_failed_variant_call_falls_back_to_the_shared_archetype():
    generator = _stub_persona(_generator(), persona="A carmaker in this category.")
    generator._generate_company_variants = lambda *a, **k: []
    generator._entity_kind = lambda entity_type: "general_company"

    slots = plan_population([_Entity("car companies", "CarCompany")],
                            max_agents=50, kinds=_KINDS, seed=3)
    profiles = generator.generate_profiles_from_slots(
        slots=slots, use_llm=True, parallel_count=2,
    )

    assert len(profiles) == len(slots) == 5
    assert all(p.name == "car companies" for p in profiles)
    # user_name still has to be unique or the clones collide at sign-up.
    assert len({p.user_name for p in profiles}) == len(slots)


def test_a_short_variant_response_is_cycled_to_cover_every_slot():
    generator = _generator()

    class _Response:
        class _Choice:
            def __init__(self):
                self.message = type("M", (), {"content": '{"v":[["A","big","x"],["B","small","y"]]}'})()
                self.finish_reason = "stop"
        choices = [_Choice()]
        usage = None

    generator.client = None
    generator.model_name = "test-model"
    import app.services.oasis_profile_generator as mod
    original = mod.create_chat_completion
    mod.create_chat_completion = lambda *a, **k: _Response()
    try:
        variants = generator._generate_company_variants("car companies", "CarCompany",
                                                        "archetype", 5)
    finally:
        mod.create_chat_completion = original

    # Two rows returned, five slots to fill: cycled, never short.
    assert len(variants) == 5
    assert [v["name"] for v in variants] == ["A", "B", "A", "B", "A"]
    assert variants[0] == {"name": "A", "position": "big", "angle": "x"}


# -- activity configs -------------------------------------------------------

def _config(entity, agent_id=0, **kwargs):
    return AgentActivityConfig(
        agent_id=agent_id,
        entity_uuid=entity.uuid,
        entity_name=entity.name,
        entity_type=entity.get_entity_type(),
        **kwargs,
    )


def test_every_slot_gets_a_config_with_a_matching_agent_id():
    entities = _entities(persons=5, media=1)
    slots = plan_population(entities, max_agents=30, seed=5)
    generator = SimulationConfigGenerator.__new__(SimulationConfigGenerator)

    configs = generator._expand_configs_to_slots(
        [_config(e, agent_id=i) for i, e in enumerate(entities)], slots
    )

    assert len(configs) == len(slots) == 30
    assert sorted(c.agent_id for c in configs) == list(range(30))
    assert all(c.entity_uuid for c in configs)


def test_clones_are_jittered_but_the_original_is_untouched():
    entity = _Entity("shopper", "Person")
    slots = plan_population([entity], max_agents=20, seed=5)
    base = _config(entity, activity_level=0.5, sentiment_bias=0.0,
                   posts_per_hour=2.0, stance="neutral")
    generator = SimulationConfigGenerator.__new__(SimulationConfigGenerator)

    configs = generator._expand_configs_to_slots([base], slots)
    by_id = {c.agent_id: c for c in configs}
    original = next(by_id[s.user_id] for s in slots if not s.is_clone)
    clones = [by_id[s.user_id] for s in slots if s.is_clone]

    assert (original.activity_level, original.sentiment_bias) == (0.5, 0.0)
    assert len({c.sentiment_bias for c in clones}) > 1
    assert all(0.05 <= c.activity_level <= 1.0 for c in clones)
    assert all(-1.0 <= c.sentiment_bias <= 1.0 for c in clones)
    assert all(c.response_delay_max > c.response_delay_min for c in clones)
    # The source config must not be mutated by the expansion.
    assert base.agent_id == 0 and base.activity_level == 0.5


def test_a_slot_whose_entity_config_failed_still_gets_an_agent():
    entities = _entities(persons=3, media=0)
    slots = plan_population(entities, max_agents=12, seed=5)
    generator = SimulationConfigGenerator.__new__(SimulationConfigGenerator)

    # Only one of the three entities produced a config (a failed LLM batch).
    configs = generator._expand_configs_to_slots([_config(entities[0])], slots)

    assert len(configs) == 12
    assert sorted(c.agent_id for c in configs) == list(range(12))


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

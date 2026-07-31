"""Helpers for validating LLM-generated ontology structures."""

from typing import Any, Dict, List, Optional, Tuple, Type

from pydantic import BaseModel, Field, create_model


MAX_ONTOLOGY_TYPES = 10
MAX_ONTOLOGY_ATTRIBUTES = 10
# What one entity type stands for, as two independent facts crossed together:
#
#   specific / general  - is this ONE named actor, or a class of them?
#                         Decides whether the type is simulated once or cloned.
#   individual / company - a natural person, or an organisation?
#                         Decides whether it gets an age, a gender and an MBTI,
#                         and which persona prompt it is written with.
#
# Written by the ontology generator, consumed by the population planner.
ENTITY_KINDS = (
    "specific_individual",   # Jensen Huang
    "specific_company",      # Tesla
    "general_individual",    # Gen Z students, EV sceptics, CEOs in general
    "general_company",       # car companies, EV startups
)

# The three-way kind this replaced. Ontologies saved before the split still
# carry these, and so does any hints response from a cached prompt.
LEGACY_ENTITY_KINDS = {
    "segment": "general_individual",
    "individual": "specific_individual",
    "institution": "specific_company",
}
MAX_ONTOLOGY_SOURCE_TARGETS = 10
# Graphiti rejects a custom attribute that shadows a field of its own
# EntityNode (see graphiti_core.utils.ontology_utils.entity_types_utils), so
# these are renamed rather than passed through. "graph_id" is not one of
# Graphiti's, but Spiegel's own graph identifier, kept reserved for the same
# reason it always was.
RESERVED_ONTOLOGY_ATTRIBUTE_NAMES = frozenset({
    "uuid",
    "name",
    "group_id",
    "graph_id",
    "labels",
    "attributes",
    "name_embedding",
    "summary",
    "created_at",
})

_FALLBACK_ATTRIBUTE = {
    "name": "details",
    "type": "text",
    "description": "Additional details about this ontology type.",
}


def normalize_entity_kind(kind: Any) -> Optional[str]:
    """
    Return a canonical kind, or ``None`` when the value is unusable.

    Accepts the legacy three-way names so an ontology saved before the split
    keeps working without a migration pass over stored projects.
    """
    key = str(kind or "").strip().lower()
    key = LEGACY_ENTITY_KINDS.get(key, key)
    return key if key in ENTITY_KINDS else None


def is_cloneable_kind(kind: str) -> bool:
    """A general kind stands for a class of actors, so N copies are meaningful."""
    return str(kind).startswith("general")


def is_person_kind(kind: str) -> bool:
    """A natural person gets an age, a gender, an MBTI and a person's persona."""
    return str(kind).endswith("individual")


def normalize_ontology_attribute(attribute: Any) -> Optional[Dict[str, Any]]:
    """Return a safe attribute definition, or ``None`` for unusable values."""

    if isinstance(attribute, str):
        if not attribute.strip():
            return None
        return {
            "name": attribute,
            "type": "text",
            "description": attribute,
        }

    if not isinstance(attribute, dict):
        return None

    name = attribute.get("name")
    if not isinstance(name, str) or not name.strip():
        return None

    normalized = dict(attribute)
    description = normalized.get("description")
    if not isinstance(description, str) or not description:
        normalized["description"] = name
    return normalized


def normalize_ontology_attributes(attributes: Any) -> List[Dict[str, Any]]:
    """Return a non-empty attribute list within the per-type limit."""

    if not isinstance(attributes, list):
        attributes = []

    normalized_attributes: List[Dict[str, Any]] = []
    for attribute in attributes:
        normalized = normalize_ontology_attribute(attribute)
        if normalized is None:
            continue
        normalized_attributes.append(normalized)
        if len(normalized_attributes) == MAX_ONTOLOGY_ATTRIBUTES:
            break

    if not normalized_attributes:
        normalized_attributes.append(dict(_FALLBACK_ATTRIBUTE))

    return normalized_attributes


def normalize_ontology_source_targets(
    source_targets: Any,
    *,
    limit: int | None = MAX_ONTOLOGY_SOURCE_TARGETS,
) -> List[Dict[str, str]]:
    """Return unique, structurally valid source-target pairs within limits."""

    if not isinstance(source_targets, list):
        return []

    normalized_targets: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source_target in source_targets:
        if not isinstance(source_target, dict):
            continue
        source = source_target.get("source")
        target = source_target.get("target")
        if not isinstance(source, str) or not source.strip():
            continue
        if not isinstance(target, str) or not target.strip():
            continue

        pair = (source.strip(), target.strip())
        if pair in seen:
            continue
        seen.add(pair)
        normalized_targets.append({"source": pair[0], "target": pair[1]})
        if limit is not None and len(normalized_targets) == limit:
            break

    return normalized_targets


# Graphiti's stock extraction prompt is tuned for personal-assistant memory:
# it says "NEVER extract abstract concepts", bans generic nouns like "people",
# and applies a "could this have its own Wikipedia article?" test before keeping
# anything.
#
# That is the opposite of what Spiegel needs. Half of every ontology it
# generates is audience *segments* - "city commuters", "Gen Z students", "EV
# sceptics" - which is what ENTITY_KINDS calls the `general_*` kinds, and what
# agent_population clones into the simulated cast. Under the stock prompt those
# are all discarded, the extraction keeps only named brands and people, and the
# simulation runs with an audience of nobody. Measured on a two-sentence sample:
# stock prompt gave 1 node and 0 edges, because both edges lost an endpoint.
#
# So this re-admits one specific class - a segment named by an ontology entity
# type - and deliberately leaves every other exclusion in the stock prompt
# alone, since those are what keep "things", "supplies" and "day" out.
SEGMENT_EXTRACTION_INSTRUCTIONS = """
IMPORTANT - domain override for this task:
This text is marketing material being analysed for audience response, so a
group or segment of people IS a valid, extractable entity whenever it matches
one of the ENTITY TYPES above. Extract it even though it is not a named
individual and would not have its own Wikipedia article.

- Extract audience segments and market segments as entities: "city commuters",
  "Gen Z students", "small business owners", "EV sceptics", "parents of
  toddlers".
- Extract categories of organisation the same way when an entity type covers
  them: "car manufacturers", "regional banks".
- Use the most specific wording the text itself uses for the segment, and keep
  it consistent so the same segment mentioned twice resolves to one entity.
- This override applies ONLY to groups matching an ENTITY TYPE. Every other
  exclusion above still holds: no bare generic nouns ("people", "customers"
  with no qualifier), no feelings, no actions, no dates.
"""


def safe_attribute_name(attribute_name: str) -> str:
    """Rewrite an attribute whose name Graphiti reserves for its own fields."""

    if attribute_name.lower() in RESERVED_ONTOLOGY_ATTRIBUTE_NAMES:
        return f"entity_{attribute_name}"
    return attribute_name


def _build_type_model(
    name: str,
    description: str,
    attributes: Any,
) -> Type[BaseModel]:
    """Turn one ontology type definition into a Graphiti extraction model.

    Every attribute is an optional string. Graphiti sends the model's JSON
    schema to the LLM and asks it to fill what the text supports, so a required
    field would force the model to invent a value for anything the source does
    not mention.
    """

    fields: Dict[str, Any] = {
        safe_attribute_name(attribute["name"]): (
            Optional[str],
            Field(default=None, description=attribute["description"]),
        )
        for attribute in normalize_ontology_attributes(attributes)
    }
    model = create_model(name, **fields)
    model.__doc__ = description
    return model


def build_graphiti_ontology(
    ontology: Optional[Dict[str, Any]],
) -> Tuple[
    Dict[str, Type[BaseModel]],
    Dict[str, Type[BaseModel]],
    Dict[Tuple[str, str], List[str]],
]:
    """Compile a stored ontology into the three arguments Graphiti extracts with.

    Returns ``(entity_types, edge_types, edge_type_map)``. The docstring of each
    generated model is the type description, which is what the extraction prompt
    actually reads - so a vague description in the stored ontology shows up as a
    vague graph, not as an error here.

    This replaces Zep's ``graph.set_ontology``, which installed the same
    definitions server-side once per graph. Graphiti takes them per ingestion
    call instead, which is why the stored project ontology is the source of
    truth and every writer recompiles it rather than trusting remote state.
    """

    if not isinstance(ontology, dict):
        return {}, {}, {}

    entity_types: Dict[str, Type[BaseModel]] = {}
    for entity_def in (ontology.get("entity_types") or [])[:MAX_ONTOLOGY_TYPES]:
        if not isinstance(entity_def, dict) or not entity_def.get("name"):
            continue
        name = entity_def["name"]
        entity_types[name] = _build_type_model(
            name,
            entity_def.get("description") or f"A {name} entity.",
            entity_def.get("attributes"),
        )

    edge_types: Dict[str, Type[BaseModel]] = {}
    edge_type_map: Dict[Tuple[str, str], List[str]] = {}
    for edge_def in (ontology.get("edge_types") or [])[:MAX_ONTOLOGY_TYPES]:
        if not isinstance(edge_def, dict) or not edge_def.get("name"):
            continue
        name = edge_def["name"]
        source_targets = normalize_ontology_source_targets(
            edge_def.get("source_targets")
        )
        # Zep dropped an edge type with no source/target pair, because it had
        # nowhere to attach it. Keep that rule: without a pair the type would be
        # defined but never offered to the extractor for any node combination.
        if not source_targets:
            continue

        edge_types[name] = _build_type_model(
            name,
            edge_def.get("description") or f"A {name} relationship.",
            edge_def.get("attributes"),
        )
        for source_target in source_targets:
            key = (source_target["source"], source_target["target"])
            edge_type_map.setdefault(key, []).append(name)

    return entity_types, edge_types, edge_type_map

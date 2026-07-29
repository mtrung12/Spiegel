"""Helpers for validating LLM-generated ontology structures."""

from typing import Any, Dict, List, Optional


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
RESERVED_ONTOLOGY_ATTRIBUTE_NAMES = frozenset({
    "uuid",
    "name",
    "group_id",
    "graph_id",
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
    """Return a non-empty Zep-compatible attribute list within service limits."""

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
    """Return unique, structurally valid source-target pairs within Zep limits."""

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

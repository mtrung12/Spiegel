"""
Ontology generation service.
Endpoint 1: analyse the text content and derive the entity and relationship
type definitions needed for a social simulation.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional
from ..utils.llm_client import LLMClient
from ..utils.locale import get_language_instruction
from ..utils.pipeline_logger import llm_caller, pipeline_log
from ..utils.file_parser import split_text_into_chunks
from ..utils.ontology import (
    MAX_ONTOLOGY_TYPES,
    normalize_entity_kind,
    normalize_ontology_attributes,
    normalize_ontology_source_targets,
)

logger = logging.getLogger(__name__)


def _to_pascal_case(name: str) -> str:
    """Convert a name in any format to PascalCase (e.g. 'works_for' -> 'WorksFor', 'person' -> 'Person')."""
    # Split on non-alphanumeric characters
    parts = re.split(r'[^a-zA-Z0-9]+', name)
    # Then split on camelCase boundaries (e.g. 'camelCase' -> ['camel', 'Case'])
    words = []
    for part in parts:
        words.extend(re.sub(r'([a-z])([A-Z])', r'\1_\2', part).split('_'))
    # Capitalize each word, dropping empty fragments
    result = ''.join(word.capitalize() for word in words if word)
    return result if result else 'Unknown'


def _to_upper_snake_case(name: str) -> str:
    """Convert free-form or camelCase names to SCREAMING_SNAKE_CASE."""

    separated = re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', name.strip())
    normalized = re.sub(r'[^a-zA-Z0-9]+', '_', separated).strip('_').upper()
    if not normalized:
        return "UNKNOWN"
    if normalized[0].isdigit():
        normalized = f"REL_{normalized}"
    return normalized


# System prompt used for ontology generation
ONTOLOGY_SYSTEM_PROMPT = """You are an expert knowledge-graph ontology designer. Your task is to analyse the given text and simulation requirement, then design the entity types and relationship types needed for a **social media opinion simulation**.

**IMPORTANT: you must output valid JSON and nothing else.**

## Task background

We are building a **social media opinion simulation system**. In that system:
- Every entity is an "account" or actor that can post, interact, and spread information on social media
- Entities influence, repost, comment on, and respond to each other
- We need to simulate how each side reacts during an opinion event and how information propagates

Therefore, **an entity must be a real-world actor that can actually speak and interact on social media**:

**Allowed**:
- Specific individuals (public figures, people involved, opinion leaders, experts and academics, ordinary people)
- Companies and businesses (including their official accounts)
- Institutions and organisations (universities, associations, NGOs, unions, ...)
- Government departments and regulators
- Media organisations (newspapers, TV stations, independent media, websites)
- Social media platforms themselves
- Representatives of a specific group (alumni associations, fan clubs, advocacy groups, ...)

**Not allowed**:
- Abstract concepts ("public opinion", "sentiment", "trend")
- Themes or topics ("academic integrity", "education reform")
- Opinions or stances ("the supporters", "the opposition")

## Output format

Output JSON with the following structure:

```json
{
    "entity_types": [
        {
            "name": "Entity type name (English, PascalCase)",
            "kind": "specific_individual | specific_company | general_individual | general_company",
            "description": "Short description (English, at most 100 characters)",
            "attributes": [
                {
                    "name": "attribute name (English, snake_case)",
                    "type": "text",
                    "description": "Attribute description"
                }
            ],
            "examples": ["example entity 1", "example entity 2"]
        }
    ],
    "edge_types": [
        {
            "name": "Relationship type name (English, UPPER_SNAKE_CASE)",
            "description": "Short description (English, at most 100 characters)",
            "source_targets": [
                {"source": "source entity type", "target": "target entity type"}
            ],
            "attributes": []
        }
    ],
    "analysis_summary": "A brief analysis of the text"
}
```

## Design guidelines (critically important)

### 1. Entity type design - must be followed strictly

**Count: exactly 10 entity types**

**Hierarchy: you must include both specific types and fallback types**

Your 10 entity types must cover the following layers:

A. **Fallback types (required, placed last in the list)**:
   - `Person`: fallback for any natural person. Use it whenever someone does not fit a more specific person type.
   - `Organization`: fallback for any organisation. Use it whenever an organisation does not fit a more specific organisation type.

B. **Specific types (8, designed from the text)**:
   - Design more specific types for the main roles appearing in the text
   - For example, an academic incident might warrant `Student`, `Professor`, `University`
   - For example, a business incident might warrant `Company`, `CEO`, `Employee`

**Why fallback types are needed**:
- Text mentions all kinds of people, e.g. "a school teacher", "a bystander", "some netizen"
- With no dedicated type to match, they belong under `Person`
- Likewise, small organisations and ad-hoc groups belong under `Organization`

**Principles for specific types**:
- Identify the frequent or pivotal role types in the text
- Each specific type must have clear boundaries and must not overlap another
- The description must state clearly how the type differs from the fallback type

C. **Kind (required on every entity type)**:

Every entity type must carry a `kind`. It answers two independent questions:

1. **specific or general** - is this ONE named actor that exists once in the
   world, or a class of actors the campaign meets many of?
2. **individual or company** - a natural person, or an organisation?

Cross them and pick exactly one:

- `specific_individual`: ONE named natural person - a named CEO, a named
  journalist, a named creator. There is only one of them, so we simulate one.
- `specific_company`: ONE named organisation, brand, media outlet, agency,
  university or platform - the advertiser itself included. It posts from a
  single official account, so we simulate one.
- `general_individual`: a class of ordinary people. The campaign reaches
  thousands of them and no single one is named. Students, brand loyalists,
  sceptics, commuters, first-time buyers, parents, "CEOs" as a group. We
  simulate many independent members, each with their own age, gender and
  personality.
- `general_company`: a class of organisations rather than one named firm - car
  companies, EV startups, regional dealerships, trade press in general. We
  simulate a handful of representative ones.

Judge what the type stands for in this campaign, not what its name resembles.
`Person` is `general_individual` (anonymous members of the public);
`Organization` is `specific_company` (it collects named orgs that had no type
of their own).

This field decides two things downstream: whether the type gets a person's age,
gender and MBTI, and whether it is simulated once or hundreds of times. A named
brand marked `general_*` would put hundreds of copies of one company in the
room; a real mass audience marked `specific_*` would be represented by a single
agent.

### 2. Relationship type design

- Count: 6-10
- Relationships should reflect genuine connections in social media interaction
- Make sure source_targets covers the entity types you defined

### 3. Attribute design

- 1-3 key attributes per entity type
- **Note**: attribute names must not be `name`, `uuid`, `group_id`, `graph_id`, `created_at`, or `summary` (these are reserved by the system)
- Prefer names such as `full_name`, `title`, `role`, `position`, `location`, `description`

## Entity type reference

**People (specific)**:
- Student
- Professor: professor or academic
- Journalist
- Celebrity: celebrity or influencer
- Executive: senior manager
- Official: government official
- Lawyer
- Doctor

**People (fallback)**:
- Person: any natural person (use when none of the specific types above fit)

**Organisations (specific)**:
- University
- Company: company or business
- GovernmentAgency
- MediaOutlet
- Hospital
- School: primary or secondary school
- NGO: non-governmental organisation

**Organisations (fallback)**:
- Organization: any organisation (use when none of the specific types above fit)

## Relationship type reference

- WORKS_FOR
- STUDIES_AT
- AFFILIATED_WITH
- REPRESENTS
- REGULATES
- REPORTS_ON
- COMMENTS_ON
- RESPONDS_TO
- SUPPORTS
- OPPOSES
- COLLABORATES_WITH
- COMPETES_WITH
"""


class OntologyGenerator:
    """
    Ontology generator.
    Analyses text content and derives entity and relationship type definitions.
    """
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client or LLMClient()
    
    def generate(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Generate the ontology definition.

        Args:
            document_texts: Document texts
            simulation_requirement: Description of the simulation requirement
            additional_context: Extra context

        Returns:
            The ontology definition (entity_types, edge_types, ...)
        """
        with pipeline_log.step(
            'OntologyGenerator', 'generate_ontology',
            documents=len(document_texts),
            document_chars=sum(len(text or '') for text in document_texts),
            has_additional_context=bool(additional_context),
        ) as step:
            return self._generate(
                step,
                document_texts,
                simulation_requirement,
                additional_context,
            )

    def _generate(
        self,
        step,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str] = None
    ) -> Dict[str, Any]:
        """Generate the ontology, recording each phase on the pipeline step."""
        # Build the user message
        user_message = self._build_user_message(
            document_texts,
            simulation_requirement,
            additional_context
        )
        step.input_text(user_message)
        step.note('user message built', chars=len(user_message))

        lang_instruction = get_language_instruction()
        system_prompt = f"{ONTOLOGY_SYSTEM_PROMPT}\n\n{lang_instruction}\nIMPORTANT: Entity type names MUST be in English PascalCase (e.g., 'PersonEntity', 'MediaOrganization'). Relationship type names MUST be in English UPPER_SNAKE_CASE (e.g., 'WORKS_FOR'). Attribute names MUST be in English snake_case. Only description fields and analysis_summary should use the specified language above."
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ]
        
        # Call the LLM
        with llm_caller('OntologyGenerator'):
            result = self.llm_client.chat_json(
                messages=messages,
                temperature=0.3,
                # Structured ontology responses can exceed 4096 completion tokens,
                # especially when a compatible provider counts hidden reasoning in
                # the same budget. Let the provider use its model-specific limit.
                max_tokens=None,
                max_attempts=2,
            )

        step.note(
            'raw ontology received',
            entity_types=len(result.get('entity_types') or []),
            edge_types=len(result.get('edge_types') or []),
        )

        # Validate and post-process
        result = self._validate_and_process(result)

        step.output(
            entity_type_names=[t.get('name') for t in result.get('entity_types') or []],
            edge_type_names=[t.get('name') for t in result.get('edge_types') or []],
        )
        step.metric(
            entity_types=len(result.get('entity_types') or []),
            edge_types=len(result.get('edge_types') or []),
        )

        return result
    
    # Maximum text length handed to the LLM (50k characters)
    MAX_TEXT_LENGTH_FOR_LLM = 50000
    LONG_TEXT_CHUNK_SIZE = 8000
    LONG_TEXT_CHUNK_OVERLAP = 200
    MAX_LONG_TEXT_CHUNKS = 60
    MIN_LONG_TEXT_EXCERPT = 400
    
    def _build_user_message(
        self,
        document_texts: List[str],
        simulation_requirement: str,
        additional_context: Optional[str]
    ) -> str:
        """Build the user message."""
        
        combined_text = self._build_document_context(document_texts)
        
        message = f"""## Simulation requirement

{simulation_requirement}

## Document content

{combined_text}
"""
        
        if additional_context:
            message += f"""
## Additional notes

{additional_context}
"""
        
        message += """
Using the content above, design the entity types and relationship types for a social opinion simulation.

**Rules you must follow**:
1. Output exactly 10 entity types
2. The last 2 must be the fallback types: Person (person fallback) and Organization (organisation fallback)
3. The first 8 are specific types designed from the text content
4. Every entity type must be a real-world actor that can speak publicly, never an abstract concept
5. Attribute names must not use reserved words such as name, uuid, group_id or graph_id; use full_name, org_name and similar instead
6. Every entity type must carry a "kind" of specific_individual, specific_company, general_individual or general_company
"""
        
        return message

    def _build_document_context(self, document_texts: List[str]) -> str:
        """Build the document context for ontology analysis. Long text is sampled
        across the whole document rather than truncated to its opening."""

        combined_text = "\n\n---\n\n".join(document_texts)
        original_length = len(combined_text)

        if original_length <= self.MAX_TEXT_LENGTH_FOR_LLM:
            return combined_text

        chunks = self._collect_document_chunks(document_texts)
        if not chunks:
            return ""

        selected_chunks = self._select_representative_chunks(chunks)
        excerpt_budget = self._calculate_excerpt_budget(len(selected_chunks))
        context = self._render_chunked_context(
            selected_chunks=selected_chunks,
            original_length=original_length,
            total_chunks=len(chunks),
            excerpt_limit=excerpt_budget,
        )

        while len(context) > self.MAX_TEXT_LENGTH_FOR_LLM and excerpt_budget > self.MIN_LONG_TEXT_EXCERPT:
            excerpt_budget = max(self.MIN_LONG_TEXT_EXCERPT, int(excerpt_budget * 0.85))
            context = self._render_chunked_context(
                selected_chunks=selected_chunks,
                original_length=original_length,
                total_chunks=len(chunks),
                excerpt_limit=excerpt_budget,
            )

        if len(context) > self.MAX_TEXT_LENGTH_FOR_LLM:
            marker = "\n\n...(chunked context compressed to fit the ontology analysis limit)..."
            context = context[:self.MAX_TEXT_LENGTH_FOR_LLM - len(marker)] + marker

        return context

    def _collect_document_chunks(self, document_texts: List[str]) -> List[Dict[str, Any]]:
        """Collect chunks per document, keeping document and chunk numbers so the
        prompt can reference their position."""

        all_chunks: List[Dict[str, Any]] = []
        for doc_index, text in enumerate(document_texts, 1):
            doc_chunks = split_text_into_chunks(
                text,
                chunk_size=self.LONG_TEXT_CHUNK_SIZE,
                overlap=self.LONG_TEXT_CHUNK_OVERLAP,
            )
            total_doc_chunks = len(doc_chunks)
            for chunk_index, chunk in enumerate(doc_chunks, 1):
                all_chunks.append({
                    "document_index": doc_index,
                    "chunk_index": chunk_index,
                    "total_document_chunks": total_doc_chunks,
                    "text": chunk,
                })

        return all_chunks

    def _select_representative_chunks(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sample chunks at even intervals so the start, middle and end of a long
        document are all covered."""

        if len(chunks) <= self.MAX_LONG_TEXT_CHUNKS:
            return chunks

        if self.MAX_LONG_TEXT_CHUNKS <= 1:
            return [chunks[0]]

        last_index = len(chunks) - 1
        selected_indexes = {
            round(i * last_index / (self.MAX_LONG_TEXT_CHUNKS - 1))
            for i in range(self.MAX_LONG_TEXT_CHUNKS)
        }
        return [chunks[i] for i in sorted(selected_indexes)]

    def _calculate_excerpt_budget(self, selected_count: int) -> int:
        """Allocate a per-chunk character budget based on how many chunks were selected."""

        header_budget = 600
        chunk_header_budget = 120 * selected_count
        available = max(
            self.MIN_LONG_TEXT_EXCERPT * selected_count,
            self.MAX_TEXT_LENGTH_FOR_LLM - header_budget - chunk_header_budget,
        )
        return max(self.MIN_LONG_TEXT_EXCERPT, available // max(selected_count, 1))

    def _render_chunked_context(
        self,
        selected_chunks: List[Dict[str, Any]],
        original_length: int,
        total_chunks: int,
        excerpt_limit: int,
    ) -> str:
        """Render the chunked context for long text."""

        lines = [
            (
                f"[Auto-chunked long-text summary] The source is {original_length} characters "
                f"and was split into {total_chunks} chunks for whole-document coverage."
            ),
            (
                f"Below are excerpts from {len(selected_chunks)} representative chunks "
                "covering the beginning, middle and end. Design the ontology from these "
                "whole-document clues, not from the opening section alone."
            ),
        ]

        for chunk in selected_chunks:
            excerpt = self._excerpt_text(chunk["text"], excerpt_limit)
            lines.append(
                "\n".join([
                    (
                        f"--- Document {chunk['document_index']} / "
                        f"chunk {chunk['chunk_index']}/{chunk['total_document_chunks']} ---"
                    ),
                    excerpt,
                ])
            )

        return "\n\n".join(lines)

    @staticmethod
    def _excerpt_text(text: str, char_limit: int) -> str:
        """Keep the head and tail of a long chunk so each chunk is not itself
        truncated to its opening."""

        text = text.strip()
        if len(text) <= char_limit:
            return text

        marker = "\n...(middle of this chunk omitted)...\n"
        if char_limit <= len(marker) + 20:
            return text[:char_limit]

        remaining = char_limit - len(marker)
        head_len = remaining // 2
        tail_len = remaining - head_len
        return f"{text[:head_len].rstrip()}{marker}{text[-tail_len:].lstrip()}"
    
    def _validate_and_process(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and post-process the result."""
        if not isinstance(result, dict):
            raise ValueError("Ontology result must be an object")

        raw_entities = result.get("entity_types")
        raw_edges = result.get("edge_types")
        if not isinstance(raw_entities, list):
            raw_entities = []
        if not isinstance(raw_edges, list):
            raw_edges = []
        if not isinstance(result.get("analysis_summary"), str):
            result["analysis_summary"] = ""

        # Normalize entity entries before touching their fields. LLMs
        # occasionally emit a bare string, null, or another scalar.
        entity_name_map: Dict[str, str] = {}
        processed_entities: List[Dict[str, Any]] = []
        seen_entity_names = set()
        for raw_entity in raw_entities:
            if isinstance(raw_entity, str):
                entity = {"name": raw_entity}
            elif isinstance(raw_entity, dict):
                entity = dict(raw_entity)
            else:
                logger.warning("Ignoring non-object ontology entity entry")
                continue

            original_name = entity.get("name")
            if not isinstance(original_name, str) or not original_name.strip():
                logger.warning("Ignoring ontology entity without a usable name")
                continue
            original_name = original_name.strip()
            normalized_name = _to_pascal_case(original_name)
            if normalized_name == "Unknown":
                continue
            if normalized_name in seen_entity_names:
                logger.warning(f"Duplicate entity type '{normalized_name}' removed during validation")
                entity_name_map[original_name] = normalized_name
                entity_name_map[original_name.lower()] = normalized_name
                continue

            if normalized_name != original_name:
                logger.warning(
                    f"Entity type name '{original_name}' auto-converted to '{normalized_name}'"
                )
            entity["name"] = normalized_name
            # An absent or unrecognised kind is left off rather than guessed at:
            # entity_kind() then falls through to the population-hints call and
            # the fixed name lists, which is also the path every ontology built
            # before this field existed takes.
            kind = normalize_entity_kind(entity.get("kind"))
            if kind:
                entity["kind"] = kind
            else:
                if entity.get("kind") is not None:
                    logger.warning(
                        f"Entity type '{normalized_name}' has an unusable kind "
                        f"{entity.get('kind')!r}; leaving it unclassified"
                    )
                entity.pop("kind", None)
            entity["attributes"] = normalize_ontology_attributes(
                entity.get("attributes", [])
            )
            if not isinstance(entity.get("examples"), list):
                entity["examples"] = []
            description = entity.get("description")
            if not isinstance(description, str) or not description:
                description = f"A {normalized_name} entity."
            entity["description"] = (
                description[:97] + "..." if len(description) > 100 else description
            )

            seen_entity_names.add(normalized_name)
            processed_entities.append(entity)
            entity_name_map[original_name] = normalized_name
            entity_name_map[original_name.lower()] = normalized_name
            entity_name_map[normalized_name] = normalized_name
            entity_name_map[normalized_name.lower()] = normalized_name

        result["entity_types"] = processed_entities

        # Fallback type definitions
        person_fallback = {
            "name": "Person",
            # Anonymous members of the public - a class of people, not one person.
            "kind": "general_individual",
            "description": "Any individual person not fitting other specific person types.",
            "attributes": [
                {"name": "full_name", "type": "text", "description": "Full name of the person"},
                {"name": "role", "type": "text", "description": "Role or occupation"}
            ],
            "examples": ["ordinary citizen", "anonymous netizen"]
        }
        
        organization_fallback = {
            "name": "Organization",
            # It collects named orgs that had no type of their own, so one
            # official account each rather than a class to be cloned.
            "kind": "specific_company",
            "description": "Any organization not fitting other specific organization types.",
            "attributes": [
                {"name": "org_name", "type": "text", "description": "Name of the organization"},
                {"name": "org_type", "type": "text", "description": "Type of organization"}
            ],
            "examples": ["small business", "community group"]
        }
        
        # Check whether the fallback types are already present
        entity_names = {e["name"] for e in result["entity_types"]}
        has_person = "Person" in entity_names
        has_organization = "Organization" in entity_names
        
        # Fallback types that still need adding
        fallbacks_to_add = []
        if not has_person:
            fallbacks_to_add.append(person_fallback)
        if not has_organization:
            fallbacks_to_add.append(organization_fallback)
        
        if fallbacks_to_add:
            current_count = len(result["entity_types"])
            needed_slots = len(fallbacks_to_add)
            
            # Adding them would exceed 10 types, so drop some existing ones
            if current_count + needed_slots > MAX_ONTOLOGY_TYPES:
                # How many types to drop
                to_remove = current_count + needed_slots - MAX_ONTOLOGY_TYPES
                # Drop from the end (the earlier, more important specific types stay)
                result["entity_types"] = result["entity_types"][:-to_remove]
            
            # Append the fallback types
            result["entity_types"].extend(fallbacks_to_add)
        
        # Defensive: make sure the limit is never exceeded
        result["entity_types"] = result["entity_types"][:MAX_ONTOLOGY_TYPES]

        # Resolve edge endpoints only after entity fallback/capping, so an edge
        # cannot refer to a type that was removed to satisfy the type limit.
        valid_entity_names = {entity["name"] for entity in result["entity_types"]}
        for name in valid_entity_names:
            entity_name_map[name] = name
            entity_name_map[name.lower()] = name

        def resolve_entity_name(value: str) -> Optional[str]:
            stripped = value.strip()
            if stripped == "Entity":
                return stripped
            mapped = entity_name_map.get(stripped) or entity_name_map.get(stripped.lower())
            if mapped in valid_entity_names:
                return mapped
            pascal_name = _to_pascal_case(stripped)
            return pascal_name if pascal_name in valid_entity_names else None

        processed_edges: List[Dict[str, Any]] = []
        seen_edge_names = set()
        for raw_edge in raw_edges:
            if isinstance(raw_edge, str):
                # A bare edge name has no endpoints and cannot be installed in
                # extracted safely. Ignore it instead of inventing a relationship.
                logger.warning(f"Ignoring ontology edge without source_targets: {raw_edge}")
                continue
            elif isinstance(raw_edge, dict):
                edge = dict(raw_edge)
            else:
                logger.warning("Ignoring non-object ontology edge entry")
                continue

            original_name = edge.get("name")
            if not isinstance(original_name, str) or not original_name.strip():
                logger.warning("Ignoring ontology edge without a usable name")
                continue
            normalized_name = _to_upper_snake_case(original_name)
            if normalized_name == "UNKNOWN" or normalized_name in seen_edge_names:
                if normalized_name in seen_edge_names:
                    logger.warning(f"Duplicate edge type '{normalized_name}' removed during validation")
                continue
            if normalized_name != original_name:
                logger.warning(
                    f"Edge type name '{original_name}' auto-converted to '{normalized_name}'"
                )
            edge["name"] = normalized_name

            normalized_targets = []
            for source_target in normalize_ontology_source_targets(
                edge.get("source_targets", []),
                limit=None,
            ):
                source = resolve_entity_name(source_target["source"])
                target = resolve_entity_name(source_target["target"])
                if source and target:
                    normalized_targets.append({"source": source, "target": target})
            edge["source_targets"] = normalize_ontology_source_targets(
                normalized_targets
            )
            edge["attributes"] = normalize_ontology_attributes(
                edge.get("attributes", [])
            )
            description = edge.get("description")
            if not isinstance(description, str) or not description:
                description = f"A {normalized_name} relationship."
            edge["description"] = (
                description[:97] + "..." if len(description) > 100 else description
            )

            seen_edge_names.add(normalized_name)
            processed_edges.append(edge)
            if len(processed_edges) == MAX_ONTOLOGY_TYPES:
                break

        result["edge_types"] = processed_edges
        
        return result
    

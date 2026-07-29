from app.services.ontology_generator import OntologyGenerator


class RecordingLLMClient:
    def __init__(self):
        self.calls = []

    def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "entity_types": [],
            "edge_types": [],
            "analysis_summary": "ok",
        }


def _generator_for_test() -> OntologyGenerator:
    generator = OntologyGenerator(llm_client=object())
    generator.MAX_TEXT_LENGTH_FOR_LLM = 2000
    generator.LONG_TEXT_CHUNK_SIZE = 500
    generator.LONG_TEXT_CHUNK_OVERLAP = 0
    generator.MAX_LONG_TEXT_CHUNKS = 3
    generator.MIN_LONG_TEXT_EXCERPT = 120
    return generator


def test_short_ontology_context_keeps_original_text():
    generator = _generator_for_test()

    context = generator._build_document_context(["short document body"])

    assert context == "short document body"
    assert "Auto-chunked long-text summary" not in context


def test_long_ontology_context_samples_across_document():
    generator = _generator_for_test()
    long_text = "BEGIN" + ("a" * 1050) + "MIDDLE" + ("b" * 1050) + "END"

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "Auto-chunked long-text summary" in context
    assert "BEGIN" in context
    assert "MIDDLE" in context
    assert "END" in context
    assert "chunk 1/" in context
    assert "chunk 3/" in context
    assert "chunk 5/" in context


def test_very_long_ontology_context_selects_representative_chunks():
    generator = _generator_for_test()
    chunks = ["BEGIN"] + [
        f"CHUNK{i:02d}-" + (str(i) * 490)
        for i in range(12)
    ] + ["FINALEND"]
    long_text = "".join(chunks)

    context = generator._build_document_context([long_text])

    assert len(context) <= generator.MAX_TEXT_LENGTH_FOR_LLM
    assert "BEGIN" in context
    assert "FINALEND" in context
    assert context.count("--- Document 1 / chunk") == generator.MAX_LONG_TEXT_CHUNKS


def test_ontology_generation_does_not_cap_structured_output_tokens():
    llm = RecordingLLMClient()
    generator = OntologyGenerator(llm_client=llm)

    result = generator.generate(
        document_texts=["A short source document."],
        simulation_requirement="Simulate the public discussion.",
    )

    assert result["analysis_summary"] == "ok"
    assert llm.calls[0]["max_tokens"] is None
    assert llm.calls[0]["max_attempts"] == 2


def test_entity_kind_is_kept_and_validated():
    generator = OntologyGenerator(llm_client=object())

    result = generator._validate_and_process({
        "entity_types": [
            {"name": "GenZStudent", "kind": "GENERAL_INDIVIDUAL"},
            {"name": "CarCompany", "kind": "general_company"},
            {"name": "TeslaExecutive", "kind": "specific_individual"},
            {"name": "LegacySegment", "kind": "segment"},
            {"name": "LegacyBrand", "kind": "institution"},
            {"name": "MediaOutlet", "kind": "nonsense"},
            {"name": "AutoForum"},
        ],
        "edge_types": [],
        "analysis_summary": "",
    })
    kinds = {e["name"]: e.get("kind") for e in result["entity_types"]}

    # Recognised values survive, case-normalised.
    assert kinds["GenZstudent"] == "general_individual"
    assert kinds["CarCompany"] == "general_company"
    assert kinds["TeslaExecutive"] == "specific_individual"
    # The three-way kind this replaced is still accepted, so an ontology saved
    # before the split keeps working without a migration over stored projects.
    assert kinds["LegacySegment"] == "general_individual"
    assert kinds["LegacyBrand"] == "specific_company"
    # An unusable or missing kind is left off rather than guessed at, so
    # entity_kind() falls through to the hints call and the fixed name lists.
    assert kinds["MediaOutlet"] is None
    assert kinds["AutoForum"] is None
    # The injected fallbacks carry their own kind: anonymous public is a class
    # of people, an unclassified org is one official account.
    assert kinds["Person"] == "general_individual"
    assert kinds["Organization"] == "specific_company"


def test_organization_fallback_is_never_cloned():
    """The ontology's kind is what stops a brand becoming the whole cast."""
    from app.services.agent_population import MAX_AGENTS, plan_population

    class _Entity:
        def __init__(self, name, entity_type):
            self.name, self._type = name, entity_type

        def get_entity_type(self):
            return self._type

    generator = OntologyGenerator(llm_client=object())
    ontology = generator._validate_and_process({
        "entity_types": [{"name": "GenZStudent", "kind": "general_individual"}],
        "edge_types": [],
        "analysis_summary": "",
    })
    kinds = {e["name"]: e["kind"] for e in ontology["entity_types"] if e.get("kind")}

    slots = plan_population(
        [_Entity("Gen Z / Students", "GenZstudent"),
         _Entity("Tesla", "Organization"),
         _Entity("TeslaMotors", "Organization")],
        kinds=kinds,
        seed=1,
    )

    assert len(slots) == MAX_AGENTS
    brands = [s for s in slots if s.entity_type == "Organization"]
    assert len(brands) == 2 and all(not s.is_clone for s in brands)
    assert all(not s.is_individual for s in brands)

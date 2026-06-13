"""Tests for the edit generator / extractor handoff into M1/M2."""
import os

import pytest

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "redocred_sample.json")
REL_INFO = os.path.join(os.path.dirname(__file__), "fixtures", "rel_info.json")


def _db_or_skip():
    try:
        from ripplekg.db.client import get_db
        return get_db(retries=1, delay=0.5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ArangoDB not available: {e}")


def _load_fixture(db):
    from ripplekg.db import schema
    from ripplekg.ingest.loader import ingest_dataset

    schema.drop_schema(db)
    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1, rel_info_path=REL_INFO)


def test_heuristic_extractor_keeps_only_supported_triples():
    from ripplekg.extraction.editor import heuristic_intended_triples

    triples = [
        ("Marie Curie", "place of birth", "Warsaw"),
        ("Marie Curie", "country of citizenship", "Poland"),
    ]
    intended = heuristic_intended_triples(triples, "Marie Curie was born in Warsaw.")

    assert intended == [("Marie Curie", "place of birth", "Warsaw")]


def test_semantic_verifier_recovers_llm_omitted_supported_old_triples():
    from ripplekg.extraction.editor import verify_supported_old_triples

    current = [
        ("Jon Montgomery", "country of citizenship", "Canadian"),
        ("Vancouver", "country", "Canada"),
    ]
    candidate = []
    verified = verify_supported_old_triples(
        current,
        "The Canadian team was coached by Jon Montgomery in Vancouver.",
        candidate,
    )

    assert verified == [("Jon Montgomery", "country of citizenship", "Canadian")]


def test_semantic_verifier_does_not_recover_superseded_replacement_value():
    from ripplekg.extraction.editor import verify_supported_old_triples

    current = [("Guri", "country", "Venezuela")]
    candidate = [
        ("Guri", "country", "Canada"),
        ("Guri", "country", "Venezuela"),
    ]
    verified = verify_supported_old_triples(
        current,
        "A school operated by Venezuela is located in Guri, Canada.",
        candidate,
        superseded_triples=current,
    )

    assert verified == [("Guri", "country", "Canada")]


def test_semantic_verifier_preserves_multivalue_relation_without_replacement():
    from ripplekg.extraction.editor import verify_supported_old_triples

    current = [("Album", "participant", "Singer A")]
    candidate = [("Album", "participant", "Singer B")]
    verified = verify_supported_old_triples(
        current,
        "Album includes Singer A and Singer B.",
        candidate,
    )

    assert verified == [
        ("Album", "participant", "Singer B"),
        ("Album", "participant", "Singer A"),
    ]


def test_semantic_verifier_replaces_only_targeted_multivalue_triple():
    from ripplekg.extraction.editor import verify_supported_old_triples

    current = [
        ("Olympics", "participant", "Athlete A"),
        ("Olympics", "participant", "Athlete B"),
    ]
    candidate = [("Olympics", "participant", "Athlete C")]
    verified = verify_supported_old_triples(
        current,
        "Athlete A, Athlete B, and Athlete C participated in the Olympics.",
        candidate,
        superseded_triples=[current[0]],
    )

    assert verified == [
        ("Olympics", "participant", "Athlete C"),
        ("Olympics", "participant", "Athlete B"),
    ]


def test_replacement_instruction_identifies_only_explicit_old_fact():
    from ripplekg.extraction.editor import _superseded_triples_for_instruction

    current = [
        ("Guri", "country", "Venezuela"),
        ("Bolívar", "country", "Venezuela"),
    ]
    superseded = _superseded_triples_for_instruction(
        current,
        "Replace the old fact 'Guri country Venezuela.' with 'Guri country Canada.'",
    )

    assert superseded == [("Guri", "country", "Venezuela")]


def test_fact_input_requires_llm_provider():
    from ripplekg.extraction.editor import build_edit_from_instruction

    class FakeCollection:
        def has(self, _key):
            return True

        def get(self, _key):
            return {"_key": "doc0:0", "text": "Marie Curie was born in Warsaw."}

    class FakeDB:
        def collection(self, _name):
            return FakeCollection()

    with pytest.raises(ValueError, match="requires provider"):
        build_edit_from_instruction(
            FakeDB(),
            doc_id="doc0",
            sent_idx=0,
            instruction="Marie Curie was born in Paris.",
            provider="heuristic",
            input_kind="fact",
        )


def test_relation_schema_aliases_are_canonicalized():
    from ripplekg.extraction.schema_merge import canonicalize_triples, match_relation_schema

    triples = [
        ("Marie Curie", "born in", "Warsaw"),
        ("Marie Curie", "citizen of", "Poland"),
        ("Marie Curie", "born in", "Warsaw"),
        ("Schneider", "became coach of", "Russian skeleton team"),
        ("Schneider", "became coach in", "July 2012"),
    ]

    assert canonicalize_triples(triples) == [
        ("Marie Curie", "place of birth", "Warsaw"),
        ("Marie Curie", "country of citizenship", "Poland"),
        ("Schneider", "coach", "Russian skeleton team"),
        ("Schneider", "start time", "July 2012"),
    ]

    match = match_relation_schema("birth location", ["place of birth", "place of death"])
    assert match["canonical_relation"] == "birth location"
    assert match["status"] == "unresolved"


def test_relation_schema_uses_current_ontology_for_exact_and_lexical_match():
    from ripplekg.extraction.schema_merge import canonicalize_triples, match_relation_schema

    ontology = ["place of birth", "country of citizenship", "participant in"]

    exact = match_relation_schema("participant in", ontology)
    assert exact["canonical_relation"] == "participant in"
    assert exact["method"] == "exact"

    lexical = match_relation_schema("country citizenship", ontology, threshold=0.75)
    assert lexical["canonical_relation"] == "country of citizenship"
    assert lexical["method"] == "lexical"
    assert lexical["status"] == "accepted"

    triples = canonicalize_triples(
        [("A", "country citizenship", "B")],
        candidates=ontology,
        threshold=0.75,
    )
    assert triples == [("A", "country of citizenship", "B")]


def test_fact_prompt_requires_direct_relevance():
    from ripplekg.extraction.openai_provider import _prompt

    prompt = _prompt(
        "Schneider won a medal.",
        [],
        "Schneider became coach of the Russian skeleton team.",
        input_kind="fact",
    )

    assert "applies_to_sentence" in prompt
    assert "Sharing only an entity" in prompt
    assert "Never append an unrelated fact" in prompt


def test_generated_edit_runs_through_m1_m2():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.extraction import build_edit_from_instruction
    from ripplekg.mechanism.pipeline import run_edit_transactional

    edit = build_edit_from_instruction(
        db,
        doc_id="doc0",
        sent_idx=0,
        instruction="remove Warsaw",
        provider="heuristic",
    )
    result = run_edit_transactional(db, edit, step=21, refresh_mode="immediate")

    assert "Warsaw" not in edit.new_text
    assert edit.intended_triples == []
    assert any(item.delta_type == "removed" for item in result.evidence_delta)
    assert any(item.decision == "REBUILD" for item in result.decisions)

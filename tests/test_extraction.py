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

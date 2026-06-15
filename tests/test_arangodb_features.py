"""ArangoDB-specific features: named graph, AQL updates, transactions."""
import os

import pytest

from ripplekg.models import EditOp

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


def test_schema_creates_named_provenance_graph():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.db import schema

    assert db.has_graph(schema.GRAPH_NAME)
    graph = db.graph(schema.GRAPH_NAME)
    edge_cols = {item["edge_collection"] for item in graph.edge_definitions()}
    assert edge_cols == {"mentions", "sentence_supports_relation"}


def test_aql_update_baseline_marks_reachable_objects_stale():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.baselines import aql_update

    result = aql_update.invalidate_sentence(db, "doc0:0", step=7)

    assert result["baseline"] == "B1_aql_update"
    assert result["stale_entities"] == ["doc0:e0", "doc0:e1"]
    assert result["stale_relations"] == ["doc0:r0"]
    assert db.collection("entities").get("doc0:e0")["freshness_status"] == "stale"
    assert db.collection("relations").get("doc0:r0")["last_changed_step"] == 7


def test_transactional_edit_commits_pipeline_outputs():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.mechanism.pipeline import run_edit_transactional

    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Warsaw was the birthplace of Marie Curie.",
        intended_triples=[("Marie Curie", "place of birth", "Warsaw")],
    )
    result = run_edit_transactional(db, edit, step=8, refresh_mode="deferred")

    assert result.edit["transaction"] == "committed"
    assert db.collection("sentences").get("doc0:0")["last_changed_step"] == 8
    assert db.collection("evidence_deltas").count() == len(result.evidence_delta)
    assert db.collection("refresh_decisions").count() == len(result.decisions)


def test_document_scope_edit_selects_and_commits_related_sentences():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.extraction.editor import build_edits_for_document_instruction
    from ripplekg.mechanism.pipeline import run_edits_transactional

    edits = build_edits_for_document_instruction(
        db,
        doc_id="doc0",
        instruction="remove Warsaw",
        provider="heuristic",
    )

    assert [edit.sent_idx for edit in edits] == [0, 1]

    results = run_edits_transactional(db, edits, step=9, refresh_mode="deferred")

    assert len(results) == 2
    assert {result.edit["transaction"] for result in results} == {"committed"}
    assert db.collection("sentences").get("doc0:0")["last_changed_step"] == 9
    assert db.collection("sentences").get("doc0:1")["last_changed_step"] == 9

    # Deferred mode marks affected objects stale but defers the refresh that drops
    # empty relations until a tick (docs/thought.md §11).
    assert db.collection("relations").get("doc0:r0")["freshness_status"] == "stale"
    assert db.collection("relations").get("doc0:r0")["status"] == "active"

    from ripplekg.mechanism.refresh import apply_refreshes
    apply_refreshes(db, step=9)

    assert db.collection("relations").get("doc0:r0")["status"] == "removed"
    assert db.collection("relations").get("doc0:r1")["status"] == "removed"

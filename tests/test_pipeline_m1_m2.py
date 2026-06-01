"""Integration tests for the M1/M2 edit pipeline."""
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

    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1, rel_info_path=REL_INFO)


def test_paraphrase_edit_skips_unchanged_evidence():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.mechanism.pipeline import run_edit

    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Warsaw was the birthplace of Marie Curie.",
        intended_triples=[("Marie Curie", "place of birth", "Warsaw")],
    )
    result = run_edit(db, edit, step=1, refresh_mode="deferred")

    assert {d.delta_type for d in result.evidence_delta} == {"unchanged"}
    assert {d.scope for d in result.evidence_delta} == {"mention", "relation"}
    assert {d.decision for d in result.decisions} == {"SKIP"}
    assert result.freshness["marked_stale"] == []
    assert db.collection("relations").get("doc0:r0")["freshness_status"] == "fresh"


def test_changed_relation_persists_delta_decision_and_refreshes():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.eval.metrics import summarize
    from ripplekg.mechanism.pipeline import run_edit

    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Marie Curie became associated with Poland.",
        intended_triples=[("Marie Curie", "country", "Poland")],
    )
    result = run_edit(db, edit, step=2, refresh_mode="immediate")

    delta_types = {d.delta_type for d in result.evidence_delta}
    decisions = {d.decision for d in result.decisions}
    assert {"added", "removed", "unchanged"} <= delta_types
    assert "PATCH" in decisions
    assert "REBUILD" in decisions
    assert result.freshness["marked_stale"]
    assert result.freshness["refreshed"]

    old_relation = db.collection("relations").get("doc0:r0")
    assert old_relation["status"] == "removed"
    assert old_relation["freshness_status"] == "fresh"

    metrics = summarize(db, step=2)
    assert metrics["evidence_delta"]["added"] >= 1
    assert metrics["evidence_delta"]["removed"] >= 1
    assert metrics["decisions"]["PATCH"] >= 1
    assert metrics["decisions"]["REBUILD"] >= 1

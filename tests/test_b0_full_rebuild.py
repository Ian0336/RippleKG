"""Tests for the B0 full-rebuild correctness baseline."""
import os

import pytest

from ripplekg.baselines import full_rebuild
from ripplekg.mechanism.policy import REBUILD_COST
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


def test_fresh_ingest_is_consistent():
    """A freshly ingested T0 graph already satisfies the IVM invariant."""
    db = _db_or_skip()
    _load_fixture(db)

    report = full_rebuild.check_consistency(db, require_fresh=True)
    assert report["consistent"], report["mismatches"]
    assert report["checked_relations"] >= 1
    assert report["checked_entities"] >= 1


def test_incremental_change_matches_full_rebuild():
    """After a relation-changing edit, the maintained KG equals a full rebuild."""
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.mechanism.pipeline import run_edit

    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Marie Curie became associated with Poland.",
        intended_triples=[("Marie Curie", "country", "Poland")],
    )
    run_edit(db, edit, step=1, refresh_mode="immediate")

    report = full_rebuild.check_consistency(db, require_fresh=True)
    assert report["consistent"], report["mismatches"]

    # The dropped relation's stored aggregate matches a fresh recomputation (0).
    recomputed = full_rebuild.recomputed_state(db)
    maintained = full_rebuild.maintained_state(db)
    for key, stored in maintained["relations"].items():
        assert stored["evidence_count"] == recomputed["relations"].get(key, 0)


def test_detects_injected_aggregate_drift():
    """If a stored evidence_count drifts from the edges, B0 must flag it."""
    db = _db_or_skip()
    _load_fixture(db)

    # Corrupt one relation's materialized aggregate without touching its edges.
    target = next(iter(full_rebuild.maintained_state(db)["relations"]))
    db.collection("relations").update({"_key": target, "evidence_count": 999})

    report = full_rebuild.check_consistency(db)
    assert not report["consistent"]
    kinds = {m["kind"] for m in report["mismatches"]}
    assert "relation_count" in kinds
    assert any(m["target_id"] == target for m in report["mismatches"])


def test_rebuild_cost_scales_with_document_objects():
    db = _db_or_skip()
    _load_fixture(db)

    counts = full_rebuild.document_object_count(db, "doc0")
    assert counts["objects"] == counts["entities"] + counts["relations"]
    assert counts["objects"] > 0
    assert full_rebuild.rebuild_cost(db, "doc0") == counts["objects"] * REBUILD_COST

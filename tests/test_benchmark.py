"""Tests for multi-sentence benchmark helpers."""
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


def test_benchmark_builds_intended_triples_from_current_evidence():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.eval.benchmark import intended_triples_from_current_evidence

    triples = intended_triples_from_current_evidence(db, "doc0:0")
    assert triples == [("Marie Curie", "place of birth", "Warsaw")]


def test_semantic_noop_benchmark_skips_unchanged_evidence():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.eval.benchmark import run_semantic_noop_benchmark, summarize_rows

    rows = run_semantic_noop_benchmark(db, limit=2)
    summary = summarize_rows(rows)

    assert summary["edits"] == 2
    assert rows[0].old_text
    assert rows[0].new_text.endswith(" ")
    assert rows[0].original_triples == rows[0].intended_triples
    assert rows[0].removed_from_intended == []
    assert rows[0].added_to_intended == []
    assert rows[0].intended_triples
    assert rows[0].evidence_audit
    assert rows[0].decision_audit
    assert rows[0].as_dict()["sent_id"] == rows[0].sent_id
    assert summary["deltas"]["unchanged"] > 0
    assert summary["deltas"]["added"] == 0
    assert summary["deltas"]["removed"] == 0
    assert summary["decisions"]["SKIP"] == summary["deltas"]["unchanged"]
    assert summary["ours_cost"] == 0
    assert summary["naive_stale_count"] > 0


def test_mixed_benchmark_exercises_multiple_decision_paths():
    db = _db_or_skip()
    _load_fixture(db)

    from ripplekg.eval.benchmark import run_mixed_benchmark, summarize_rows

    rows = run_mixed_benchmark(db, per_scenario=1)
    summary = summarize_rows(rows)

    assert {"semantic_noop", "remove_relation"} <= set(summary["by_scenario"])
    assert summary["decisions"]["SKIP"] > 0
    assert summary["decisions"]["REBUILD"] > 0
    assert summary["deltas"]["removed"] > 0
    assert any(row.removed_from_intended for row in rows)
    changed_text_rows = [
        row for row in rows if row.scenario in {"remove_relation", "change_relation_tail"}
    ]
    assert any(row.new_text != row.old_text for row in changed_text_rows)
    rebuilds = [
        item
        for row in rows
        for item in row.decision_audit
        if item["decision"] == "REBUILD"
    ]
    assert rebuilds
    assert rebuilds[0]["after_refresh"]["evidence_count"] == 0
    assert summary["naive_stale_count"] > 0

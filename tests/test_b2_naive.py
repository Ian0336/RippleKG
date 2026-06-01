"""Tests for the B2 naive invalidation baseline."""
import os

import pytest

from ripplekg.baselines import naive
from ripplekg.models import EditOp

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "redocred_sample.json")


def test_sentence_id():
    assert naive.sentence_id("doc7", 3) == "doc7:3"


def _db_or_skip():
    try:
        from ripplekg.db.client import get_db
        return get_db(retries=1, delay=0.5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ArangoDB not available: {e}")


def test_b2_marks_changed_sentence_objects_stale():
    db = _db_or_skip()

    from ripplekg.db import schema
    from ripplekg.ingest.loader import ingest_dataset

    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1)

    objects = naive.affected_objects(db, "doc0:0")
    assert objects["entities"] == ["doc0:e0", "doc0:e1"]
    assert objects["relations"] == ["doc0:r0"]

    result = naive.invalidate_sentence(db, "doc0:0", step=1)
    assert result["stale_count"] == 3
    assert result["cost"] == 3

    assert db.collection("entities").get("doc0:e0")["freshness_status"] == "stale"
    assert db.collection("entities").get("doc0:e1")["freshness_status"] == "stale"
    assert db.collection("relations").get("doc0:r0")["freshness_status"] == "stale"


def test_b2_run_edit_returns_pipeline_shaped_result():
    db = _db_or_skip()

    from ripplekg.db import schema
    from ripplekg.ingest.loader import ingest_dataset

    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1)

    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Marie Curie was born in Warsaw.",
        intended_triples=[("Marie Curie", "place of birth", "Warsaw")],
    )
    result = naive.run_edit(db, edit, step=2, dry_run=True)

    assert result.edit["baseline"] == "B2_naive_invalidation"
    assert result.evidence_delta == []
    assert len(result.decisions) == 3
    assert result.freshness["marked_stale"] == [
        "entity:doc0:e0",
        "entity:doc0:e1",
        "relation:doc0:r0",
    ]

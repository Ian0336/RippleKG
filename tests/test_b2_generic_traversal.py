"""Tests for the generic AQL traversal baseline."""
import os

import pytest

from ripplekg.baselines import generic_traversal
from ripplekg.models import EditOp

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "redocred_sample.json")


def test_sentence_id():
    assert generic_traversal.sentence_id("doc2", 4) == "doc2:4"


def _db_or_skip():
    try:
        from ripplekg.db.client import get_db
        return get_db(retries=1, delay=0.5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ArangoDB not available: {e}")


def test_generic_traversal_reaches_active_provenance_objects():
    db = _db_or_skip()

    from ripplekg.db import schema
    from ripplekg.ingest.loader import ingest_dataset

    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1)

    objects = generic_traversal.reachable_objects(db, "doc0:0")
    assert objects["entities"] == ["doc0:e0", "doc0:e1"]
    assert objects["relations"] == ["doc0:r0"]

    result = generic_traversal.invalidate_sentence(db, "doc0:0", step=1)
    assert result["baseline"] == "B1_generic_aql_traversal"
    assert result["stale_count"] == 3

    assert db.collection("entities").get("doc0:e0")["freshness_status"] == "stale"
    assert db.collection("entities").get("doc0:e1")["freshness_status"] == "stale"
    assert db.collection("relations").get("doc0:r0")["freshness_status"] == "stale"


def test_generic_traversal_run_edit_supports_dry_run():
    db = _db_or_skip()

    from ripplekg.db import schema
    from ripplekg.ingest.loader import ingest_dataset

    schema.init_schema(db)
    ingest_dataset(db, FIXTURE, limit=1)

    original_text = db.collection("sentences").get("doc0:0")["text"]
    edit = EditOp(
        doc_id="doc0",
        sent_idx=0,
        new_text="Marie Curie was born in Warsaw.",
        intended_triples=[("Marie Curie", "place of birth", "Warsaw")],
    )

    result = generic_traversal.run_edit(db, edit, step=2, dry_run=True)

    assert result.edit["baseline"] == "B1_generic_aql_traversal"
    assert result.edit["dry_run"] is True
    assert len(result.decisions) == 3
    assert db.collection("sentences").get("doc0:0")["text"] == original_text

"""Tests for the Re-DocRED ingest. Parser tests run anywhere; DB tests skip if ArangoDB is down."""
import os

import pytest

from ripplekg.ingest.docred import normalize_name, parse_docred

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "redocred_sample.json")


def test_normalize_name():
    assert normalize_name("  Marie   Curie ") == "marie curie"


def test_parse_docred_fixture():
    docs = parse_docred(FIXTURE)
    assert len(docs) == 1
    d = docs[0]
    assert d["sentences"][0].startswith("Marie Curie was born")
    assert len(d["entities"]) == 3

    warsaw = d["entities"][1]
    assert warsaw["norm_name"] == "warsaw"
    assert len(warsaw["mentions"]) == 2  # mentioned in both sentences

    assert {r["rel_type"] for r in d["relations"]} == {"place of birth", "country"}
    assert d["relations"][0]["evidence"] == [0]


def _db_or_skip():
    try:
        from ripplekg.db.client import get_db
        return get_db(retries=1, delay=0.5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ArangoDB not available: {e}")


def test_ingest_document_builds_graph():
    db = _db_or_skip()
    from ripplekg.db import repo, schema
    schema.init_schema(db)

    from ripplekg.ingest.loader import ingest_dataset
    n = ingest_dataset(db, FIXTURE, limit=1)
    assert n == 1

    g = repo.fetch_graph(db)
    assert len(g.nodes) == 3
    assert len(g.edges) == 2

    # provenance: editing sentence 0 reaches the place-of-birth relation + 2 mentions
    affected = repo.affected_evidence(db, "doc0:0")
    assert len(affected["relations"]) == 1
    assert len(affected["mentions"]) == 2

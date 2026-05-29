"""Scaffold smoke tests. Pure-model tests always run; DB tests skip if ArangoDB is down."""
import pytest

from ripplekg.db import schema
from ripplekg.models import EditOp, EditResult, GraphView


def test_edit_result_shape():
    r = EditResult(step=1, edit={"doc_id": "d", "sent_idx": 0, "old_text": None, "new_text": "x"})
    assert r.evidence_delta == []
    assert r.decisions == []
    assert set(r.freshness) == {"marked_stale", "refreshed"}
    assert set(r.cost) == {"this_step", "vs_full_rebuild"}


def test_edit_op_triples():
    e = EditOp(doc_id="d", sent_idx=2, new_text="t", intended_triples=[("A", "rel", "B")])
    assert e.intended_triples[0] == ("A", "rel", "B")


def test_graph_view_default_empty():
    g = GraphView()
    assert g.nodes == [] and g.edges == []


def test_schema_collection_counts():
    assert len(schema.DOCUMENT_COLLECTIONS) == 6
    assert len(schema.EDGE_COLLECTIONS) == 2
    known = set(schema.DOCUMENT_COLLECTIONS) | set(schema.EDGE_COLLECTIONS)
    assert set(schema.INDEXES) <= known


def _db_or_skip():
    try:
        from ripplekg.db.client import get_db
        return get_db(retries=1, delay=0.5)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"ArangoDB not available: {e}")


def test_init_schema_and_empty_graph():
    db = _db_or_skip()
    from ripplekg.db import repo
    schema.init_schema(db)
    for name in schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS:
        assert db.has_collection(name)
    g = repo.fetch_graph(db)
    assert isinstance(g.nodes, list) and isinstance(g.edges, list)

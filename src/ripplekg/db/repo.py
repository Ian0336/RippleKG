"""The data-access seam. Everything (mechanism / api / scripts) goes through here.

This module is pure plumbing — collection reads/writes and 1-hop provenance
traversal. It contains NO maintenance logic (that lives in ripplekg.mechanism).
"""
from arango.database import StandardDatabase

from ripplekg.db import schema
from ripplekg.models import Decision, Edge, EvidenceDelta, GraphView, Node


# ---------- reads (render / inspect) ----------

def fetch_graph(db: StandardDatabase, fresh_only: bool = False) -> GraphView:
    """Current KG as nodes (entities) + edges (relations) for the demo."""
    if not db.has_collection("entities"):
        return GraphView()
    ent_filter = "FILTER e.freshness_status == 'fresh'" if fresh_only else ""
    rel_filter = (
        "FILTER r.status != 'removed' AND r.freshness_status == 'fresh'"
        if fresh_only
        else "FILTER r.status != 'removed'"
    )
    nodes = list(db.aql.execute(
        f"""FOR e IN entities {ent_filter}
            RETURN {{ id: e._key, label: e.name, type: e.type,
                      freshness: e.freshness_status }}"""
    ))
    edges = list(db.aql.execute(
        f"""FOR r IN relations {rel_filter}
            RETURN {{ id: r._key, source: r.head, target: r.tail,
                      label: r.rel_type, freshness: r.freshness_status }}"""
    ))
    return GraphView(
        nodes=[Node(**n) for n in nodes],
        edges=[Edge(**e) for e in edges],
    )


def get_sentences(db: StandardDatabase, doc_id: str | None = None) -> list[dict]:
    if not db.has_collection("sentences"):
        return []
    if doc_id is None:
        return list(db.aql.execute("FOR s IN sentences SORT s.doc_id, s.idx RETURN s"))
    return list(db.aql.execute(
        "FOR s IN sentences FILTER s.doc_id == @d SORT s.idx RETURN s",
        bind_vars={"d": doc_id},
    ))


def affected_evidence(db: StandardDatabase, sent_id: str) -> dict:
    """1-hop OUTBOUND from a changed sentence — the affected set (thought.md §10.3)."""
    sent = f"sentences/{sent_id}"
    mentions = list(db.aql.execute(
        """FOR v, e IN 1..1 OUTBOUND @sent mentions
           FILTER e.status != 'removed'
           RETURN { edge: e, entity_id: v._key }""",
        bind_vars={"sent": sent},
    ))
    relations = list(db.aql.execute(
        """FOR v, e IN 1..1 OUTBOUND @sent sentence_supports_relation
           FILTER e.status != 'removed'
           RETURN { edge: e, relation_id: v._key }""",
        bind_vars={"sent": sent},
    ))
    return {"mentions": mentions, "relations": relations}


def list_deltas(db: StandardDatabase, step: int | None = None) -> list[dict]:
    if step is None:
        return list(db.aql.execute("FOR d IN evidence_deltas SORT d.step RETURN d"))
    return list(db.aql.execute(
        "FOR d IN evidence_deltas FILTER d.step == @s RETURN d", bind_vars={"s": step}
    ))


def list_decisions(db: StandardDatabase, step: int | None = None) -> list[dict]:
    if step is None:
        return list(db.aql.execute("FOR d IN refresh_decisions SORT d.step RETURN d"))
    return list(db.aql.execute(
        "FOR d IN refresh_decisions FILTER d.step == @s RETURN d", bind_vars={"s": step}
    ))


def pending_decisions(db: StandardDatabase) -> list[dict]:
    return list(db.aql.execute(
        "FOR d IN refresh_decisions FILTER d.status == 'pending' SORT d.step RETURN d"
    ))


def get_relation(db: StandardDatabase, relation_id: str) -> dict | None:
    if not db.has_collection("relations") or not db.collection("relations").has(relation_id):
        return None
    return db.collection("relations").get(relation_id)


def get_entity(db: StandardDatabase, entity_id: str) -> dict | None:
    if not db.has_collection("entities") or not db.collection("entities").has(entity_id):
        return None
    return db.collection("entities").get(entity_id)


def find_entity_by_norm(db: StandardDatabase, doc_id: str, norm_name: str) -> dict | None:
    rows = list(db.aql.execute(
        """FOR e IN entities
           FILTER STARTS_WITH(e._key, @prefix) AND e.norm_name == @norm
           LIMIT 1
           RETURN e""",
        bind_vars={"prefix": f"{doc_id}:e", "norm": norm_name},
    ))
    return rows[0] if rows else None


def find_relation(
    db: StandardDatabase,
    head: str,
    rel_type: str,
    tail: str,
) -> dict | None:
    rows = list(db.aql.execute(
        """FOR r IN relations
           FILTER r.head == @head
             AND r.tail == @tail
             AND r.rel_type == @rel_type
             AND r.status != 'removed'
           LIMIT 1
           RETURN r""",
        bind_vars={"head": head, "tail": tail, "rel_type": rel_type},
    ))
    return rows[0] if rows else None


def count_active_evidence(db: StandardDatabase, target_type: str, target_id: str) -> int:
    edge_col = "mentions" if target_type == "entity" else "sentence_supports_relation"
    target_col = "entities" if target_type == "entity" else "relations"
    rows = list(db.aql.execute(
        f"""FOR e IN {edge_col}
            FILTER e._to == @target_id AND e.status == 'active'
            COLLECT WITH COUNT INTO n
            RETURN n""",
        bind_vars={"target_id": f"{target_col}/{target_id}"},
    ))
    return rows[0] if rows else 0


def update_evidence_count(db: StandardDatabase, target_type: str, target_id: str) -> int:
    count = count_active_evidence(db, target_type, target_id)
    col = "entities" if target_type == "entity" else "relations"
    update = {"_key": target_id, "evidence_count": count}
    if target_type == "relation" and count == 0:
        update["status"] = "removed"
    db.collection(col).update(update)
    return count


# ---------- writes (pipeline stages persist here) ----------

def write_delta(db: StandardDatabase, delta: EvidenceDelta, step: int, sent_id: str) -> dict:
    doc = delta.model_dump()
    doc.update({"step": step, "sent_id": sent_id})
    return db.collection("evidence_deltas").insert(doc)


def write_decision(db: StandardDatabase, decision: Decision, step: int) -> dict:
    doc = decision.model_dump()
    doc.update({"step": step, "status": "pending"})
    return db.collection("refresh_decisions").insert(doc)


def mark_decision_applied(db: StandardDatabase, decision_key: str) -> None:
    db.collection("refresh_decisions").update({"_key": decision_key, "status": "applied"})


def set_freshness(
    db: StandardDatabase,
    target_type: str,
    target_id: str,
    status: str,
    step: int | None = None,
) -> None:
    col = "entities" if target_type == "entity" else "relations"
    db.collection(col).update(
        {"_key": target_id, "freshness_status": status, "last_changed_step": step}
    )


# ---------- bulk writes (ingest) ----------

def bulk_insert(db: StandardDatabase, collection: str, docs: list[dict]) -> None:
    if docs:
        db.collection(collection).insert_many(docs)


def clear_all(db: StandardDatabase) -> None:
    """Truncate the 8 collections so ingest can re-run idempotently."""
    for name in schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS:
        if db.has_collection(name):
            db.collection(name).truncate()

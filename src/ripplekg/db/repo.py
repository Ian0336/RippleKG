"""The data-access seam. Everything (mechanism / api / scripts) goes through here.

This module is pure plumbing — collection reads/writes and 1-hop provenance
traversal. It contains NO maintenance logic (that lives in ripplekg.mechanism).
"""
from arango.database import StandardDatabase

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

"""B0 baseline: full corpus-to-KG rebuild as a correctness reference (docs/thought.md §13).

This is the classic incremental-view-maintenance correctness statement: a
materialized view's stored aggregates must equal a *from-scratch recomputation*
over the base data. Here the base data is the active provenance evidence
(``mentions`` / ``sentence_supports_relation`` edges) and the materialized view is
the ``entities`` / ``relations`` collections that the M1/M2 incremental pipeline
maintains.

B0 recomputes every KG object's evidence aggregate directly from the active edges
and compares it to what the incremental path stored. If they match, our selective
SKIP / PATCH / REBUILD maintenance produced exactly the KG a full rebuild would
have — *without* rebuilding the whole graph. This is the correctness sanity check
the proposal contrasts the cheap incremental path against.

Two notes specific to Re-DocRED:

* ``evidence_count`` is the materialized aggregate; the recomputed count is the
  live number of active supporting edges. The invariant is ``equal``.
* Many Re-DocRED relations are annotated with an empty ``evidence`` list, so they
  are legitimately active with zero supporting edges. B0 therefore does **not**
  treat "0 edges" as "should be removed"; it only flags a removed relation that
  still has active edges, and any drift between stored and recomputed counts.
"""
from typing import TYPE_CHECKING

from ripplekg.mechanism.policy import REBUILD_COST

if TYPE_CHECKING:
    from arango.database import StandardDatabase


def _doc_filter(var: str, doc_id: str | None) -> tuple[str, dict]:
    if doc_id is None:
        return "", {}
    return f"FILTER STARTS_WITH({var}._key, @prefix)", {"prefix": f"{doc_id}:"}


def recomputed_state(db: "StandardDatabase", doc_id: str | None = None) -> dict:
    """Recompute each KG object's evidence aggregate purely from active edges.

    Returns the canonical "full rebuild" view, independent of the stored
    ``evidence_count`` / ``status`` fields::

        {"relations": {relation_key: active_supporting_edge_count},
         "entities":  {entity_key:  active_mention_edge_count}}
    """
    rel_filter, rel_bind = _doc_filter("r", doc_id)
    relations = {
        row["key"]: row["count"]
        for row in db.aql.execute(
            f"""
            FOR r IN relations
              {rel_filter}
              LET count = LENGTH(
                FOR v, e IN 1..1 INBOUND r sentence_supports_relation
                  FILTER e.status == 'active'
                  RETURN 1
              )
              RETURN {{ key: r._key, count: count }}
            """,
            bind_vars=rel_bind,
        )
    }
    ent_filter, ent_bind = _doc_filter("n", doc_id)
    entities = {
        row["key"]: row["count"]
        for row in db.aql.execute(
            f"""
            FOR n IN entities
              {ent_filter}
              LET count = LENGTH(
                FOR v, e IN 1..1 INBOUND n mentions
                  FILTER e.status == 'active'
                  RETURN 1
              )
              RETURN {{ key: n._key, count: count }}
            """,
            bind_vars=ent_bind,
        )
    }
    return {"relations": relations, "entities": entities}


def maintained_state(db: "StandardDatabase", doc_id: str | None = None) -> dict:
    """The KG aggregates as currently materialized by the incremental pipeline."""
    rel_filter, rel_bind = _doc_filter("r", doc_id)
    relations = {
        row["key"]: row
        for row in db.aql.execute(
            f"""FOR r IN relations {rel_filter}
                RETURN {{ key: r._key, evidence_count: r.evidence_count,
                          status: r.status, freshness: r.freshness_status }}""",
            bind_vars=rel_bind,
        )
    }
    ent_filter, ent_bind = _doc_filter("n", doc_id)
    entities = {
        row["key"]: row
        for row in db.aql.execute(
            f"""FOR n IN entities {ent_filter}
                RETURN {{ key: n._key, evidence_count: n.evidence_count,
                          freshness: n.freshness_status }}""",
            bind_vars=ent_bind,
        )
    }
    return {"relations": relations, "entities": entities}


def check_consistency(
    db: "StandardDatabase",
    doc_id: str | None = None,
    *,
    require_fresh: bool = False,
) -> dict:
    """Assert the incremental KG equals a from-scratch rebuild over active evidence.

    Mismatch kinds:

    ``relation_count``  stored ``evidence_count`` != recomputed active edge count.
    ``relation_status`` relation is marked ``removed`` yet still has active edges.
    ``entity_count``    stored ``evidence_count`` != recomputed active mention count.
    ``stale``           object still ``stale`` (only checked when ``require_fresh``).

    ``require_fresh`` is meaningful after an immediate-refresh run, where every
    maintained object should already be back to ``fresh``.
    """
    recomputed = recomputed_state(db, doc_id)
    maintained = maintained_state(db, doc_id)
    mismatches: list[dict] = []

    for key, stored in maintained["relations"].items():
        rebuilt_count = recomputed["relations"].get(key, 0)
        removed = stored.get("status") == "removed"
        if stored["evidence_count"] != rebuilt_count:
            mismatches.append({
                "kind": "relation_count", "target_id": key,
                "stored": stored["evidence_count"], "rebuilt": rebuilt_count,
            })
        if removed and rebuilt_count > 0:
            mismatches.append({
                "kind": "relation_status", "target_id": key,
                "stored": "removed", "rebuilt": f"{rebuilt_count} active edges",
            })
        if require_fresh and not removed and stored.get("freshness") != "fresh":
            mismatches.append({
                "kind": "stale", "target_id": key,
                "stored": stored.get("freshness"), "rebuilt": "fresh",
            })

    for key, stored in maintained["entities"].items():
        rebuilt_count = recomputed["entities"].get(key, 0)
        if stored["evidence_count"] != rebuilt_count:
            mismatches.append({
                "kind": "entity_count", "target_id": key,
                "stored": stored["evidence_count"], "rebuilt": rebuilt_count,
            })
        if require_fresh and stored.get("freshness") != "fresh":
            mismatches.append({
                "kind": "stale", "target_id": key,
                "stored": stored.get("freshness"), "rebuilt": "fresh",
            })

    # Transparency: relations that are active with no corpus evidence are static
    # Re-DocRED annotations (empty evidence list), not maintenance errors.
    evidence_free = sum(
        1
        for key, stored in maintained["relations"].items()
        if stored.get("status") != "removed" and recomputed["relations"].get(key, 0) == 0
    )

    return {
        "baseline": "B0_full_rebuild",
        "doc_id": doc_id,
        "consistent": not mismatches,
        "mismatches": mismatches,
        "checked_relations": len(maintained["relations"]),
        "checked_entities": len(maintained["entities"]),
        "evidence_free_relations": evidence_free,
    }


def document_object_count(db: "StandardDatabase", doc_id: str) -> dict:
    """Count active KG objects in one document — the scope a document rebuild re-derives."""
    row = list(db.aql.execute(
        """
        RETURN {
          entities: LENGTH(FOR n IN entities FILTER STARTS_WITH(n._key, @p) RETURN 1),
          relations: LENGTH(
            FOR r IN relations FILTER STARTS_WITH(r._key, @p) AND r.status != 'removed' RETURN 1
          )
        }
        """,
        bind_vars={"p": f"{doc_id}:"},
    ))[0]
    row["objects"] = row["entities"] + row["relations"]
    return row


def corpus_object_count(db: "StandardDatabase") -> dict:
    """Count active KG objects across the whole corpus — the scope a full rebuild re-derives."""
    row = list(db.aql.execute(
        """
        RETURN {
          entities: LENGTH(FOR n IN entities RETURN 1),
          relations: LENGTH(FOR r IN relations FILTER r.status != 'removed' RETURN 1)
        }
        """
    ))[0]
    row["objects"] = row["entities"] + row["relations"]
    return row


def document_rebuild_cost(
    db: "StandardDatabase",
    doc_id: str,
    *,
    rebuild_cost_unit: int = REBUILD_COST,
) -> int:
    """Nominal cost of a document-level (GraphRAG-style) rebuild after one edit.

    Re-derives every object in the changed document, so each counts as one
    REBUILD. The middle tier between a whole-corpus rebuild and our incremental
    path, which pays only for the objects whose evidence actually changed.
    """
    return document_object_count(db, doc_id)["objects"] * rebuild_cost_unit


def whole_kg_rebuild_cost(
    db: "StandardDatabase",
    *,
    rebuild_cost_unit: int = REBUILD_COST,
) -> int:
    """Nominal cost B0 pays to fully rebuild the entire corpus KG after one edit.

    A full corpus-to-KG rebuild re-derives every object in every document, so the
    cost is independent of which sentence changed — the most expensive tier.
    """
    return corpus_object_count(db)["objects"] * rebuild_cost_unit


# Backwards-compatible alias: the original B0 cost was document-scoped.
rebuild_cost = document_rebuild_cost

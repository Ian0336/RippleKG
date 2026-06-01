"""B1/B2 AQL baseline: generic provenance traversal invalidation.

This baseline asks ArangoDB for every active KG object reachable from a changed
sentence over the provenance edge collections, then marks all of them stale. It
is deliberately graph-structural: it does not compare old/new evidence, so it
over-invalidates on paraphrases just like the naive baseline.
"""
import hashlib
from typing import TYPE_CHECKING, Any

from ripplekg.models import Decision, EditOp, EditResult

if TYPE_CHECKING:
    from arango.database import StandardDatabase

EDGE_COLLECTIONS = ["mentions", "sentence_supports_relation"]


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sentence_id(doc_id: str, sent_idx: int) -> str:
    """Return the canonical sentence key used by the Re-DocRED ingest."""
    return f"{doc_id}:{sent_idx}"


def reachable_objects(
    db: "StandardDatabase",
    sent_id: str,
    *,
    max_depth: int = 1,
) -> dict[str, list[str]]:
    """Return KG objects reachable from one sentence through provenance edges."""
    query = """
    FOR v, e, p IN 1..@max_depth OUTBOUND @sent
      mentions, sentence_supports_relation
      FILTER e.status != 'removed'
      FILTER IS_SAME_COLLECTION('entities', v)
          OR IS_SAME_COLLECTION('relations', v)
      RETURN DISTINCT {
        collection: PARSE_IDENTIFIER(v._id).collection,
        key: v._key
      }
    """
    rows = list(db.aql.execute(
        query,
        bind_vars={
            "sent": f"sentences/{sent_id}",
            "max_depth": max_depth,
        },
    ))

    entities = sorted(row["key"] for row in rows if row["collection"] == "entities")
    relations = sorted(row["key"] for row in rows if row["collection"] == "relations")
    return {"entities": entities, "relations": relations}


def invalidate_sentence(
    db: "StandardDatabase",
    sent_id: str,
    step: int,
    *,
    max_depth: int = 1,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mark all graph-reachable entities/relations from a sentence as stale."""
    from ripplekg.db import repo

    objects = reachable_objects(db, sent_id, max_depth=max_depth)
    decisions: list[Decision] = [
        Decision(
            target_type="entity",
            target_id=entity_id,
            decision="REBUILD",
            reason="Generic AQL traversal baseline: reachable entity",
            cost=1,
        )
        for entity_id in objects["entities"]
    ] + [
        Decision(
            target_type="relation",
            target_id=relation_id,
            decision="REBUILD",
            reason="Generic AQL traversal baseline: reachable relation",
            cost=1,
        )
        for relation_id in objects["relations"]
    ]

    if not dry_run:
        for decision in decisions:
            repo.set_freshness(
                db,
                decision.target_type,
                decision.target_id,
                "stale",
                step,
            )

    return {
        "baseline": "B1_generic_aql_traversal",
        "sent_id": sent_id,
        "step": step,
        "max_depth": max_depth,
        "stale_entities": objects["entities"],
        "stale_relations": objects["relations"],
        "stale_count": len(decisions),
        "decisions": decisions,
        "cost": sum(decision.cost for decision in decisions),
        "dry_run": dry_run,
    }


def run_edit(
    db: "StandardDatabase",
    edit: EditOp,
    step: int,
    *,
    max_depth: int = 1,
    dry_run: bool = False,
) -> EditResult:
    """Apply an edit and run the generic traversal baseline."""
    sent_id = sentence_id(edit.doc_id, edit.sent_idx)
    old_text = None

    if db.has_collection("sentences"):
        sentences = db.collection("sentences")
        if sentences.has(sent_id):
            current = sentences.get(sent_id)
            old_text = current.get("text")
            if not dry_run:
                sentences.update({
                    "_key": sent_id,
                    "text": edit.new_text,
                    "text_hash": _hash(edit.new_text),
                    "last_changed_step": step,
                })

    result = invalidate_sentence(
        db,
        sent_id,
        step,
        max_depth=max_depth,
        dry_run=dry_run,
    )
    decisions = result["decisions"]
    marked_stale = [
        f"{decision.target_type}:{decision.target_id}"
        for decision in decisions
    ]

    return EditResult(
        step=step,
        edit={
            "doc_id": edit.doc_id,
            "sent_idx": edit.sent_idx,
            "old_text": old_text,
            "new_text": edit.new_text,
            "baseline": result["baseline"],
            "dry_run": dry_run,
            "max_depth": max_depth,
        },
        decisions=decisions,
        freshness={"marked_stale": marked_stale, "refreshed": []},
        cost={
            "this_step": result["cost"],
            "vs_full_rebuild": result["cost"],
        },
    )

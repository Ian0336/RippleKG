"""B2 baseline: naive sentence-level invalidation.

This intentionally skips M1 semantic evidence delta. A changed sentence directly
invalidates every active entity mention and relation evidence reachable from it.
The point is to make the over-invalidation visible, especially for paraphrases
where the evidence-aware pipeline can SKIP.
"""
import hashlib
from typing import TYPE_CHECKING, Any

from ripplekg.models import Decision, EditOp, EditResult

if TYPE_CHECKING:
    from arango.database import StandardDatabase


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def sentence_id(doc_id: str, sent_idx: int) -> str:
    """Return the canonical sentence key used by the Re-DocRED ingest."""
    return f"{doc_id}:{sent_idx}"


def affected_objects(db: "StandardDatabase", sent_id: str) -> dict[str, list[str]]:
    """Return active KG objects that B2 would mark stale for one sentence."""
    from ripplekg.db import repo

    affected = repo.affected_evidence(db, sent_id)

    entity_ids = sorted({
        item["entity_id"]
        for item in affected.get("mentions", [])
        if item.get("entity_id")
    })
    relation_ids = sorted({
        item["relation_id"]
        for item in affected.get("relations", [])
        if item.get("relation_id")
    })

    return {
        "entities": entity_ids,
        "relations": relation_ids,
    }


def invalidate_sentence(
    db: "StandardDatabase",
    sent_id: str,
    step: int,
    *,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Mark every mentioned entity/relation from a changed sentence as stale.

    B2 has no semantic delta and no SKIP/PATCH/REBUILD policy. For comparison
    with the main pipeline, we still return one nominal REBUILD decision per
    invalidated object, making the baseline's cost explicit.
    """
    from ripplekg.db import repo

    objects = affected_objects(db, sent_id)

    decisions: list[Decision] = [
        Decision(
            target_type="entity",
            target_id=entity_id,
            decision="REBUILD",
            reason="B2 naive invalidation: changed sentence mentions entity",
            cost=1,
        )
        for entity_id in objects["entities"]
    ] + [
        Decision(
            target_type="relation",
            target_id=relation_id,
            decision="REBUILD",
            reason="B2 naive invalidation: changed sentence supports relation",
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
        "baseline": "B2_naive_invalidation",
        "sent_id": sent_id,
        "step": step,
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
    dry_run: bool = False,
) -> EditResult:
    """Apply an edit and run B2 naive invalidation.

    The return shape mirrors ``pipeline.run_edit`` so scripts/notebooks can swap
    the baseline in with minimal glue code.
    """
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

    result = invalidate_sentence(db, sent_id, step, dry_run=dry_run)
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
        },
        decisions=decisions,
        freshness={"marked_stale": marked_stale, "refreshed": []},
        cost={
            "this_step": result["cost"],
            "vs_full_rebuild": result["cost"],
        },
    )

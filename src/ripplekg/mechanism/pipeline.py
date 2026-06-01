"""§10 — Update path orchestration. The single entry point every front door calls.

    run_edit(db, edit, step) -> EditResult
"""
import hashlib

from arango.database import StandardDatabase

from ripplekg.db import repo, schema
from ripplekg.mechanism import delta, policy, refresh
from ripplekg.models import EditOp, EditResult


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def run_edit(
    db: StandardDatabase,
    edit: EditOp,
    step: int,
    refresh_mode: str = "immediate",
) -> EditResult:
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    old_text = None
    sentence_found = False

    if db.has_collection("sentences"):
        sentences = db.collection("sentences")
        if sentences.has(sent_id):
            sentence_found = True
            current = sentences.get(sent_id)
            old_text = current.get("text")
            sentences.update({
                "_key": sent_id,
                "text": edit.new_text,
                "text_hash": _hash(edit.new_text),
                "last_changed_step": step,
            })

    deltas = []
    decisions = []
    marked_stale = []
    refreshed = []

    if sentence_found:
        deltas = delta.compute_and_apply(db, sent_id, edit.intended_triples, step, edit.new_text)
        for item in deltas:
            repo.write_delta(db, item, step, sent_id)

        for item in deltas:
            decision = policy.decide(db, item)
            decisions.append(decision)
            repo.write_decision(db, decision, step)
            if decision.decision in {"PATCH", "REBUILD"}:
                repo.set_freshness(
                    db,
                    decision.target_type,
                    decision.target_id,
                    "stale",
                    step,
                )
                marked_stale.append(f"{decision.target_type}:{decision.target_id}")

        if refresh_mode == "immediate":
            refresh_result = refresh.apply_refreshes(db, step)
            refreshed = refresh_result.get("refreshed", [])
        elif refresh_mode != "deferred":
            raise ValueError("refresh_mode must be 'immediate' or 'deferred'")

    return EditResult(
        step=step,
        edit={
            "doc_id": edit.doc_id,
            "sent_idx": edit.sent_idx,
            "old_text": old_text,
            "new_text": edit.new_text,
            "sentence_found": sentence_found,
            "refresh_mode": refresh_mode,
        },
        evidence_delta=deltas,
        decisions=decisions,
        freshness={"marked_stale": marked_stale, "refreshed": refreshed},
        cost={
            "this_step": sum(decision.cost for decision in decisions),
            "vs_full_rebuild": len(decisions) * policy.REBUILD_COST,
        },
    )


def run_edit_transactional(
    db: StandardDatabase,
    edit: EditOp,
    step: int,
    refresh_mode: str = "immediate",
) -> EditResult:
    """Run one edit step inside an ArangoDB stream transaction."""
    txn = db.begin_transaction(
        read=schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS,
        write=schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS,
    )
    try:
        result = run_edit(txn, edit, step, refresh_mode)
        txn.commit_transaction()
        result.edit["transaction"] = "committed"
        return result
    except Exception:
        txn.abort_transaction()
        raise

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


def _embedding_update(new_text: str, had_embedding: bool) -> tuple[dict, str]:
    """Refresh sentence.embedding if the sentence already had one.

    Embeddings are optional. If the extra dependency is not installed, the core
    evidence-refresh path still runs and records why embedding refresh skipped.
    """
    if not had_embedding:
        return {}, "not_present"

    try:
        from ripplekg.extraction.embeddings import compute_embedding, embedding_backend
    except Exception as exc:  # noqa: BLE001
        return {}, f"unavailable:{exc}"

    try:
        return {
            "embedding": compute_embedding(new_text),
            "embedding_backend": embedding_backend(),
        }, "refreshed"
    except Exception as exc:  # noqa: BLE001
        return {}, f"failed:{exc}"


def run_edit(
    db: StandardDatabase,
    edit: EditOp,
    step: int,
    refresh_mode: str = "immediate",
) -> EditResult:
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    old_text = None
    sentence_found = False
    embedding_refresh = "not_present"

    if db.has_collection("sentences"):
        sentences = db.collection("sentences")
        if sentences.has(sent_id):
            sentence_found = True
            current = sentences.get(sent_id)
            old_text = current.get("text")
            embedding_patch, embedding_refresh = _embedding_update(
                edit.new_text,
                had_embedding="embedding" in current,
            )
            update_doc = {
                "_key": sent_id,
                "text": edit.new_text,
                "text_hash": _hash(edit.new_text),
                "last_changed_step": step,
            }
            update_doc.update(embedding_patch)
            sentences.update(update_doc)

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
            "embedding_refresh": embedding_refresh,
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


def run_edits_transactional(
    db: StandardDatabase,
    edits: list[EditOp],
    step: int,
    refresh_mode: str = "immediate",
) -> list[EditResult]:
    """Run multiple sentence edits as one logical ArangoDB transaction."""
    if refresh_mode not in {"immediate", "deferred"}:
        raise ValueError("refresh_mode must be 'immediate' or 'deferred'")

    txn = db.begin_transaction(
        read=schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS,
        write=schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS,
    )
    try:
        results = [
            run_edit(txn, edit, step, refresh_mode="deferred")
            for edit in edits
        ]

        refreshed = []
        if refresh_mode == "immediate" and edits:
            refresh_result = refresh.apply_refreshes(txn, step)
            refreshed = refresh_result.get("refreshed", [])
            if results:
                results[-1].freshness["refreshed"] = refreshed

        txn.commit_transaction()
        for result in results:
            result.edit["transaction"] = "committed"
            result.edit["batch_size"] = len(edits)
            result.edit["batch_refresh_mode"] = refresh_mode
        return results
    except Exception:
        txn.abort_transaction()
        raise

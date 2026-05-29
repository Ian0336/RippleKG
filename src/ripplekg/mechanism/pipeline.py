"""§10 — Update path orchestration. The single entry point every front door calls.

    run_edit(db, edit, step) -> EditResult

Current state: SCAFFOLD STUB. It applies the sentence text change (if the
sentence exists) and returns a correctly-shaped EditResult with empty
deltas/decisions. The real wiring is the M3/M4/M5 milestones:

    affected = repo.affected_evidence(db, sent_id)          # §10.3
    deltas   = delta.compute_delta(affected, intended)      # M1  §10.4
    for d in deltas: repo.write_delta(...)
    decisions = [policy.decide(d, state) for d in deltas]   # M2  §10.5
    for dec in decisions: repo.write_decision(...); repo.set_freshness(stale)
    if refresh_mode == "immediate": refresh.apply_refreshes(db, step)
"""
import hashlib

from arango.database import StandardDatabase

from ripplekg.models import EditOp, EditResult


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def run_edit(
    db: StandardDatabase,
    edit: EditOp,
    step: int,
    refresh_mode: str = "deferred",
) -> EditResult:
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    old_text = None

    if db.has_collection("sentences"):
        sentences = db.collection("sentences")
        if sentences.has(sent_id):
            current = sentences.get(sent_id)
            old_text = current.get("text")
            sentences.update({
                "_key": sent_id,
                "text": edit.new_text,
                "text_hash": _hash(edit.new_text),
                "last_changed_step": step,
            })

    # TODO(M3/M4/M5): M1 delta -> M2 decision -> freshness -> refresh.
    return EditResult(
        step=step,
        edit={
            "doc_id": edit.doc_id,
            "sent_idx": edit.sent_idx,
            "old_text": old_text,
            "new_text": edit.new_text,
        },
    )

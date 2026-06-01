"""§11 — Refresh execution (immediate + deferred).

Processes pending refresh_decisions: PATCH recomputes aggregates from current
evidence; REBUILD re-resolves / drops empty edges; both flip freshness -> fresh.
Idempotent (always recomputes from current evidence) — safe to defer.
"""
from arango.database import StandardDatabase

from ripplekg.db import repo


def apply_refreshes(
    db: StandardDatabase,
    step: int | None = None,
    only: str | None = None,
) -> dict:
    refreshed = []
    skipped = []

    for decision in repo.pending_decisions(db):
        if step is not None and decision.get("step", 0) > step:
            continue
        if only is not None and decision["decision"] != only:
            continue

        target_type = decision["target_type"]
        target_id = decision["target_id"]

        if decision["decision"] == "SKIP":
            repo.mark_decision_applied(db, decision["_key"])
            skipped.append(f"{target_type}:{target_id}")
            continue

        evidence_count = repo.update_evidence_count(db, target_type, target_id)
        repo.set_freshness(db, target_type, target_id, "fresh", decision.get("step"))
        repo.mark_decision_applied(db, decision["_key"])
        refreshed.append({
            "target_type": target_type,
            "target_id": target_id,
            "decision": decision["decision"],
            "evidence_count": evidence_count,
        })

    return {"refreshed": refreshed, "skipped": skipped}

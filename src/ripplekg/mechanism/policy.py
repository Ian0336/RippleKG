"""M2: cost-aware invalidation policy."""
from arango.database import StandardDatabase

from ripplekg.db import repo
from ripplekg.models import Decision, EvidenceDelta

PATCH_COST = 1
REBUILD_COST = 5


def target_type(delta: EvidenceDelta) -> str:
    return "entity" if delta.scope == "mention" else "relation"


def decide(db: StandardDatabase, delta: EvidenceDelta) -> Decision:
    """Choose SKIP/PATCH/REBUILD for one evidence delta."""
    kind = target_type(delta)

    if delta.delta_type == "unchanged":
        return Decision(
            target_type=kind,
            target_id=delta.target_id,
            decision="SKIP",
            reason="Evidence is semantically unchanged",
            cost=0,
        )

    if delta.delta_type == "added":
        return Decision(
            target_type=kind,
            target_id=delta.target_id,
            decision="PATCH",
            reason="New active evidence can be patched into aggregates",
            cost=PATCH_COST,
        )

    active_count = repo.count_active_evidence(db, kind, delta.target_id)
    if active_count == 0:
        return Decision(
            target_type=kind,
            target_id=delta.target_id,
            decision="REBUILD",
            reason="Last active evidence was removed",
            cost=REBUILD_COST,
        )

    return Decision(
        target_type=kind,
        target_id=delta.target_id,
        decision="PATCH",
        reason="Evidence was removed but other active evidence remains",
        cost=PATCH_COST,
    )

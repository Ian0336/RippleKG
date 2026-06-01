"""AQL graph-update baseline.

This baseline performs invalidation inside ArangoDB: a changed sentence is used
as the traversal start vertex, and AQL UPDATE statements mark reachable KG
objects stale.
"""
from typing import TYPE_CHECKING, Any

from ripplekg.models import Decision

if TYPE_CHECKING:
    from arango.database import StandardDatabase


def invalidate_sentence(
    db: "StandardDatabase",
    sent_id: str,
    step: int,
) -> dict[str, Any]:
    sent = f"sentences/{sent_id}"
    entity_rows = list(db.aql.execute(
        """
        FOR v, e IN 1..1 OUTBOUND @sent mentions
          FILTER e.status != 'removed'
          COLLECT key = v._key
          UPDATE key WITH {
            freshness_status: 'stale',
            last_changed_step: @step
          } IN entities
          RETURN NEW._key
        """,
        bind_vars={"sent": sent, "step": step},
    ))
    relation_rows = list(db.aql.execute(
        """
        FOR v, e IN 1..1 OUTBOUND @sent sentence_supports_relation
          FILTER e.status != 'removed'
          COLLECT key = v._key
          UPDATE key WITH {
            freshness_status: 'stale',
            last_changed_step: @step
          } IN relations
          RETURN NEW._key
        """,
        bind_vars={"sent": sent, "step": step},
    ))

    decisions = [
        Decision(
            target_type="entity",
            target_id=key,
            decision="REBUILD",
            reason="AQL update baseline: reachable entity marked stale in ArangoDB",
            cost=1,
        )
        for key in sorted(entity_rows)
    ] + [
        Decision(
            target_type="relation",
            target_id=key,
            decision="REBUILD",
            reason="AQL update baseline: reachable relation marked stale in ArangoDB",
            cost=1,
        )
        for key in sorted(relation_rows)
    ]

    return {
        "baseline": "B1_aql_update",
        "sent_id": sent_id,
        "step": step,
        "stale_entities": sorted(entity_rows),
        "stale_relations": sorted(relation_rows),
        "stale_count": len(decisions),
        "decisions": decisions,
        "cost": sum(decision.cost for decision in decisions),
    }

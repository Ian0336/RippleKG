"""Evaluation summaries from persisted ArangoDB logs."""
from arango.database import StandardDatabase

from ripplekg.mechanism.policy import REBUILD_COST


def _counts(db: StandardDatabase, collection: str, field: str, step: int | None) -> dict:
    step_filter = "FILTER item.step == @step" if step is not None else ""
    bind_vars = {"step": step} if step is not None else {}
    rows = list(db.aql.execute(
        f"""FOR item IN {collection}
            {step_filter}
            COLLECT value = item.{field} WITH COUNT INTO n
            RETURN {{ value, n }}""",
        bind_vars=bind_vars,
    ))
    return {row["value"]: row["n"] for row in rows}


def summarize(db: StandardDatabase, step: int | None = None) -> dict:
    """Return the project metrics described in docs/thought.md §14."""
    delta_counts = _counts(db, "evidence_deltas", "delta_type", step)
    decision_counts = _counts(db, "refresh_decisions", "decision", step)

    step_filter = "FILTER d.step == @step" if step is not None else ""
    bind_vars = {"step": step} if step is not None else {}
    cost_rows = list(db.aql.execute(
        f"""FOR d IN refresh_decisions
            {step_filter}
            COLLECT AGGREGATE total = SUM(d.cost), count = COUNT()
            RETURN {{ total, count }}""",
        bind_vars=bind_vars,
    ))
    cost = cost_rows[0] if cost_rows else {"total": 0, "count": 0}

    stale_rows = list(db.aql.execute(
        """RETURN {
          entities: LENGTH(FOR e IN entities FILTER e.freshness_status == 'stale' RETURN 1),
          relations: LENGTH(FOR r IN relations FILTER r.freshness_status == 'stale' RETURN 1)
        }"""
    ))

    return {
        "step": step,
        "evidence_delta": {
            "added": delta_counts.get("added", 0),
            "removed": delta_counts.get("removed", 0),
            "unchanged": delta_counts.get("unchanged", 0),
        },
        "decisions": {
            "SKIP": decision_counts.get("SKIP", 0),
            "PATCH": decision_counts.get("PATCH", 0),
            "REBUILD": decision_counts.get("REBUILD", 0),
        },
        "cost": {
            "actual": cost.get("total") or 0,
            "full_rebuild_nominal": (cost.get("count") or 0) * REBUILD_COST,
        },
        "stale": stale_rows[0] if stale_rows else {"entities": 0, "relations": 0},
    }

"""§11 — Refresh execution (immediate + deferred). Owner: C.

Processes pending refresh_decisions: PATCH recomputes aggregates from current
evidence; REBUILD re-resolves / drops empty edges; both flip freshness -> fresh.
Idempotent (always recomputes from current evidence) — safe to defer.

Stub returns a no-op summary so POST /tick is safe before this is implemented.
"""
from arango.database import StandardDatabase


def apply_refreshes(
    db: StandardDatabase,
    step: int | None = None,
    only: str | None = None,
) -> dict:
    # TODO(C, §11): for d in repo.pending_decisions(db): PATCH/REBUILD -> set fresh.
    return {"refreshed": [], "note": "stub: refresh.apply_refreshes not implemented (§11)"}

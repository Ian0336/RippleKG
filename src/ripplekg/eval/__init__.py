"""Metrics from the persisted logs (docs/thought.md §14). Owner: D.

Counts straight off evidence_deltas / refresh_decisions:
added/removed/unchanged, SKIP/PATCH/REBUILD, nominal cost vs full rebuild,
stale-object curve across steps. No separate eval harness needed.
"""

"""Baselines (docs/thought.md §13), all optional. Owners: A / B2.

  full_rebuild.py       B0  rebuild whole KG after each edit (correctness sanity)
  generic_traversal.py  B1  reachability invalidation over provenance edges
  aql_update.py         B1  graph-update invalidation executed directly in AQL
  naive.py              B2  changed sentence -> mark mentioned objects stale
"""

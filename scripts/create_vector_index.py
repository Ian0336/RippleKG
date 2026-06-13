"""Create a native ArangoDB vector index for sentence embeddings when available.

ArangoDB 3.12 requires the server to be started with `--vector-index`.
If the feature is unavailable, semantic search still falls back to an exact
Python cosine scan over stored embeddings.
"""
from __future__ import annotations

import json

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import ensure_vector_index


if __name__ == "__main__":
    db = get_db()
    result = ensure_vector_index(db)
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))

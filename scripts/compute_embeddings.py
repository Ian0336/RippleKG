"""Compute sentence embeddings and store them in ArangoDB.

Embeddings are optional. Install the extra before running this script:

    pip install -e ".[embeddings]"

Inside Docker:

    docker compose exec api pip install -e ".[embeddings]"
    docker compose exec api python scripts/compute_embeddings.py --doc-id doc0
"""
from __future__ import annotations

import argparse

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import EMBEDDING_MODEL, embedding_backend, store_embeddings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", help="Only compute embeddings for one document.")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    count = store_embeddings(db, doc_id=args.doc_id)
    scope = args.doc_id or "all documents"
    print(f"backend={embedding_backend()}")
    print(f"model={EMBEDDING_MODEL}")
    print(f"stored_embeddings={count}")
    print(f"scope={scope}")

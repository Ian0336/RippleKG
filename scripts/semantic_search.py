"""Search stored sentence embeddings for semantically related sentences.

This is an optional showcase script: embeddings are stored in ArangoDB on the
`sentences.embedding` field, then searched by cosine similarity.
"""
from __future__ import annotations

import argparse

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import semantic_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", required=True)
    parser.add_argument("--doc-id", help="Limit search to one document.")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--threshold",
        type=float,
        help="Only return sentences with similarity >= this value.",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="Use the exact Python scan instead of native ArangoDB vector search.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    rows = semantic_search(
        db,
        args.query,
        doc_id=args.doc_id,
        limit=args.limit,
        threshold=args.threshold,
        use_index=not args.no_index,
    )

    print(f"query={args.query}")
    print(f"doc_id={args.doc_id or '(all)'}")
    print()
    if not rows:
        print("No embedded sentences found. Run scripts/compute_embeddings.py first.")
        raise SystemExit(0)

    for i, row in enumerate(rows, start=1):
        print(f"{i}. {row['sent_id']} score={row['score']:.4f}")
        print(f"   {row['text']}")

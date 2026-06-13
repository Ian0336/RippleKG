"""Show how an LLM-generated relation label maps to the current KG schema."""
from __future__ import annotations

import argparse
import json

from ripplekg.db.client import get_db
from ripplekg.extraction.schema_merge import match_relation_schema, relation_ontology


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--relation", required=True)
    parser.add_argument("--threshold", type=float, default=0.82)
    parser.add_argument(
        "--use-embeddings",
        action="store_true",
        help="Also compare relation labels with optional sentence-transformers embeddings.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    candidates = relation_ontology(db)
    result = match_relation_schema(
        args.relation,
        candidates,
        threshold=args.threshold,
        use_embeddings=args.use_embeddings,
    )
    result["candidate_count"] = len(candidates)
    print(json.dumps(result, indent=2, ensure_ascii=False))

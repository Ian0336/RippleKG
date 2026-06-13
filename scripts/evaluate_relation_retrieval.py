"""Evaluate embedding retrieval using Re-DocRED relation evidence as gold labels.

Each query is generated from an existing KG triple:

    "<head> <relation> <tail>."

The sentence_supports_relation provenance edges provide the expected sentence
IDs. This is a reproducible proxy benchmark; review the exported cases because
dataset evidence labels and generated query wording are not always exhaustive.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import embedding_backend, semantic_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=50)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--scope", choices=["document", "global"], default="document")
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=[0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75],
    )
    parser.add_argument("--output", help="Optional JSON path for row-level review.")
    return parser.parse_args()


def load_cases(db: Any, limit: int) -> list[dict[str, Any]]:
    rows = list(db.aql.execute(
        """
        FOR relation IN relations
          FILTER relation.status == "active"
          LET head = DOCUMENT("entities", relation.head)
          LET tail = DOCUMENT("entities", relation.tail)
          LET evidence = (
            FOR edge IN sentence_supports_relation
              FILTER edge._to == relation._id AND edge.status == "active"
              SORT edge._from
              RETURN PARSE_IDENTIFIER(edge._from).key
          )
          FILTER head != null AND tail != null AND LENGTH(evidence) > 0
          LET doc_id = SPLIT(relation._key, ":")[0]
          SORT doc_id, relation._key
          RETURN {
            relation_id: relation._key,
            doc_id: doc_id,
            query: CONCAT(head.name, " ", relation.rel_type, " ", tail.name, "."),
            head: head.name,
            relation: relation.rel_type,
            tail: tail.name,
            expected_ids: evidence
          }
        """
    ))

    # Round-robin across documents so early documents cannot dominate the sample.
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_doc[row["doc_id"]].append(row)

    selected: list[dict[str, Any]] = []
    while len(selected) < limit:
        added = False
        for doc_id in sorted(by_doc):
            if by_doc[doc_id]:
                selected.append(by_doc[doc_id].pop(0))
                added = True
                if len(selected) >= limit:
                    break
        if not added:
            break
    return selected


def evaluate_case(db: Any, case: dict[str, Any], *, limit: int, scope: str) -> dict[str, Any]:
    results = semantic_search(
        db,
        case["query"],
        doc_id=case["doc_id"] if scope == "document" else None,
        limit=limit,
        threshold=None,
        use_index=False,
    )
    expected = set(case["expected_ids"])
    ranks = [
        rank
        for rank, row in enumerate(results, start=1)
        if row["sent_id"] in expected
    ]
    return {
        **case,
        "results": results,
        "first_relevant_rank": min(ranks) if ranks else None,
    }


def print_ranking_metrics(rows: list[dict[str, Any]]) -> None:
    count = len(rows)
    print("Ranking metrics")
    for k in (1, 3, 5, 10):
        hits = sum(
            row["first_relevant_rank"] is not None and row["first_relevant_rank"] <= k
            for row in rows
        )
        print(f"  Hit@{k:<2} = {hits / count:.3f} ({hits}/{count})")
    mrr = sum(
        1 / row["first_relevant_rank"] if row["first_relevant_rank"] else 0
        for row in rows
    ) / count
    print(f"  MRR    = {mrr:.3f}")


def print_threshold_metrics(rows: list[dict[str, Any]], thresholds: list[float]) -> None:
    print()
    print("Threshold metrics")
    print("threshold  precision  recall  f1  false_positives  false_negatives")
    for threshold in thresholds:
        tp = fp = fn = 0
        for row in rows:
            expected = set(row["expected_ids"])
            selected = {
                result["sent_id"]
                for result in row["results"]
                if result["score"] >= threshold
            }
            tp += len(selected & expected)
            fp += len(selected - expected)
            fn += len(expected - selected)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(f"{threshold:9.2f}  {precision:9.3f}  {recall:6.3f}  {f1:.3f}  {fp:15d}  {fn:15d}")


def main() -> None:
    args = parse_args()
    db = get_db()
    cases = load_cases(db, args.cases)
    if not cases:
        raise SystemExit("No active relation evidence found.")

    rows = [
        evaluate_case(db, case, limit=args.limit, scope=args.scope)
        for case in cases
    ]

    print(f"backend={embedding_backend()}")
    print(f"scope={args.scope}")
    print(f"cases={len(rows)}")
    print()
    print_ranking_metrics(rows)
    print_threshold_metrics(rows, args.thresholds)

    misses = [row for row in rows if row["first_relevant_rank"] is None]
    print()
    print(f"misses_within_top_{args.limit}={len(misses)}")
    for row in misses[:5]:
        print(f"  - {row['relation_id']}: {row['query']}")

    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"review_output={path}")


if __name__ == "__main__":
    main()

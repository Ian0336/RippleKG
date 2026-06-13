"""Evaluate semantic sentence selection on a small multi-document gold set."""
from __future__ import annotations

import argparse
from dataclasses import dataclass

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import embedding_backend, semantic_search


@dataclass(frozen=True)
class Case:
    doc_id: str
    query: str
    expected_ids: frozenset[str]


CASES = (
    Case(
        "doc0",
        "Schneider became the coach of the Russian skeleton team.",
        frozenset({"doc0:5"}),
    ),
    Case(
        "doc1",
        "After World War II, Alger earned an MBA from the University of Toronto.",
        frozenset({"doc1:4"}),
    ),
    Case(
        "doc2",
        "Four songs on the album were recorded live at venues in London and Toronto.",
        frozenset({"doc2:2"}),
    ),
    Case(
        "doc3",
        "Idriss's most popular composition was the Oscar-nominated Woody Woodpecker Song.",
        frozenset({"doc3:3"}),
    ),
    Case(
        "doc4",
        "ELAM students receive free tuition, housing, meals, and a small stipend.",
        frozenset({"doc4:4"}),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=[0.3, 0.4, 0.5, 0.6, 0.65, 0.7, 0.75],
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db = get_db()
    all_rows: list[tuple[Case, list[dict]]] = []

    print(f"backend={embedding_backend()}")
    print(f"cases={len(CASES)}")
    print()

    for case in CASES:
        rows = semantic_search(
            db,
            case.query,
            doc_id=case.doc_id,
            limit=args.limit,
            threshold=None,
            use_index=False,
        )
        all_rows.append((case, rows))
        expected_scores = [row["score"] for row in rows if row["sent_id"] in case.expected_ids]
        negative_scores = [row["score"] for row in rows if row["sent_id"] not in case.expected_ids]
        expected_score = max(expected_scores, default=float("nan"))
        strongest_negative = max(negative_scores, default=float("nan"))

        print(f"[{case.doc_id}] {case.query}")
        print(
            f"  expected_score={expected_score:.4f} "
            f"strongest_negative={strongest_negative:.4f} "
            f"margin={expected_score - strongest_negative:.4f}"
        )
        for rank, row in enumerate(rows, start=1):
            marker = "POS" if row["sent_id"] in case.expected_ids else "NEG"
            print(f"  {rank}. {marker} {row['sent_id']} score={row['score']:.4f}")
        print()

    print("Threshold sweep (sentence selection within each document)")
    print("threshold  precision  recall  f1  false_positives  false_negatives")
    for threshold in args.thresholds:
        tp = fp = fn = 0
        for case, rows in all_rows:
            selected = {row["sent_id"] for row in rows if row["score"] >= threshold}
            tp += len(selected & case.expected_ids)
            fp += len(selected - case.expected_ids)
            fn += len(case.expected_ids - selected)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        print(f"{threshold:9.2f}  {precision:9.3f}  {recall:6.3f}  {f1:.3f}  {fp:15d}  {fn:15d}")


if __name__ == "__main__":
    main()

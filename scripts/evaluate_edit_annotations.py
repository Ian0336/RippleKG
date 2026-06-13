"""Evaluate suggested labels and embedding retrieval against human annotations."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/edit_annotation_set.json")
    parser.add_argument("--top-k", type=int, nargs="+", default=[1, 3, 5])
    parser.add_argument(
        "--thresholds",
        type=float,
        nargs="+",
        default=[0.3, 0.4, 0.5, 0.6],
    )
    return parser.parse_args()


def metrics(predicted: set[str], expected: set[str]) -> tuple[float, float, float, int, int, int]:
    tp = len(predicted & expected)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1, tp, fp, fn


def print_metrics(label: str, predicted: set[str], expected: set[str]) -> None:
    precision, recall, f1, tp, fp, fn = metrics(predicted, expected)
    print(
        f"{label:<22} precision={precision:.3f} recall={recall:.3f} "
        f"f1={f1:.3f} tp={tp} fp={fp} fn={fn}"
    )


def candidate_key(case: dict[str, Any], candidate: dict[str, Any]) -> str:
    return f"{case['case_id']}::{candidate['sentence_id']}"


def main() -> None:
    args = parse_args()
    payload = json.loads(Path(args.path).read_text(encoding="utf-8"))
    cases = payload["cases"]
    candidates = [
        (case, candidate)
        for case in cases
        for candidate in case["candidates"]
    ]
    reviewed = [
        (case, candidate)
        for case, candidate in candidates
        if isinstance(candidate.get("human_should_edit"), bool)
    ]
    unreviewed = len(candidates) - len(reviewed)

    print(f"cases={len(cases)}")
    print(f"candidate_sentences={len(candidates)}")
    print(f"reviewed={len(reviewed)}")
    print(f"unreviewed={unreviewed}")
    if unreviewed:
        raise SystemExit("Complete all human_should_edit labels before evaluation.")

    expected = {
        candidate_key(case, candidate)
        for case, candidate in reviewed
        if candidate["human_should_edit"]
    }
    suggested = {
        candidate_key(case, candidate)
        for case, candidate in reviewed
        if candidate["suggested_should_edit"]
    }
    print()
    print_metrics("provenance suggestion", suggested, expected)

    for top_k in args.top_k:
        predicted = {
            candidate_key(case, candidate)
            for case, candidate in reviewed
            if candidate["retrieval_rank"] is not None
            and candidate["retrieval_rank"] <= top_k
        }
        print_metrics(f"embedding top-{top_k}", predicted, expected)

    for threshold in args.thresholds:
        predicted = {
            candidate_key(case, candidate)
            for case, candidate in reviewed
            if candidate["retrieval_score"] is not None
            and candidate["retrieval_score"] >= threshold
        }
        print_metrics(f"threshold {threshold:.2f}", predicted, expected)


if __name__ == "__main__":
    main()

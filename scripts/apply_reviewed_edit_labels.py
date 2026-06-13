"""Apply the reviewed labels for the generated 30-case annotation set.

This keeps the initial assisted review reproducible. The project owner should
still spot-check the labels before treating them as final human gold.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


SHOULD_EDIT: dict[str, set[str]] = {
    "replace-001": {"doc0:0"},
    "replace-002": {"doc1:1"},
    "replace-003": set(),
    "replace-004": {"doc4:0"},
    "replace-005": {"doc0:0"},
    "replace-006": set(),
    "replace-007": {"doc3:0"},
    "replace-008": {"doc4:0"},
    "replace-009": set(),
    "replace-010": {"doc1:1"},
    "replace-011": set(),
    "replace-012": {"doc4:5"},
    "replace-013": {"doc0:4"},
    "replace-014": set(),
    "replace-015": set(),
    "replace-016": {"doc4:5"},
    "replace-017": {"doc0:4"},
    "replace-018": {"doc1:0"},
    "replace-019": {"doc3:0"},
    "replace-020": {"doc4:5"},
    "replace-021": {"doc0:4"},
    "replace-022": {"doc1:3"},
    "replace-023": set(),
    "replace-024": {"doc0:4"},
    "replace-025": set(),
    "replace-026": set(),
    "replace-027": {"doc0:0"},
    "replace-028": {"doc1:3"},
    "replace-029": set(),
    "replace-030": {"doc0:2"},
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", default="data/edit_annotation_set.json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    path = Path(args.path)
    payload = json.loads(path.read_text(encoding="utf-8"))

    seen_cases = {case["case_id"] for case in payload["cases"]}
    if seen_cases != set(SHOULD_EDIT):
        raise SystemExit("Annotation cases changed; review labels again before applying.")

    true_count = 0
    for case in payload["cases"]:
        expected = SHOULD_EDIT[case["case_id"]]
        candidate_ids = {candidate["sentence_id"] for candidate in case["candidates"]}
        if not expected <= candidate_ids:
            raise SystemExit(f"Missing reviewed candidate in {case['case_id']}")
        for candidate in case["candidates"]:
            should_edit = candidate["sentence_id"] in expected
            candidate["human_should_edit"] = should_edit
            candidate["human_notes"] = (
                "The sentence states or structurally expresses the old fact."
                if should_edit
                else "The sentence does not assert the old fact strongly enough to require editing."
            )
            true_count += should_edit

    payload["metadata"]["annotation_status"] = "assistant_reviewed_pending_owner_spot_check"
    payload["metadata"]["annotation_rule_applied"] = (
        "True only when the sentence states or structurally expresses the old fact "
        "and would become stale when that fact is explicitly replaced."
    )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"labeled_cases={len(payload['cases'])}")
    print(f"should_edit_true={true_count}")
    print(f"output={path}")


if __name__ == "__main__":
    main()

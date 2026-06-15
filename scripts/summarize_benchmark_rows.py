#!/usr/bin/env python3
"""Summarize run_benchmark.py row-level JSONL by scenario.

First generate rows:
    docker compose exec api python scripts/run_benchmark.py --docs 50 --edits 100 --mode mixed --json --rows-jsonl data/baseline_rows.jsonl --rows-csv data/baseline_rows.csv > data/baseline_summary.json

Then run:
    docker compose exec api python scripts/summarize_benchmark_rows.py --input data/baseline_rows.jsonl --output data/scenario_summary.csv --json-output data/scenario_summary.json
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


def as_int(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return 0


def safe_ratio(a: float, b: float) -> float | None:
    return a / b if b else None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def get_counts(row: dict[str, Any], key: str) -> dict[str, int]:
    value = row.get(key, {})
    if not isinstance(value, dict):
        return {}
    return {str(k): as_int(v) for k, v in value.items()}


def summarize(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row.get("scenario", "unknown"))].append(row)

    out = []
    for scenario, items in sorted(groups.items()):
        deltas = {"added": 0, "removed": 0, "unchanged": 0}
        decisions = {"SKIP": 0, "PATCH": 0, "REBUILD": 0}

        affected_mentions = 0
        affected_relations = 0
        ours_cost = 0
        full_rebuild_cost = 0
        naive_stale_count = 0
        b1_reachable_count = 0
        document_rebuild_cost = 0
        whole_kg_rebuild_cost = 0
        marked_stale_count = 0
        refreshed_target_count = 0

        for row in items:
            dc = get_counts(row, "delta_counts")
            for k in deltas:
                deltas[k] += as_int(dc.get(k))

            mc = get_counts(row, "decision_counts")
            for k in decisions:
                decisions[k] += as_int(mc.get(k))

            affected_mentions += as_int(row.get("affected_mentions"))
            affected_relations += as_int(row.get("affected_relations"))
            ours_cost += as_int(row.get("ours_cost"))
            full_rebuild_cost += as_int(row.get("full_rebuild_cost"))
            naive_stale_count += as_int(row.get("naive_stale_count"))
            b1_reachable_count += as_int(row.get("b1_reachable_count"))
            document_rebuild_cost += as_int(row.get("document_rebuild_cost"))
            whole_kg_rebuild_cost += as_int(row.get("whole_kg_rebuild_cost"))

            marked = row.get("marked_stale", [])
            refreshed = row.get("refreshed_targets", [])
            marked_stale_count += len(marked) if isinstance(marked, list) else 0
            refreshed_target_count += len(refreshed) if isinstance(refreshed, list) else 0

        total_decisions = sum(decisions.values())
        out.append({
            "scenario": scenario,
            "edits": len(items),
            "affected_mentions": affected_mentions,
            "affected_relations": affected_relations,

            "m1_added": deltas["added"],
            "m1_removed": deltas["removed"],
            "m1_unchanged": deltas["unchanged"],

            "m2_SKIP": decisions["SKIP"],
            "m2_PATCH": decisions["PATCH"],
            "m2_REBUILD": decisions["REBUILD"],
            "skip_rate": safe_ratio(decisions["SKIP"], total_decisions),
            "patch_rate": safe_ratio(decisions["PATCH"], total_decisions),
            "rebuild_rate": safe_ratio(decisions["REBUILD"], total_decisions),

            "ours_cost": ours_cost,
            "full_rebuild_cost": full_rebuild_cost,
            "cost_reduction_vs_affected_full": (
                1 - ours_cost / full_rebuild_cost if full_rebuild_cost else None
            ),

            "naive_stale_count": naive_stale_count,
            "b1_reachable_count": b1_reachable_count,
            "ours_marked_stale_count": marked_stale_count,
            "refreshed_target_count": refreshed_target_count,

            "document_rebuild_cost": document_rebuild_cost,
            "whole_kg_rebuild_cost": whole_kg_rebuild_cost,
            "document_over_ours": safe_ratio(document_rebuild_cost, ours_cost),
            "whole_kg_over_ours": safe_ratio(whole_kg_rebuild_cost, ours_cost),
        })

    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="data/scenario_summary.csv")
    parser.add_argument("--json-output", default="data/scenario_summary.json")
    args = parser.parse_args()

    rows = read_jsonl(Path(args.input))
    summary = summarize(rows)

    write_csv(Path(args.output), summary)
    Path(args.json_output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.json_output).write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"read {len(rows)} rows")
    print(f"wrote {args.output}")
    print(f"wrote {args.json_output}")


if __name__ == "__main__":
    main()

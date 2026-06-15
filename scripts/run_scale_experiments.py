#!/usr/bin/env python3
"""Run RippleKG benchmark across multiple corpus sizes.

Uses the existing main-branch benchmark:

    scripts/run_benchmark.py --docs X --edits N --mode mixed --json

Outputs:
    data/scale_experiments.csv
    data/scale_experiments.json

Windows CMD via Docker:
    docker compose exec api python scripts/run_scale_experiments.py --docs-list 5,10,25,50,100 --edits 100 --mode mixed --output data/scale_experiments.csv --json-output data/scale_experiments.json
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


def parse_docs_list(text: str) -> list[int]:
    docs = []
    for part in text.split(","):
        part = part.strip()
        if part:
            docs.append(int(part))
    if not docs:
        raise ValueError("--docs-list must contain at least one integer")
    return docs


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def extract_json(stdout: str) -> dict[str, Any]:
    text = stdout.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end < start:
            raise
        return json.loads(text[start : end + 1])


def run_benchmark(
    docs: int,
    edits: int,
    mode: str,
    benchmark_script: str,
    data_path: str,
    keep_rows: bool,
    rows_dir: Path,
) -> tuple[dict[str, Any], str]:
    cmd = [
        sys.executable,
        benchmark_script,
        "--path",
        data_path,
        "--docs",
        str(docs),
        "--edits",
        str(edits),
        "--mode",
        mode,
        "--json",
    ]

    if keep_rows:
        rows_dir.mkdir(parents=True, exist_ok=True)
        cmd.extend([
            "--rows-jsonl",
            str(rows_dir / f"scale_docs{docs}_rows.jsonl"),
            "--rows-csv",
            str(rows_dir / f"scale_docs{docs}_rows.csv"),
        ])

    print("+ " + " ".join(cmd), flush=True)
    proc = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    return extract_json(proc.stdout), proc.stdout


def flatten_summary(summary: dict[str, Any]) -> dict[str, Any]:
    cost = summary.get("cost", {})
    over = summary.get("over_invalidation", {})
    consistency = summary.get("b0_consistency", {})
    deltas = summary.get("deltas", {})
    decisions = summary.get("decisions", {})

    ours_cost = summary.get("ours_cost", cost.get("ours_sentence", 0)) or 0
    full_rebuild_cost = summary.get("full_rebuild_cost", 0) or 0
    document_rebuild = cost.get("document_rebuild", 0) or 0
    whole_kg_rebuild = cost.get("whole_kg_rebuild", 0) or 0

    return {
        "docs": summary.get("doc_limit"),
        "docs_loaded": summary.get("docs_loaded"),
        "edits_per_scenario": summary.get("edit_limit"),
        "mode": summary.get("mode"),
        "total_edits": summary.get("edits"),
        "affected_mentions": summary.get("affected_mentions"),
        "affected_relations": summary.get("affected_relations"),

        "m1_added": deltas.get("added", 0),
        "m1_removed": deltas.get("removed", 0),
        "m1_unchanged": deltas.get("unchanged", 0),

        "m2_SKIP": decisions.get("SKIP", 0),
        "m2_PATCH": decisions.get("PATCH", 0),
        "m2_REBUILD": decisions.get("REBUILD", 0),

        "ours_cost": ours_cost,
        "affected_full_rebuild_cost": full_rebuild_cost,
        "affected_full_rebuild_reduction": (
            1 - ours_cost / full_rebuild_cost if full_rebuild_cost else None
        ),

        "document_rebuild_cost": document_rebuild,
        "whole_kg_rebuild_cost": whole_kg_rebuild,
        "document_over_ours": cost.get("document_over_ours"),
        "whole_kg_over_ours": cost.get("whole_kg_over_ours"),

        "ours_marked_stale": over.get("ours_marked_stale"),
        "b1_generic_reachable": over.get("b1_generic_reachable"),
        "b2_naive_stale": over.get("b2_naive_stale"),
        "b1_over_ours": over.get("b1_over_ours"),
        "b2_over_ours": over.get("b2_over_ours"),

        "b0_consistent": consistency.get("consistent"),
        "b0_mismatches": consistency.get("mismatches"),
        "checked_relations": consistency.get("checked_relations"),
        "checked_entities": consistency.get("checked_entities"),
        "evidence_free_relations": consistency.get("evidence_free_relations"),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    ensure_parent(path)
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
    parser.add_argument("--docs-list", default="5,10,25,50,100")
    parser.add_argument("--edits", type=int, default=100)
    parser.add_argument("--mode", choices=["noop", "mixed"], default="mixed")
    parser.add_argument("--path", default="data/docred/dev_revised.json")
    parser.add_argument("--benchmark-script", default="scripts/run_benchmark.py")
    parser.add_argument("--output", default="data/scale_experiments.csv")
    parser.add_argument("--json-output", default="data/scale_experiments.json")
    parser.add_argument("--stdout-dir", default="data/scale_stdout")
    parser.add_argument("--rows-dir", default="data/scale_rows")
    parser.add_argument("--keep-rows", action="store_true")
    args = parser.parse_args()

    docs_list = parse_docs_list(args.docs_list)
    rows = []
    raw = []

    stdout_dir = Path(args.stdout_dir)
    stdout_dir.mkdir(parents=True, exist_ok=True)
    rows_dir = Path(args.rows_dir)

    for docs in docs_list:
        summary, stdout = run_benchmark(
            docs=docs,
            edits=args.edits,
            mode=args.mode,
            benchmark_script=args.benchmark_script,
            data_path=args.path,
            keep_rows=args.keep_rows,
            rows_dir=rows_dir,
        )
        (stdout_dir / f"scale_docs{docs}_stdout.txt").write_text(stdout, encoding="utf-8")
        raw.append(summary)
        rows.append(flatten_summary(summary))

    write_csv(Path(args.output), rows)
    ensure_parent(Path(args.json_output))
    Path(args.json_output).write_text(json.dumps(raw, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"wrote {args.output}")
    print(f"wrote {args.json_output}")


if __name__ == "__main__":
    main()

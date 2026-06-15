#!/usr/bin/env python3
"""Run benchmark and collect wall-clock time plus DB maintenance-log overhead.

Command:
    docker compose exec api python scripts/run_timed_benchmark.py --docs 50 --edits 100 --mode mixed --output data/timed_benchmark.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    from arango import ArangoClient
except Exception:
    ArangoClient = None  # type: ignore


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


def connect_db() -> Any:
    if ArangoClient is None:
        raise RuntimeError("python-arango not importable. Run inside the api container.")

    url = os.environ.get("ARANGO_URL", "http://arangodb:8529")
    db_name = os.environ.get("ARANGO_DB", "ripplekg")
    user = os.environ.get("ARANGO_USER", "root")
    password = os.environ.get("ARANGO_PASSWORD") or os.environ.get("ARANGO_ROOT_PASSWORD", "ripplekg-dev")

    client = ArangoClient(hosts=url)
    return client.db(db_name, username=user, password=password)


def count_collection(db: Any, name: str) -> int:
    if not db.has_collection(name):
        return 0
    return int(db.collection(name).count())


def run_benchmark(args: argparse.Namespace) -> tuple[dict[str, Any], str, float]:
    ensure_parent(Path(args.rows_jsonl))
    cmd = [
        sys.executable,
        args.benchmark_script,
        "--path",
        args.path,
        "--docs",
        str(args.docs),
        "--edits",
        str(args.edits),
        "--mode",
        args.mode,
        "--json",
        "--rows-jsonl",
        args.rows_jsonl,
        "--rows-csv",
        args.rows_csv,
    ]

    print("+ " + " ".join(cmd), flush=True)
    t0 = time.perf_counter()
    proc = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    elapsed = time.perf_counter() - t0

    summary = extract_json(proc.stdout)
    return summary, proc.stdout, elapsed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--docs", type=int, default=50)
    parser.add_argument("--edits", type=int, default=100)
    parser.add_argument("--mode", choices=["noop", "mixed"], default="mixed")
    parser.add_argument("--path", default="data/docred/dev_revised.json")
    parser.add_argument("--benchmark-script", default="scripts/run_benchmark.py")
    parser.add_argument("--output", default="data/timed_benchmark.json")
    parser.add_argument("--stdout-output", default="data/timed_benchmark_stdout.txt")
    parser.add_argument("--rows-jsonl", default="data/timed_benchmark_rows.jsonl")
    parser.add_argument("--rows-csv", default="data/timed_benchmark_rows.csv")
    args = parser.parse_args()

    summary, stdout, elapsed = run_benchmark(args)

    db = connect_db()
    evidence_delta_records = count_collection(db, "evidence_deltas")
    refresh_decision_records = count_collection(db, "refresh_decisions")
    entities = count_collection(db, "entities")
    relations = count_collection(db, "relations")
    sentences = count_collection(db, "sentences")
    mentions = count_collection(db, "mentions")
    supports = count_collection(db, "sentence_supports_relation")

    edits = summary.get("edits", 0) or 0
    ours_cost = summary.get("ours_cost", 0) or 0
    full_rebuild_cost = summary.get("full_rebuild_cost", 0) or 0
    cost = summary.get("cost", {}) or {}
    over = summary.get("over_invalidation", {}) or {}
    consistency = summary.get("b0_consistency", {}) or {}

    result = {
        "settings": {
            "docs": args.docs,
            "edits_per_scenario": args.edits,
            "mode": args.mode,
        },
        "runtime": {
            "elapsed_sec": elapsed,
            "total_edits": edits,
            "ms_per_edit": (elapsed * 1000 / edits) if edits else None,
        },
        "maintenance_logs": {
            "evidence_delta_records": evidence_delta_records,
            "refresh_decision_records": refresh_decision_records,
            "total_log_records": evidence_delta_records + refresh_decision_records,
            "log_records_per_edit": (
                (evidence_delta_records + refresh_decision_records) / edits
                if edits else None
            ),
        },
        "db_artifacts_after_run": {
            "sentences": sentences,
            "entities": entities,
            "relations": relations,
            "mentions": mentions,
            "sentence_supports_relation": supports,
        },
        "cost": {
            "ours_cost": ours_cost,
            "affected_full_rebuild_cost": full_rebuild_cost,
            "affected_full_rebuild_reduction": (
                1 - ours_cost / full_rebuild_cost if full_rebuild_cost else None
            ),
            "document_rebuild_cost": cost.get("document_rebuild"),
            "whole_kg_rebuild_cost": cost.get("whole_kg_rebuild"),
            "document_over_ours": cost.get("document_over_ours"),
            "whole_kg_over_ours": cost.get("whole_kg_over_ours"),
        },
        "over_invalidation": over,
        "b0_consistency": consistency,
        "raw_summary": summary,
    }

    ensure_parent(Path(args.output))
    Path(args.output).write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    Path(args.stdout_output).write_text(stdout, encoding="utf-8")

    print(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"wrote {args.output}")
    print(f"wrote {args.stdout_output}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Collect final-report table inputs into one compact Markdown file.

Command:
  docker compose exec api python scripts/collect_final_tables.py --output data/final_tables.md
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def read_json(path: Path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def md_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return "_No data found._\n"
    lines = []
    lines.append("| " + " | ".join(columns) + " |")
    lines.append("| " + " | ".join(["---"] * len(columns)) + " |")
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(c, "")) for c in columns) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", default="data/scale_experiments.csv")
    parser.add_argument("--scenario", default="data/scenario_summary.csv")
    parser.add_argument("--timed", default="data/timed_benchmark.json")
    parser.add_argument("--output", default="data/final_tables.md")
    args = parser.parse_args()

    scale = read_csv(Path(args.scale))
    scenario = read_csv(Path(args.scenario))
    timed = read_json(Path(args.timed))

    parts = []
    parts.append("# RippleKG final experiment tables\n")

    parts.append("## Scaling experiment\n")
    parts.append(md_table(scale, [
        "docs", "total_edits", "ours_cost", "document_rebuild_cost",
        "whole_kg_rebuild_cost", "document_over_ours", "whole_kg_over_ours",
        "b0_consistent", "b0_mismatches"
    ]))

    parts.append("\n## Scenario breakdown\n")
    parts.append(md_table(scenario, [
        "scenario", "edits", "m1_added", "m1_removed", "m1_unchanged",
        "m2_SKIP", "m2_PATCH", "m2_REBUILD", "ours_cost",
        "naive_stale_count", "document_rebuild_cost", "whole_kg_rebuild_cost"
    ]))

    parts.append("\n## Timing / log overhead\n")
    if timed:
        parts.append("```json\n")
        parts.append(json.dumps({
            "runtime": timed.get("runtime"),
            "maintenance_logs": timed.get("maintenance_logs"),
            "cost": timed.get("cost"),
            "b0_consistency": timed.get("b0_consistency"),
        }, indent=2, ensure_ascii=False))
        parts.append("\n```\n")
    else:
        parts.append("_No timing data found._\n")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

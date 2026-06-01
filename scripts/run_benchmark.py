"""Run a multi-document semantic no-op benchmark.

This validates the main M1/M2 path on many sentences, not only demo.json.
For each selected evidence sentence, the script uses its current active
relations as intended triples and applies a whitespace-only text edit. Ours
should SKIP unchanged evidence, while the B2 naive baseline would mark reachable
objects stale.
"""
import argparse
import csv
import json

from ripplekg.db.client import get_db
from ripplekg.eval.benchmark import run_mixed_benchmark, run_semantic_noop_benchmark, summarize_rows
from ripplekg.ingest.loader import ingest_dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="data/docred/dev_revised.json")
    parser.add_argument("--docs", type=int, default=50)
    parser.add_argument("--edits", type=int, default=100)
    parser.add_argument(
        "--mode",
        choices=["noop", "mixed"],
        default="noop",
        help="noop validates many semantic no-op edits; mixed also exercises remove/change scenarios.",
    )
    parser.add_argument("--json", action="store_true", help="Print JSON summary only.")
    parser.add_argument("--show-rows", action="store_true", help="Print row-level audit details.")
    parser.add_argument("--rows-jsonl", help="Write row-level audit records as JSONL.")
    parser.add_argument("--rows-csv", help="Write row-level audit records as CSV.")
    parser.add_argument(
        "--skip-ingest",
        action="store_true",
        help="Use the current DB state instead of reloading T0.",
    )
    return parser.parse_args()


def print_summary(summary: dict) -> None:
    print("Benchmark summary")
    print("-----------------")
    print(f"edits: {summary['edits']}")
    print(f"affected_mentions: {summary['affected_mentions']}")
    print(f"affected_relations: {summary['affected_relations']}")
    print(
        "deltas: "
        f"added={summary['deltas']['added']} "
        f"removed={summary['deltas']['removed']} "
        f"unchanged={summary['deltas']['unchanged']}"
    )
    print(
        "decisions: "
        f"SKIP={summary['decisions']['SKIP']} "
        f"PATCH={summary['decisions']['PATCH']} "
        f"REBUILD={summary['decisions']['REBUILD']}"
    )
    print(f"ours_cost: {summary['ours_cost']}")
    print(f"full_rebuild_cost: {summary['full_rebuild_cost']}")
    print(f"b2_naive_stale_count: {summary['naive_stale_count']}")
    if summary.get("by_scenario"):
        print()
        print("By scenario")
        print("-----------")
        for scenario, item in summary["by_scenario"].items():
            print(
                f"{scenario}: edits={item['edits']} "
                f"added={item['deltas']['added']} "
                f"removed={item['deltas']['removed']} "
                f"unchanged={item['deltas']['unchanged']} "
                f"SKIP={item['decisions']['SKIP']} "
                f"PATCH={item['decisions']['PATCH']} "
                f"REBUILD={item['decisions']['REBUILD']} "
                f"cost={item['ours_cost']} "
                f"naive_stale={item['naive_stale_count']}"
            )


def print_rows(rows) -> None:
    print()
    print("Row-level audit")
    print("---------------")
    for row in rows:
        print(f"[{row.scenario}] step={row.step} sent={row.sent_id}")
        print(f"old: {row.old_text}")
        print(f"new: {row.new_text}")
        if row.removed_from_intended or row.added_to_intended:
            print("original_triples:")
            for head, rel, tail in row.original_triples:
                print(f"  - {head} | {rel} | {tail}")
        if row.removed_from_intended:
            print("removed_from_intended:")
            for head, rel, tail in row.removed_from_intended:
                print(f"  - {head} | {rel} | {tail}")
        if row.added_to_intended:
            print("added_to_intended:")
            for head, rel, tail in row.added_to_intended:
                print(f"  - {head} | {rel} | {tail}")
        print("intended_triples:")
        for head, rel, tail in row.intended_triples:
            print(f"  - {head} | {rel} | {tail}")
        print(
            "result: "
            f"delta={row.delta_counts} "
            f"decision={row.decision_counts} "
            f"ours_cost={row.ours_cost} "
            f"naive_stale={row.naive_stale_count}"
        )
        changed = [item for item in row.evidence_audit if item["delta_type"] != "unchanged"]
        if changed:
            print("changed_evidence:")
            for item in changed:
                print(
                    f"  - {item['delta_type']} {item['scope']} "
                    f"{item['target_id']}: {item['triple']} "
                    f"reason={item['reason']}"
                )
        actions = [item for item in row.decision_audit if item["decision"] != "SKIP"]
        if actions:
            print("refresh_actions:")
            for item in actions:
                after = item["after_refresh"]
                if item["target_type"] == "relation":
                    label = f"{after.get('head')} | {after.get('rel_type')} | {after.get('tail')}"
                    state = (
                        f"evidence_count={after.get('evidence_count')} "
                        f"freshness={after.get('freshness_status')} "
                        f"status={after.get('status')}"
                    )
                else:
                    label = after.get("label")
                    state = (
                        f"evidence_count={after.get('evidence_count')} "
                        f"freshness={after.get('freshness_status')}"
                    )
                print(
                    f"  - {item['decision']} {item['target_type']} "
                    f"{item['target_id']}: {label} "
                    f"cost={item['cost']} {state} "
                    f"reason={item['reason']}"
                )
        print()


def write_rows_jsonl(path: str, rows) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row.as_dict(), ensure_ascii=False) + "\n")


def write_rows_csv(path: str, rows) -> None:
    fieldnames = [
        "step",
        "sent_id",
        "scenario",
        "old_text",
        "new_text",
        "original_triples",
        "intended_triples",
        "removed_from_intended",
        "added_to_intended",
        "affected_mentions",
        "affected_relations",
        "delta_counts",
        "decision_counts",
        "evidence_audit",
        "decision_audit",
        "marked_stale",
        "refreshed_targets",
        "ours_cost",
        "full_rebuild_cost",
        "naive_stale_count",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            item = row.as_dict()
            item["original_triples"] = json.dumps(item["original_triples"], ensure_ascii=False)
            item["intended_triples"] = json.dumps(item["intended_triples"], ensure_ascii=False)
            item["removed_from_intended"] = json.dumps(
                item["removed_from_intended"], ensure_ascii=False
            )
            item["added_to_intended"] = json.dumps(item["added_to_intended"], ensure_ascii=False)
            item["delta_counts"] = json.dumps(item["delta_counts"], ensure_ascii=False)
            item["decision_counts"] = json.dumps(item["decision_counts"], ensure_ascii=False)
            item["evidence_audit"] = json.dumps(item["evidence_audit"], ensure_ascii=False)
            item["decision_audit"] = json.dumps(item["decision_audit"], ensure_ascii=False)
            item["marked_stale"] = json.dumps(item["marked_stale"], ensure_ascii=False)
            item["refreshed_targets"] = json.dumps(item["refreshed_targets"], ensure_ascii=False)
            writer.writerow(item)


if __name__ == "__main__":
    args = parse_args()
    db = get_db()

    if not args.skip_ingest:
        docs = ingest_dataset(db, args.path, limit=args.docs)
    else:
        docs = None

    if args.mode == "mixed":
        rows = run_mixed_benchmark(db, per_scenario=args.edits)
    else:
        rows = run_semantic_noop_benchmark(db, limit=args.edits)
    summary = summarize_rows(rows)
    summary.update({
        "docs_loaded": docs,
        "doc_limit": args.docs,
        "edit_limit": args.edits,
        "mode": args.mode,
    })

    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        if docs is not None:
            print(f"ingested {docs} docs from {args.path}")
            print()
        print_summary(summary)
        if args.show_rows:
            print_rows(rows)

    if args.rows_jsonl:
        write_rows_jsonl(args.rows_jsonl, rows)
    if args.rows_csv:
        write_rows_csv(args.rows_csv, rows)

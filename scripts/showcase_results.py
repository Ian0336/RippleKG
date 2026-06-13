"""Print PPT-ready end-to-end RippleKG showcase results.

This script is intentionally more compact than the raw JSON returned by
run_edit.py. Each case reloads a clean T0 graph so the metrics are easy to
explain in slides.

Usage:
  docker compose exec api python scripts/showcase_results.py
"""
from __future__ import annotations

import argparse
from collections import Counter

from ripplekg.baselines import naive
from ripplekg.db import schema
from ripplekg.db.client import get_db
from ripplekg.edits.store import load_edits
from ripplekg.eval.metrics import summarize
from ripplekg.ingest.loader import ingest_dataset
from ripplekg.mechanism.pipeline import run_edit_transactional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="data/docred/dev_revised.json")
    parser.add_argument("--edits", default="data/edits/demo.json")
    parser.add_argument("--docs", type=int, default=5)
    return parser.parse_args()


def reset_t0(db, path: str, docs: int) -> None:
    schema.drop_schema(db)
    schema.init_schema(db)
    ingest_dataset(db, path, limit=docs)


def collection_counts(db) -> dict[str, int]:
    names = [
        "documents",
        "sentences",
        "entities",
        "relations",
        "mentions",
        "sentence_supports_relation",
        "evidence_deltas",
        "refresh_decisions",
    ]
    return {name: db.collection(name).count() for name in names}


def pct_reduction(actual: int, nominal: int) -> float:
    if nominal == 0:
        return 0.0
    return round((1 - actual / nominal) * 100, 2)


def print_header(title: str) -> None:
    print()
    print("=" * 80)
    print(title)
    print("=" * 80)


def short_triple(delta) -> str:
    payload = delta.triple
    if delta.scope == "relation":
        return f"{payload.get('head')} --{payload.get('rel_type')}--> {payload.get('tail')}"
    return f"mention: {payload.get('entity')}"


def print_case_result(title: str, result, metrics: dict) -> None:
    delta_counts = Counter((item.delta_type, item.scope) for item in result.evidence_delta)
    decision_counts = Counter(item.decision for item in result.decisions)
    actual = metrics["cost"]["actual"]
    nominal = metrics["cost"]["full_rebuild_nominal"]

    print_header(title)
    print(f"Sentence: {result.edit['doc_id']}:{result.edit['sent_idx']}")
    print(f"Refresh mode: {result.edit['refresh_mode']}")
    print()
    print("Before:")
    print(result.edit["old_text"])
    print()
    print("After:")
    print(result.edit["new_text"])
    print()
    print("M1 Evidence Delta")
    print(f"  added     mentions={delta_counts[('added', 'mention')]} relations={delta_counts[('added', 'relation')]}")
    print(f"  removed   mentions={delta_counts[('removed', 'mention')]} relations={delta_counts[('removed', 'relation')]}")
    print(f"  unchanged mentions={delta_counts[('unchanged', 'mention')]} relations={delta_counts[('unchanged', 'relation')]}")
    print()
    print("Representative changed relation evidence")
    changed_relations = [
        item for item in result.evidence_delta
        if item.scope == "relation" and item.delta_type in {"added", "removed"}
    ]
    if changed_relations:
        for item in changed_relations[:6]:
            print(f"  {item.delta_type.upper():7} {short_triple(item)}")
    else:
        print("  (none; relation evidence is unchanged)")
    print()
    print("M2 Decisions")
    print(f"  SKIP={decision_counts['SKIP']} PATCH={decision_counts['PATCH']} REBUILD={decision_counts['REBUILD']}")
    print()
    print("Cost")
    print(f"  incremental_cost={actual}")
    print(f"  nominal_full_rebuild_cost={nominal}")
    print(f"  reduction={pct_reduction(actual, nominal)}%")
    print()
    print("Freshness")
    print(f"  marked_stale={len(result.freshness['marked_stale'])}")
    print(f"  refreshed={len(result.freshness['refreshed'])}")
    print(f"  stale_after_step={metrics['stale']}")


def print_naive_baseline(db, edit, step: int) -> None:
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    baseline = naive.invalidate_sentence(db, sent_id, step=step, dry_run=True)
    print()
    print("Naive sentence invalidation baseline")
    print(f"  sentence={sent_id}")
    print(f"  would_mark_stale={baseline['stale_count']}")
    print(f"  nominal_cost={baseline['cost']}")


def print_db_checklist(step: int, sent_id: str) -> None:
    print()
    print("ArangoDB checklist for screenshots")
    print(f"  sentences: _key == '{sent_id}'")
    print(f"  evidence_deltas: step == {step}")
    print(f"  refresh_decisions: step == {step}")
    print("  relations/entities: freshness_status and evidence_count")


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    edits = load_edits(args.edits)

    print_header("T0 Graph Load")
    reset_t0(db, args.path, args.docs)
    for name, count in collection_counts(db).items():
        print(f"{name}: {count}")

    # Case A: paraphrase, same evidence, should SKIP.
    reset_t0(db, args.path, args.docs)
    paraphrase = edits[1]
    print_naive_baseline(db, paraphrase, step=1)
    paraphrase_result = run_edit_transactional(db, paraphrase, step=1, refresh_mode="immediate")
    print_case_result(
        "Case A: Paraphrase edit -> evidence unchanged -> SKIP",
        paraphrase_result,
        summarize(db, step=1),
    )
    print_db_checklist(step=1, sent_id=f"{paraphrase.doc_id}:{paraphrase.sent_idx}")

    # Case B: factual change, added/removed evidence, should PATCH/REBUILD.
    reset_t0(db, args.path, args.docs)
    factual_change = edits[0]
    print_naive_baseline(db, factual_change, step=2)
    change_result = run_edit_transactional(db, factual_change, step=2, refresh_mode="immediate")
    print_case_result(
        "Case B: Factual change -> evidence changed -> PATCH/REBUILD",
        change_result,
        summarize(db, step=2),
    )
    print_db_checklist(step=2, sent_id=f"{factual_change.doc_id}:{factual_change.sent_idx}")

    print_header("One-line PPT takeaway")
    print(
        "RippleKG compares sentence-supported evidence triples, so paraphrases are "
        "skipped while factual changes only patch/rebuild affected KG objects."
    )

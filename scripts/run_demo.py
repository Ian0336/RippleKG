"""Run a compact end-to-end RippleKG demo.

The output is designed for reports/slides:
  1. Load T0.
  2. Run the paraphrase edit where ours SKIPs but B2 naive would invalidate.
  3. Run the relation-changing edit in deferred mode, then tick refresh.
"""
import argparse

from ripplekg.baselines import naive
from ripplekg.db.client import get_db
from ripplekg.edits.store import load_edits
from ripplekg.eval.metrics import summarize
from ripplekg.ingest.loader import ingest_dataset
from ripplekg.mechanism.pipeline import run_edit_transactional
from ripplekg.mechanism.refresh import apply_refreshes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", default="data/docred/dev_revised.json")
    parser.add_argument("--edits", default="data/edits/demo.json")
    parser.add_argument("--docs", type=int, default=5)
    return parser.parse_args()


def collection_counts(db) -> dict[str, int]:
    names = [
        "documents",
        "sentences",
        "entities",
        "relations",
        "mentions",
        "sentence_supports_relation",
    ]
    return {name: db.collection(name).count() for name in names}


def print_counts(counts: dict[str, int]) -> None:
    print("T0 loaded")
    print("---------")
    for name, count in counts.items():
        print(f"{name}: {count}")


def print_result(title: str, result, metrics: dict) -> None:
    print()
    print(title)
    print("-" * len(title))
    print(f"sentence: {result.edit['doc_id']}:{result.edit['sent_idx']}")
    print(f"refresh_mode: {result.edit['refresh_mode']}")
    print(
        "M1 deltas: "
        f"added={metrics['evidence_delta']['added']} "
        f"removed={metrics['evidence_delta']['removed']} "
        f"unchanged={metrics['evidence_delta']['unchanged']}"
    )
    print(
        "M2 decisions: "
        f"SKIP={metrics['decisions']['SKIP']} "
        f"PATCH={metrics['decisions']['PATCH']} "
        f"REBUILD={metrics['decisions']['REBUILD']}"
    )
    print(
        "cost: "
        f"actual={metrics['cost']['actual']} "
        f"full_rebuild_nominal={metrics['cost']['full_rebuild_nominal']}"
    )
    print(
        "stale: "
        f"entities={metrics['stale']['entities']} "
        f"relations={metrics['stale']['relations']}"
    )


def print_naive_comparison(db, edit, step: int) -> None:
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    baseline = naive.invalidate_sentence(db, sent_id, step=step, dry_run=True)
    print()
    print("B2 naive comparison")
    print("-------------------")
    print(f"sentence: {sent_id}")
    print(f"would_mark_stale: {baseline['stale_count']}")
    print(f"nominal_cost: {baseline['cost']}")


if __name__ == "__main__":
    args = parse_args()
    db = get_db()

    ingest_dataset(db, args.path, limit=args.docs)
    edits = load_edits(args.edits)

    print_counts(collection_counts(db))

    paraphrase = edits[1]
    print_naive_comparison(db, paraphrase, step=1)
    paraphrase_result = run_edit_transactional(db, paraphrase, step=1, refresh_mode="immediate")
    print_result("Ours: edit 2 paraphrase", paraphrase_result, summarize(db, step=1))

    ingest_dataset(db, args.path, limit=args.docs)
    relation_change = edits[0]
    change_result = run_edit_transactional(db, relation_change, step=2, refresh_mode="deferred")
    print_result("Ours: edit 1 relation change before tick", change_result, summarize(db, step=2))

    tick = apply_refreshes(db, step=2)
    after_tick = summarize(db, step=2)
    print()
    print("Refresh tick")
    print("------------")
    print(f"refreshed: {len(tick['refreshed'])}")
    print(
        "stale_after_tick: "
        f"entities={after_tick['stale']['entities']} "
        f"relations={after_tick['stale']['relations']}"
    )

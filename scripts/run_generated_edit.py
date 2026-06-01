"""Generate an EditOp from an instruction, then run M1/M2.

This is the A/B/C end-to-end path:

    doc_id + sent_idx + instruction
      -> extractor/editor provider
      -> EditOp(new_text, intended_triples)
      -> ArangoDB-backed M1/M2 pipeline

Use ``--provider heuristic`` for offline deterministic runs, ``--provider anthropic``
with ANTHROPIC_API_KEY, or ``--provider openai`` with OPENAI_API_KEY.
"""
import argparse
import json

from ripplekg.db.client import get_db
from ripplekg.extraction import build_edit_from_instruction
from ripplekg.mechanism.pipeline import run_edit_transactional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--sent-idx", type=int, required=True)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--provider",
        choices=["heuristic", "anthropic", "openai"],
        default="heuristic",
        help="heuristic runs offline; anthropic/openai call the configured LLM provider.",
    )
    parser.add_argument(
        "--refresh-mode",
        choices=["immediate", "deferred"],
        default="immediate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the generated EditOp but do not apply M1/M2.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    db = get_db()
    edit = build_edit_from_instruction(
        db,
        doc_id=args.doc_id,
        sent_idx=args.sent_idx,
        instruction=args.instruction,
        provider=args.provider,
    )

    print("Generated EditOp")
    print(json.dumps(edit.model_dump(), indent=2, ensure_ascii=False))

    if args.dry_run:
        raise SystemExit(0)

    result = run_edit_transactional(
        db,
        edit,
        step=args.step,
        refresh_mode=args.refresh_mode,
    )
    print()
    print("M1/M2 result")
    print(result.model_dump_json(indent=2))

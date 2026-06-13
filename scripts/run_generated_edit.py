"""Generate an EditOp from an instruction, then run M1/M2.

This is the A/B/C end-to-end path:

    doc_id + sent_idx + instruction/fact
      -> extractor/editor provider
      -> one EditOp(new_text, intended_triples)
      -> ArangoDB-backed M1/M2 pipeline

or:

    doc_id + instruction/fact + --scope document
      -> related evidence sentences in the document
      -> multiple EditOps(new_text, intended_triples)
      -> ArangoDB-backed M1/M2 pipeline

Use ``--provider heuristic`` for offline deterministic runs, ``--provider anthropic``
with ANTHROPIC_API_KEY, or ``--provider openai`` with OPENAI_API_KEY.
"""
import argparse
import json

from ripplekg.db.client import get_db
from ripplekg.extraction import build_edit_from_instruction, build_edits_for_document_instruction
from ripplekg.mechanism.pipeline import run_edit_transactional, run_edits_transactional


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-id", required=True)
    parser.add_argument("--sent-idx", type=int)
    parser.add_argument("--instruction", required=True)
    parser.add_argument("--step", type=int, default=1)
    parser.add_argument(
        "--input-kind",
        choices=["instruction", "fact"],
        default="instruction",
        help="instruction is a text-edit command; fact is an updated factual statement.",
    )
    parser.add_argument(
        "--scope",
        choices=["sentence", "document"],
        default="sentence",
        help="sentence edits one sent_idx; document edits all related evidence sentences.",
    )
    parser.add_argument(
        "--selector",
        choices=["evidence", "embedding"],
        default="evidence",
        help="document scope selector: evidence uses surface/provenance; embedding uses semantic search.",
    )
    parser.add_argument(
        "--semantic-limit",
        type=int,
        default=5,
        help="Top-k embedded sentences to consider when --selector embedding.",
    )
    parser.add_argument(
        "--semantic-threshold",
        type=float,
        help="Minimum embedding similarity for --selector embedding candidates.",
    )
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

    if args.scope == "sentence":
        if args.sent_idx is None:
            raise SystemExit("--sent-idx is required when --scope sentence")
        edit = build_edit_from_instruction(
            db,
            doc_id=args.doc_id,
            sent_idx=args.sent_idx,
            instruction=args.instruction,
            provider=args.provider,
            input_kind=args.input_kind,
        )
        edits = [edit]
    else:
        edits = build_edits_for_document_instruction(
            db,
            doc_id=args.doc_id,
            instruction=args.instruction,
            provider=args.provider,
            input_kind=args.input_kind,
            selector=args.selector,
            semantic_limit=args.semantic_limit,
            semantic_threshold=args.semantic_threshold,
        )

    print(f"Generated {len(edits)} EditOp(s)")
    print(json.dumps([edit.model_dump() for edit in edits], indent=2, ensure_ascii=False))

    if args.dry_run:
        raise SystemExit(0)

    if not edits:
        raise SystemExit("No related evidence sentences found for this instruction.")

    if len(edits) == 1:
        result = run_edit_transactional(
            db,
            edits[0],
            step=args.step,
            refresh_mode=args.refresh_mode,
        )
    else:
        result = run_edits_transactional(
            db,
            edits,
            step=args.step,
            refresh_mode=args.refresh_mode,
        )
    print()
    print("M1/M2 result")
    if isinstance(result, list):
        print(json.dumps([item.model_dump() for item in result], indent=2, ensure_ascii=False))
    else:
        print(result.model_dump_json(indent=2))

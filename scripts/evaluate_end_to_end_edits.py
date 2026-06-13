"""Evaluate final EditOps and M1/M2 decisions on reviewed replacement cases."""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from arango import ArangoClient

from ripplekg.config import settings
from ripplekg.db import repo, schema
from ripplekg.db.client import get_db
from ripplekg.extraction.editor import (
    build_edit_from_instruction,
    current_triples_for_sentence,
    verify_supported_old_triples,
)
from ripplekg.ingest.docred import normalize_name
from ripplekg.mechanism.pipeline import run_edit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", default="data/edit_annotation_set.json")
    parser.add_argument("--gate-results", default="data/llm_relevance_gate_eval.json")
    parser.add_argument("--output", default="data/end_to_end_edit_eval.json")
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    parser.add_argument("--eval-db", default="ripplekg_eval")
    parser.add_argument("--max-ops", type=int)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--refresh", action="store_true", help="Regenerate cached EditOps.")
    return parser.parse_args()


def get_eval_db(name: str) -> Any:
    client = ArangoClient(hosts=settings.arango_url)
    system = client.db(
        "_system",
        username=settings.arango_user,
        password=settings.arango_password,
    )
    if not system.has_database(name):
        system.create_database(name)
    db = client.db(name, username=settings.arango_user, password=settings.arango_password)
    schema.init_schema(db)
    return db


def clone_database(source: Any, target: Any) -> None:
    """Clone the current project collections so annotations and evaluation agree."""
    repo.clear_all(target)
    for collection_name in schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS:
        docs = []
        for document in source.collection(collection_name).all():
            clean = {
                key: value
                for key, value in document.items()
                if key not in {"_id", "_rev", "_oldRev"}
            }
            docs.append(clean)
        repo.bulk_insert(target, collection_name, docs)


def triple_key(triple: tuple[str, str, str] | list[str]) -> tuple[str, str, str]:
    return normalize_name(triple[0]), normalize_name(triple[1]), normalize_name(triple[2])


def source_triple(case: dict[str, Any]) -> tuple[str, str, str]:
    return case["old_fact_parts"]["head"], case["old_fact_parts"]["relation"], case["old_fact_parts"]["tail"]


def replacement_triple(case: dict[str, Any]) -> tuple[str, str, str]:
    return case["new_fact_parts"]["head"], case["new_fact_parts"]["relation"], case["new_fact_parts"]["tail"]


def enrich_fact_parts(db: Any, case: dict[str, Any]) -> None:
    relation = repo.get_relation(db, case["source_relation_id"])
    if relation is None:
        raise ValueError(f"Missing source relation: {case['source_relation_id']}")
    head = repo.get_entity(db, relation["head"])
    tail = repo.get_entity(db, relation["tail"])
    if head is None or tail is None:
        raise ValueError(f"Missing source relation endpoints: {case['source_relation_id']}")

    new_tail = case["new_fact"][: -len(".")].removeprefix(
        f"{head['name']} {relation['rel_type']} "
    )
    case["old_fact_parts"] = {
        "head": head["name"],
        "relation": relation["rel_type"],
        "tail": tail["name"],
    }
    case["new_fact_parts"] = {
        "head": head["name"],
        "relation": relation["rel_type"],
        "tail": new_tail,
    }


def gate_predictions(gate_payload: dict[str, Any]) -> dict[tuple[str, str], bool]:
    return {
        (case_id, decision["sentence_id"]): decision["should_edit"]
        for case_id, result in gate_payload["cases"].items()
        for decision in result["decisions"]
    }


def load_cache(path: Path, provider: str, refresh: bool) -> dict[str, Any]:
    if not refresh and path.exists():
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("provider") == provider:
            return payload
    return {"provider": provider, "operations": {}}


def save_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def generate_edit_with_retries(
    db: Any,
    case: dict[str, Any],
    candidate: dict[str, Any],
    provider: str,
    retries: int,
) -> Any:
    sent_idx = int(candidate["sentence_id"].split(":", 1)[1])
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return build_edit_from_instruction(
                db,
                doc_id=case["doc_id"],
                sent_idx=sent_idx,
                instruction=case["update_input"],
                provider=provider,
                input_kind="instruction",
            )
        except Exception as exc:  # noqa: BLE001 - provider failures vary
            last_error = exc
            if attempt == retries:
                break
            print(
                f"  generation attempt {attempt}/{retries} failed: "
                f"{type(exc).__name__}; retrying",
                flush=True,
            )
            time.sleep(attempt * 2)
    raise RuntimeError(f"EditOp generation failed after {retries} attempts") from last_error


def relation_deltas(result: Any) -> list[Any]:
    return [item for item in result.evidence_delta if item.scope == "relation"]


def decision_for_delta(result: Any, target_id: str) -> str | None:
    return next(
        (item.decision for item in result.decisions if item.target_id == target_id),
        None,
    )


def expected_policy_decision(db: Any, delta: Any) -> str:
    if delta.delta_type == "unchanged":
        return "SKIP"
    if delta.delta_type == "added":
        return "PATCH"
    target_type = "entity" if delta.scope == "mention" else "relation"
    return "REBUILD" if repo.count_active_evidence(db, target_type, delta.target_id) == 0 else "PATCH"


def evaluate_operation(
    db: Any,
    case: dict[str, Any],
    candidate: dict[str, Any],
    edit_payload: dict[str, Any],
    *,
    step: int,
) -> dict[str, Any]:
    from ripplekg.models import EditOp

    edit = EditOp.model_validate(edit_payload)
    sent_id = f"{edit.doc_id}:{edit.sent_idx}"
    edit.intended_triples = verify_supported_old_triples(
        current_triples_for_sentence(db, sent_id),
        edit.new_text,
        edit.intended_triples,
        superseded_triples=[source_triple(case)],
    )
    old_key = triple_key(source_triple(case))
    new_key = triple_key(replacement_triple(case))
    intended_keys = {triple_key(triple) for triple in edit.intended_triples}

    old_evidence_before = repo.count_active_evidence(db, "relation", case["source_relation_id"])
    expected_old_decision = "REBUILD" if old_evidence_before == 1 else "PATCH"
    result = run_edit(db, edit, step=step, refresh_mode="deferred")
    rel_deltas = relation_deltas(result)

    removed_old = next(
        (
            item for item in rel_deltas
            if item.delta_type == "removed" and triple_key((
                item.triple["head"],
                item.triple["rel_type"],
                item.triple["tail"],
            )) == old_key
        ),
        None,
    )
    added_new = next(
        (
            item for item in rel_deltas
            if item.delta_type == "added" and triple_key((
                item.triple["head"],
                item.triple["rel_type"],
                item.triple["tail"],
            )) == new_key
        ),
        None,
    )

    old_decision = decision_for_delta(result, removed_old.target_id) if removed_old else None
    new_decision = decision_for_delta(result, added_new.target_id) if added_new else None
    human_should_edit = candidate["human_should_edit"]
    content_correct = (
        human_should_edit
        and old_key not in intended_keys
        and new_key in intended_keys
        and edit.new_text != candidate["text"]
    )
    m1_correct = bool(human_should_edit and removed_old and added_new)
    m2_correct = bool(
        m1_correct
        and old_decision == expected_old_decision
        and new_decision == "PATCH"
    )
    actual_decisions = {item.target_id: item.decision for item in result.decisions}
    policy_checks = [
        {
            "target_id": delta.target_id,
            "delta_type": delta.delta_type,
            "scope": delta.scope,
            "expected": expected_policy_decision(db, delta),
            "actual": actual_decisions.get(delta.target_id),
        }
        for delta in result.evidence_delta
    ]
    policy_rule_correct = all(
        item["expected"] == item["actual"] for item in policy_checks
    )

    return {
        "case_id": case["case_id"],
        "sentence_id": candidate["sentence_id"],
        "human_should_edit": human_should_edit,
        "edit_generated": True,
        "old_fact": case["old_fact"],
        "new_fact": case["new_fact"],
        "edit": edit.model_dump(),
        "content": {
            "old_triple_removed_from_intended": old_key not in intended_keys,
            "new_triple_added_to_intended": new_key in intended_keys,
            "text_changed": edit.new_text != candidate["text"],
            "correct": content_correct,
        },
        "m1": {
            "removed_old_relation": bool(removed_old),
            "added_new_relation": bool(added_new),
            "correct": m1_correct,
            "relation_deltas": [item.model_dump() for item in rel_deltas],
        },
        "m2": {
            "expected_old_decision": expected_old_decision,
            "actual_old_decision": old_decision,
            "expected_new_decision": "PATCH",
            "actual_new_decision": new_decision,
            "correct": m2_correct,
            "policy_rule_correct": policy_rule_correct,
            "policy_checks": policy_checks,
            "decisions": [item.model_dump() for item in result.decisions],
        },
    }


def failed_operation(case: dict[str, Any], candidate: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "case_id": case["case_id"],
        "sentence_id": candidate["sentence_id"],
        "human_should_edit": candidate["human_should_edit"],
        "edit_generated": False,
        "generation_error": error,
        "old_fact": case["old_fact"],
        "new_fact": case["new_fact"],
        "content": {"correct": False},
        "m1": {"correct": False},
        "m2": {"correct": False},
    }


def summarize(
    annotations: list[dict[str, Any]],
    predictions: dict[tuple[str, str], bool],
    operations: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [
        (case, candidate)
        for case in annotations
        for candidate in case["candidates"]
    ]
    tp = fp = fn = tn = 0
    for case, candidate in candidates:
        predicted = predictions.get((case["case_id"], candidate["sentence_id"]), False)
        actual = candidate["human_should_edit"]
        tp += predicted and actual
        fp += predicted and not actual
        fn += not predicted and actual
        tn += not predicted and not actual

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    true_ops = [item for item in operations if item["human_should_edit"]]
    generated_ops = [item for item in operations if item["edit_generated"]]
    return {
        "edit_selection": {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "tp": tp,
            "fp": fp,
            "fn": fn,
            "tn": tn,
        },
        "edit_content": {
            "evaluated_true_operations": len(true_ops),
            "correct": sum(item["content"]["correct"] for item in true_ops),
            "accuracy": (
                sum(item["content"]["correct"] for item in true_ops) / len(true_ops)
                if true_ops else 0.0
            ),
        },
        "generation": {
            "selected_candidates": len(operations),
            "generated": len(generated_ops),
            "failed_or_rejected": len(operations) - len(generated_ops),
            "incorrect_generated_edits": sum(
                item["edit_generated"] and not item["human_should_edit"]
                for item in operations
            ),
        },
        "m1": {
            "evaluated_true_operations": len(true_ops),
            "correct": sum(item["m1"]["correct"] for item in true_ops),
            "accuracy": (
                sum(item["m1"]["correct"] for item in true_ops) / len(true_ops)
                if true_ops else 0.0
            ),
        },
        "m2": {
            "evaluated_true_operations": len(true_ops),
            "correct": sum(item["m2"]["correct"] for item in true_ops),
            "accuracy": (
                sum(item["m2"]["correct"] for item in true_ops) / len(true_ops)
                if true_ops else 0.0
            ),
            "policy_rule_evaluated_operations": len(generated_ops),
            "policy_rule_correct_operations": sum(
                item["m2"]["policy_rule_correct"] for item in generated_ops
            ),
            "policy_rule_accuracy": (
                sum(item["m2"]["policy_rule_correct"] for item in generated_ops)
                / len(generated_ops)
                if generated_ops else 0.0
            ),
        },
    }


def main() -> None:
    args = parse_args()
    annotations_payload = json.loads(Path(args.annotations).read_text(encoding="utf-8"))
    gate_payload = json.loads(Path(args.gate_results).read_text(encoding="utf-8"))
    annotations = annotations_payload["cases"]
    predictions = gate_predictions(gate_payload)
    selected = [
        (case, candidate)
        for case in annotations
        for candidate in case["candidates"]
        if predictions.get((case["case_id"], candidate["sentence_id"]), False)
    ]
    if args.max_ops is not None:
        selected = selected[:args.max_ops]

    output = Path(args.output)
    cache = load_cache(output, args.provider, args.refresh)
    source_db = get_db()
    db = get_eval_db(args.eval_db)
    operations = []

    for index, (case, candidate) in enumerate(selected, start=1):
        operation_id = f"{case['case_id']}::{candidate['sentence_id']}"
        clone_database(source_db, db)
        enrich_fact_parts(db, case)
        if operation_id not in cache["operations"]:
            print(f"[{index}/{len(selected)}] generating {operation_id}", flush=True)
            try:
                edit = generate_edit_with_retries(
                    db,
                    case,
                    candidate,
                    args.provider,
                    args.retries,
                )
                if edit is None:
                    raise RuntimeError(f"Provider returned no EditOp for {operation_id}")
                cache["operations"][operation_id] = {"edit": edit.model_dump()}
            except Exception as exc:  # noqa: BLE001 - record provider reliability
                cache["operations"][operation_id] = {
                    "generation_error": f"{type(exc).__name__}: {exc}",
                }
            save_cache(output, cache)
        else:
            print(f"[{index}/{len(selected)}] cached {operation_id}", flush=True)

        cached = cache["operations"][operation_id]
        if "edit" in cached:
            operation = evaluate_operation(
                db,
                case,
                candidate,
                cached["edit"],
                step=index,
            )
        else:
            operation = failed_operation(
                case,
                candidate,
                cached.get("generation_error", "unknown generation failure"),
            )
        cache["operations"][operation_id]["evaluation"] = operation
        operations.append(operation)
        save_cache(output, cache)

    cache["summary"] = summarize(annotations, predictions, operations)
    save_cache(output, cache)
    summary = cache["summary"]
    print()
    for section in ("edit_selection", "generation", "edit_content", "m1", "m2"):
        print(f"{section}={json.dumps(summary[section], ensure_ascii=False)}")
    print(f"output={output}")


if __name__ == "__main__":
    main()

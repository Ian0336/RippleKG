"""Create a human-review set for document-level fact replacement edits.

The generated cases explicitly mean "replace the old fact with the new fact".
Existing provenance evidence for the old fact is suggested as should-edit, while
embedding retrieval contributes additional candidates for human review.
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from ripplekg.db.client import get_db
from ripplekg.extraction.embeddings import embedding_backend, semantic_search


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", default="data/edit_annotation_set.json")
    return parser.parse_args()


def load_relation_facts(db: Any) -> list[dict[str, Any]]:
    return list(db.aql.execute(
        """
        FOR relation IN relations
          FILTER relation.status == "active"
          FILTER !REGEX_TEST(relation.rel_type, "^P[0-9]+$")
          LET head = DOCUMENT("entities", relation.head)
          LET tail = DOCUMENT("entities", relation.tail)
          LET evidence = (
            FOR edge IN sentence_supports_relation
              FILTER edge._to == relation._id AND edge.status == "active"
              SORT edge._from
              RETURN PARSE_IDENTIFIER(edge._from).key
          )
          FILTER head != null AND tail != null AND LENGTH(evidence) > 0
          LET doc_id = SPLIT(relation._key, ":")[0]
          SORT doc_id, relation._key
          RETURN {
            relation_id: relation._key,
            doc_id: doc_id,
            head: head.name,
            relation: relation.rel_type,
            tail: tail.name,
            evidence_ids: evidence
          }
        """
    ))


def alternative_tails(facts: list[dict[str, Any]]) -> dict[str, list[str]]:
    tails: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        tails[fact["relation"]].add(fact["tail"])
    return {relation: sorted(values) for relation, values in tails.items()}


def choose_replacement_tail(fact: dict[str, Any], tails: dict[str, list[str]]) -> str | None:
    return next(
        (
            tail
            for tail in tails[fact["relation"]]
            if tail != fact["tail"] and tail != fact["head"]
        ),
        None,
    )


def sentence_texts(db: Any, sentence_ids: set[str]) -> dict[str, str]:
    return {
        row["_key"]: row["text"]
        for row in db.aql.execute(
            """
            FOR sentence IN sentences
              FILTER sentence._key IN @ids
              RETURN KEEP(sentence, "_key", "text")
            """,
            bind_vars={"ids": sorted(sentence_ids)},
        )
    }


def build_case(
    db: Any,
    fact: dict[str, Any],
    replacement_tail: str,
    *,
    top_k: int,
    case_number: int,
) -> dict[str, Any]:
    old_fact = f"{fact['head']} {fact['relation']} {fact['tail']}."
    new_fact = f"{fact['head']} {fact['relation']} {replacement_tail}."
    update_input = f"Replace the old fact '{old_fact}' with '{new_fact}'"
    retrieved = semantic_search(
        db,
        update_input,
        doc_id=fact["doc_id"],
        limit=top_k,
        threshold=None,
        use_index=False,
    )
    retrieval_by_id = {row["sent_id"]: row for row in retrieved}
    evidence_ids = set(fact["evidence_ids"])
    candidate_ids = evidence_ids | set(retrieval_by_id)
    texts = sentence_texts(db, candidate_ids)

    candidates = []
    for sentence_id in sorted(
        candidate_ids,
        key=lambda item: (
            retrieval_by_id.get(item, {}).get("score", float("-inf")),
            item,
        ),
        reverse=True,
    ):
        retrieval = retrieval_by_id.get(sentence_id)
        supported_old_fact = sentence_id in evidence_ids
        candidates.append({
            "sentence_id": sentence_id,
            "text": texts.get(sentence_id, ""),
            "retrieval_rank": (
                next(
                    index
                    for index, row in enumerate(retrieved, start=1)
                    if row["sent_id"] == sentence_id
                )
                if retrieval
                else None
            ),
            "retrieval_score": round(retrieval["score"], 6) if retrieval else None,
            "provenance_supports_old_fact": supported_old_fact,
            "suggested_should_edit": supported_old_fact,
            "suggested_reason": (
                "This sentence is provenance evidence for the old fact being replaced."
                if supported_old_fact
                else "Embedding retrieved it, but provenance does not link it to the old fact."
            ),
            "human_should_edit": None,
            "human_notes": "",
        })

    return {
        "case_id": f"replace-{case_number:03d}",
        "doc_id": fact["doc_id"],
        "update_type": "replace_old_fact",
        "annotation_rule": (
            "Mark true only if this sentence must change when the old fact is explicitly "
            "replaced by the new fact."
        ),
        "source_relation_id": fact["relation_id"],
        "old_fact": old_fact,
        "new_fact": new_fact,
        "update_input": update_input,
        "candidates": candidates,
    }


def select_facts(facts: list[dict[str, Any]], count: int) -> list[dict[str, Any]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        by_doc[fact["doc_id"]].append(fact)

    selected = []
    while len(selected) < count:
        added = False
        for doc_id in sorted(by_doc):
            if by_doc[doc_id]:
                selected.append(by_doc[doc_id].pop(0))
                added = True
                if len(selected) >= count:
                    break
        if not added:
            break
    return selected


def main() -> None:
    args = parse_args()
    db = get_db()
    facts = load_relation_facts(db)
    tails = alternative_tails(facts)
    replaceable = [
        fact for fact in facts
        if choose_replacement_tail(fact, tails) is not None
    ]
    selected = select_facts(replaceable, args.cases)
    cases = [
        build_case(
            db,
            fact,
            choose_replacement_tail(fact, tails) or "",
            top_k=args.top_k,
            case_number=index,
        )
        for index, fact in enumerate(selected, start=1)
    ]

    payload = {
        "metadata": {
            "embedding_backend": embedding_backend(),
            "requested_cases": args.cases,
            "generated_cases": len(cases),
            "top_k": args.top_k,
            "instructions": (
                "Review every candidate and set human_should_edit to true or false. "
                "Do not change suggested_should_edit; it is the system baseline."
            ),
        },
        "cases": cases,
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    candidate_count = sum(len(case["candidates"]) for case in cases)
    suggested_count = sum(
        candidate["suggested_should_edit"]
        for case in cases
        for candidate in case["candidates"]
    )
    print(f"backend={embedding_backend()}")
    print(f"generated_cases={len(cases)}")
    print(f"candidate_sentences={candidate_count}")
    print(f"suggested_should_edit={suggested_count}")
    print(f"review_output={output}")


if __name__ == "__main__":
    main()

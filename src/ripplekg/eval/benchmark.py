"""Batch benchmark helpers for larger DB-backed validation runs."""
from collections import Counter
from dataclasses import dataclass
import re

from arango.database import StandardDatabase

from ripplekg.baselines import naive
from ripplekg.db import repo
from ripplekg.mechanism.pipeline import run_edit_transactional
from ripplekg.models import EditOp, Triple


@dataclass
class BenchmarkRow:
    step: int
    sent_id: str
    scenario: str
    old_text: str
    new_text: str
    original_triples: list[Triple]
    intended_triples: list[Triple]
    removed_from_intended: list[Triple]
    added_to_intended: list[Triple]
    affected_mentions: int
    affected_relations: int
    delta_counts: dict[str, int]
    decision_counts: dict[str, int]
    evidence_audit: list[dict]
    decision_audit: list[dict]
    marked_stale: list[str]
    refreshed_targets: list[dict]
    ours_cost: int
    full_rebuild_cost: int
    naive_stale_count: int

    def as_dict(self) -> dict:
        return {
            "step": self.step,
            "sent_id": self.sent_id,
            "scenario": self.scenario,
            "old_text": self.old_text,
            "new_text": self.new_text,
            "original_triples": self.original_triples,
            "intended_triples": self.intended_triples,
            "removed_from_intended": self.removed_from_intended,
            "added_to_intended": self.added_to_intended,
            "affected_mentions": self.affected_mentions,
            "affected_relations": self.affected_relations,
            "delta_counts": self.delta_counts,
            "decision_counts": self.decision_counts,
            "evidence_audit": self.evidence_audit,
            "decision_audit": self.decision_audit,
            "marked_stale": self.marked_stale,
            "refreshed_targets": self.refreshed_targets,
            "ours_cost": self.ours_cost,
            "full_rebuild_cost": self.full_rebuild_cost,
            "naive_stale_count": self.naive_stale_count,
        }


def evidence_sentences(db: StandardDatabase, limit: int) -> list[dict]:
    """Return sentences with at least one active relation evidence edge."""
    return list(db.aql.execute(
        """
        FOR s IN sentences
          LET relation_count = LENGTH(
            FOR v, e IN 1..1 OUTBOUND s sentence_supports_relation
              FILTER e.status != 'removed'
              RETURN 1
          )
          FILTER relation_count > 0
          SORT s.doc_id, s.idx
          LIMIT @limit
          RETURN {
            sent_id: s._key,
            doc_id: s.doc_id,
            sent_idx: s.idx,
            text: s.text,
            relation_count: relation_count
          }
        """,
        bind_vars={"limit": limit},
    ))


def non_evidence_sentences(db: StandardDatabase, limit: int) -> list[dict]:
    """Return sentences with no active relation evidence edges."""
    return list(db.aql.execute(
        """
        FOR s IN sentences
          LET relation_count = LENGTH(
            FOR v, e IN 1..1 OUTBOUND s sentence_supports_relation
              FILTER e.status != 'removed'
              RETURN 1
          )
          FILTER relation_count == 0
          SORT s.doc_id, s.idx
          LIMIT @limit
          RETURN {
            sent_id: s._key,
            doc_id: s.doc_id,
            sent_idx: s.idx,
            text: s.text,
            relation_count: relation_count
          }
        """,
        bind_vars={"limit": limit},
    ))


def relation_only_sentences(db: StandardDatabase, limit: int, offset: int = 0) -> list[dict]:
    """Return evidence sentences with offset support for scenario partitioning."""
    return list(db.aql.execute(
        """
        FOR s IN sentences
          LET relation_count = LENGTH(
            FOR v, e IN 1..1 OUTBOUND s sentence_supports_relation
              FILTER e.status != 'removed'
              RETURN 1
          )
          FILTER relation_count > 0
          SORT s.doc_id, s.idx
          LIMIT @offset, @limit
          RETURN {
            sent_id: s._key,
            doc_id: s.doc_id,
            sent_idx: s.idx,
            text: s.text,
            relation_count: relation_count
          }
        """,
        bind_vars={"limit": limit, "offset": offset},
    ))


def intended_triples_from_current_evidence(
    db: StandardDatabase,
    sent_id: str,
) -> list[Triple]:
    """Use current active relation evidence as authored intended triples."""
    affected = repo.affected_evidence(db, sent_id)
    triples = []
    for item in affected["relations"]:
        relation = repo.get_relation(db, item["relation_id"])
        if relation is None:
            continue
        head = repo.get_entity(db, relation["head"])
        tail = repo.get_entity(db, relation["tail"])
        if head is None or tail is None:
            continue
        triples.append((head["name"], relation["rel_type"], tail["name"]))
    return triples


def _count(items, attr: str) -> dict[str, int]:
    return dict(Counter(getattr(item, attr) for item in items))


def _triple_list(edit: EditOp) -> list[Triple]:
    return [tuple(item) for item in edit.intended_triples]


def _triple_diff(before: list[Triple], after: list[Triple]) -> tuple[list[Triple], list[Triple]]:
    after_counter = Counter(after)
    removed = []
    for triple in before:
        if after_counter[triple] > 0:
            after_counter[triple] -= 1
        else:
            removed.append(triple)

    before_counter = Counter(before)
    added = []
    for triple in after:
        if before_counter[triple] > 0:
            before_counter[triple] -= 1
        else:
            added.append(triple)
    return removed, added


def _cleanup_text(text: str) -> str:
    text = re.sub(r"\s{2,}", " ", text).strip()
    text = re.sub(r",\s*,", ",", text)
    text = re.sub(r"\(\s*\)", "", text).strip()
    return text


def _remove_phrase(text: str, phrase: str) -> str:
    pattern = r"(?<!\w)\s*,?\s*" + re.escape(phrase) + r"\s*,?(?!\w)"
    updated = re.sub(pattern, " ", text, count=1)
    return _cleanup_text(updated)


def _replace_phrase(text: str, old: str, new: str) -> str:
    pattern = r"(?<!\w)" + re.escape(old) + r"(?!\w)"
    updated = re.sub(pattern, new, text, count=1)
    return _cleanup_text(updated)


def _phrase_occurs(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def _choose_removal_phrase(text: str, triples: list[Triple]) -> str | None:
    candidates = []
    for head, _, tail in triples:
        for phrase in (tail, head):
            if not phrase or not _phrase_occurs(text, phrase):
                continue
            position = text.rfind(phrase)
            touched = sum(1 for h, _, t in triples if h == phrase or t == phrase)
            candidates.append((position, -touched, len(phrase), phrase))
    if not candidates:
        return None
    return max(candidates)[3]


def _remove_triples_for_phrase(triples: list[Triple], phrase: str) -> list[Triple]:
    return [triple for triple in triples if triple[0] != phrase and triple[2] != phrase]


def _choose_tail_change(text: str, triples: list[Triple]) -> Triple:
    for triple in triples:
        tail = triple[2]
        if _phrase_occurs(text, tail):
            return triple
    return triples[0]


def _target_snapshot(db: StandardDatabase, target_type: str, target_id: str) -> dict:
    if target_type == "entity":
        entity = repo.get_entity(db, target_id)
        if entity is None:
            return {"target_id": target_id, "missing": True}
        return {
            "target_id": target_id,
            "label": entity.get("name"),
            "evidence_count": entity.get("evidence_count"),
            "freshness_status": entity.get("freshness_status"),
        }

    relation = repo.get_relation(db, target_id)
    if relation is None:
        return {"target_id": target_id, "missing": True}

    head = repo.get_entity(db, relation["head"])
    tail = repo.get_entity(db, relation["tail"])
    return {
        "target_id": target_id,
        "head": head.get("name") if head else relation.get("head"),
        "rel_type": relation.get("rel_type"),
        "tail": tail.get("name") if tail else relation.get("tail"),
        "evidence_count": relation.get("evidence_count"),
        "freshness_status": relation.get("freshness_status"),
        "status": relation.get("status"),
    }


def _evidence_audit(result) -> list[dict]:
    return [
        {
            "delta_type": item.delta_type,
            "scope": item.scope,
            "target_id": item.target_id,
            "triple": item.triple,
            "reason": item.reason,
        }
        for item in result.evidence_delta
    ]


def _decision_audit(db: StandardDatabase, result) -> list[dict]:
    rows = []
    for item in result.decisions:
        target = _target_snapshot(db, item.target_type, item.target_id)
        rows.append({
            "target_type": item.target_type,
            "target_id": item.target_id,
            "decision": item.decision,
            "reason": item.reason,
            "cost": item.cost,
            "after_refresh": target,
        })
    return rows


def _record_row(
    db: StandardDatabase,
    sentence: dict,
    scenario: str,
    edit: EditOp,
    step: int,
    original_triples: list[Triple] | None = None,
) -> BenchmarkRow:
    affected = repo.affected_evidence(db, sentence["sent_id"])
    baseline = naive.invalidate_sentence(db, sentence["sent_id"], step, dry_run=True)
    before_triples = original_triples or intended_triples_from_current_evidence(db, sentence["sent_id"])
    after_triples = _triple_list(edit)
    removed, added = _triple_diff(before_triples, after_triples)
    result = run_edit_transactional(db, edit, step, refresh_mode="immediate")
    evidence_audit = _evidence_audit(result)
    decision_audit = _decision_audit(db, result)
    return BenchmarkRow(
        step=step,
        sent_id=sentence["sent_id"],
        scenario=scenario,
        old_text=sentence["text"],
        new_text=edit.new_text,
        original_triples=before_triples,
        intended_triples=after_triples,
        removed_from_intended=removed,
        added_to_intended=added,
        affected_mentions=len(affected["mentions"]),
        affected_relations=len(affected["relations"]),
        delta_counts=_count(result.evidence_delta, "delta_type"),
        decision_counts=_count(result.decisions, "decision"),
        evidence_audit=evidence_audit,
        decision_audit=decision_audit,
        marked_stale=result.freshness["marked_stale"],
        refreshed_targets=result.freshness["refreshed"],
        ours_cost=result.cost["this_step"],
        full_rebuild_cost=result.cost["vs_full_rebuild"],
        naive_stale_count=baseline["stale_count"],
    )


def run_semantic_noop_benchmark(
    db: StandardDatabase,
    limit: int,
    start_step: int = 1,
) -> list[BenchmarkRow]:
    """Run many semantic no-op edits and compare ours against B2 naive."""
    rows = []
    for offset, sentence in enumerate(evidence_sentences(db, limit), start=0):
        step = start_step + offset
        sent_id = sentence["sent_id"]
        affected = repo.affected_evidence(db, sent_id)
        baseline = naive.invalidate_sentence(db, sent_id, step, dry_run=True)
        edit = EditOp(
            doc_id=sentence["doc_id"],
            sent_idx=sentence["sent_idx"],
            new_text=sentence["text"] + " ",
            intended_triples=intended_triples_from_current_evidence(db, sent_id),
        )
        before_triples = _triple_list(edit)
        after_triples = _triple_list(edit)
        removed, added = _triple_diff(before_triples, after_triples)
        result = run_edit_transactional(db, edit, step, refresh_mode="immediate")
        evidence_audit = _evidence_audit(result)
        decision_audit = _decision_audit(db, result)
        rows.append(BenchmarkRow(
            step=step,
            sent_id=sent_id,
            scenario="semantic_noop",
            old_text=sentence["text"],
            new_text=edit.new_text,
            original_triples=before_triples,
            intended_triples=after_triples,
            removed_from_intended=removed,
            added_to_intended=added,
            affected_mentions=len(affected["mentions"]),
            affected_relations=len(affected["relations"]),
            delta_counts=_count(result.evidence_delta, "delta_type"),
            decision_counts=_count(result.decisions, "decision"),
            evidence_audit=evidence_audit,
            decision_audit=decision_audit,
            marked_stale=result.freshness["marked_stale"],
            refreshed_targets=result.freshness["refreshed"],
            ours_cost=result.cost["this_step"],
            full_rebuild_cost=result.cost["vs_full_rebuild"],
            naive_stale_count=baseline["stale_count"],
        ))
    return rows


def run_mixed_benchmark(
    db: StandardDatabase,
    per_scenario: int,
    start_step: int = 1,
) -> list[BenchmarkRow]:
    """Run no-op, remove, and change-tail edits across different sentences."""
    rows = []
    step = start_step

    noop_sentences = relation_only_sentences(db, per_scenario, offset=0)
    remove_sentences = relation_only_sentences(db, per_scenario, offset=per_scenario)
    change_sentences = relation_only_sentences(db, per_scenario, offset=per_scenario * 2)
    non_relation = non_evidence_sentences(db, per_scenario)

    for sentence in noop_sentences:
        triples = intended_triples_from_current_evidence(db, sentence["sent_id"])
        edit = EditOp(
            doc_id=sentence["doc_id"],
            sent_idx=sentence["sent_idx"],
            new_text=sentence["text"] + " ",
            intended_triples=triples,
        )
        rows.append(_record_row(db, sentence, "semantic_noop", edit, step))
        step += 1

    for sentence in remove_sentences:
        triples = intended_triples_from_current_evidence(db, sentence["sent_id"])
        if not triples:
            continue
        removed_phrase = _choose_removal_phrase(sentence["text"], triples)
        if removed_phrase:
            new_text = _remove_phrase(sentence["text"], removed_phrase)
            intended = _remove_triples_for_phrase(triples, removed_phrase)
        else:
            new_text = sentence["text"] + " [benchmark relation removed]"
            intended = triples[1:]
        edit = EditOp(
            doc_id=sentence["doc_id"],
            sent_idx=sentence["sent_idx"],
            new_text=new_text,
            intended_triples=intended,
        )
        rows.append(_record_row(db, sentence, "remove_relation", edit, step, triples))
        step += 1

    for sentence in change_sentences:
        triples = intended_triples_from_current_evidence(db, sentence["sent_id"])
        if not triples:
            continue
        old = _choose_tail_change(sentence["text"], triples)
        head, rel_type, old_tail = old
        replacement_tail = f"Benchmark Entity {step}"
        changed = [
            (head, rel_type, replacement_tail) if triple == old else triple
            for triple in triples
        ]
        if _phrase_occurs(sentence["text"], old_tail):
            new_text = _replace_phrase(sentence["text"], old_tail, replacement_tail)
        else:
            new_text = f"{sentence['text']} {replacement_tail}."
        edit = EditOp(
            doc_id=sentence["doc_id"],
            sent_idx=sentence["sent_idx"],
            new_text=new_text,
            intended_triples=changed,
        )
        rows.append(_record_row(db, sentence, "change_relation_tail", edit, step, triples))
        step += 1

    for sentence in non_relation:
        edit = EditOp(
            doc_id=sentence["doc_id"],
            sent_idx=sentence["sent_idx"],
            new_text=sentence["text"] + " ",
            intended_triples=[],
        )
        rows.append(_record_row(db, sentence, "no_relation_evidence", edit, step, []))
        step += 1

    return rows


def summarize_rows(rows: list[BenchmarkRow], include_by_scenario: bool = True) -> dict:
    delta_counts = Counter()
    decision_counts = Counter()
    affected_mentions = 0
    affected_relations = 0
    ours_cost = 0
    full_rebuild_cost = 0
    naive_stale_count = 0

    for row in rows:
        delta_counts.update(row.delta_counts)
        decision_counts.update(row.decision_counts)
        affected_mentions += row.affected_mentions
        affected_relations += row.affected_relations
        ours_cost += row.ours_cost
        full_rebuild_cost += row.full_rebuild_cost
        naive_stale_count += row.naive_stale_count

    summary = {
        "edits": len(rows),
        "affected_mentions": affected_mentions,
        "affected_relations": affected_relations,
        "deltas": {
            "added": delta_counts.get("added", 0),
            "removed": delta_counts.get("removed", 0),
            "unchanged": delta_counts.get("unchanged", 0),
        },
        "decisions": {
            "SKIP": decision_counts.get("SKIP", 0),
            "PATCH": decision_counts.get("PATCH", 0),
            "REBUILD": decision_counts.get("REBUILD", 0),
        },
        "ours_cost": ours_cost,
        "full_rebuild_cost": full_rebuild_cost,
        "naive_stale_count": naive_stale_count,
    }

    if include_by_scenario:
        summary["by_scenario"] = {
            scenario: summarize_rows(
                [row for row in rows if row.scenario == scenario],
                include_by_scenario=False,
            )
            for scenario in sorted({row.scenario for row in rows})
        }

    return summary

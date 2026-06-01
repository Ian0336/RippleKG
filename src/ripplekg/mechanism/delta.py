"""M1: semantic evidence delta and synchronous provenance updates."""
import hashlib
from dataclasses import dataclass

from arango.database import StandardDatabase

from ripplekg.db import repo
from ripplekg.ingest.docred import normalize_name
from ripplekg.models import EvidenceDelta, Triple


@dataclass(frozen=True)
class ResolvedTriple:
    head_name: str
    rel_type: str
    tail_name: str
    head_id: str
    tail_id: str
    relation_id: str

    @property
    def key(self) -> tuple[str, str, str]:
        return (
            normalize_name(self.head_name),
            self.rel_type,
            normalize_name(self.tail_name),
        )


def _safe_key(prefix: str, *parts: str) -> str:
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _edge_key(prefix: str, *parts: str) -> str:
    return _safe_key(prefix, *parts)


def _ensure_entity(db: StandardDatabase, doc_id: str, name: str, step: int) -> dict:
    norm = normalize_name(name)
    existing = repo.find_entity_by_norm(db, doc_id, norm)
    if existing is not None:
        return existing

    key = f"{doc_id}:e_{_safe_key('auto', norm)}"
    entity = {
        "_key": key,
        "name": name,
        "norm_name": norm,
        "type": "unknown",
        "evidence_count": 0,
        "freshness_status": "fresh",
        "last_changed_step": step,
    }
    db.collection("entities").insert(entity)
    return entity


def _ensure_relation(
    db: StandardDatabase,
    doc_id: str,
    head_id: str,
    rel_type: str,
    tail_id: str,
    step: int,
) -> dict:
    existing = repo.find_relation(db, head_id, rel_type, tail_id)
    if existing is not None:
        return existing

    key = f"{doc_id}:r_{_safe_key('auto', head_id, rel_type, tail_id)}"
    relation = {
        "_key": key,
        "head": head_id,
        "tail": tail_id,
        "rel_id": rel_type,
        "rel_type": rel_type,
        "evidence_count": 0,
        "freshness_status": "fresh",
        "status": "active",
        "last_changed_step": step,
    }
    db.collection("relations").insert(relation)
    return relation


def _upsert_edge(db: StandardDatabase, collection: str, edge: dict) -> None:
    col = db.collection(collection)
    if col.has(edge["_key"]):
        col.update(edge)
    else:
        col.insert(edge)


def _active_relation_triples(db: StandardDatabase, affected: dict) -> dict[tuple[str, str, str], ResolvedTriple]:
    triples = {}
    for item in affected.get("relations", []):
        relation = repo.get_relation(db, item["relation_id"])
        if relation is None:
            continue
        head = repo.get_entity(db, relation["head"])
        tail = repo.get_entity(db, relation["tail"])
        if head is None or tail is None:
            continue
        resolved = ResolvedTriple(
            head_name=head["name"],
            rel_type=relation["rel_type"],
            tail_name=tail["name"],
            head_id=head["_key"],
            tail_id=tail["_key"],
            relation_id=relation["_key"],
        )
        triples[resolved.key] = resolved
    return triples


def _intended_relation_triples(
    db: StandardDatabase,
    doc_id: str,
    intended: list[Triple],
    step: int,
) -> dict[tuple[str, str, str], ResolvedTriple]:
    triples = {}
    for head_name, rel_type, tail_name in intended:
        head = _ensure_entity(db, doc_id, head_name, step)
        tail = _ensure_entity(db, doc_id, tail_name, step)
        relation = _ensure_relation(db, doc_id, head["_key"], rel_type, tail["_key"], step)
        resolved = ResolvedTriple(
            head_name=head["name"],
            rel_type=rel_type,
            tail_name=tail["name"],
            head_id=head["_key"],
            tail_id=tail["_key"],
            relation_id=relation["_key"],
        )
        triples[resolved.key] = resolved
    return triples


def _active_mentions(db: StandardDatabase, affected: dict) -> dict[str, dict]:
    mentions = {}
    for item in affected.get("mentions", []):
        entity = repo.get_entity(db, item["entity_id"])
        if entity is None:
            continue
        mentions[entity["norm_name"]] = {
            "entity_id": entity["_key"],
            "name": entity["name"],
            "surface": item["edge"].get("surface", entity["name"]),
            "edge_key": item["edge"]["_key"],
        }
    return mentions


def _intended_mentions(
    db: StandardDatabase,
    doc_id: str,
    intended: list[Triple],
    step: int,
    old_mentions: dict[str, dict],
    new_text: str,
) -> dict[str, dict]:
    mentions = {}
    normalized_text = normalize_name(new_text)
    for norm, old in old_mentions.items():
        if normalize_name(old.get("surface", old["name"])) in normalized_text:
            mentions[norm] = old

    for head_name, _, tail_name in intended:
        for name in [head_name, tail_name]:
            norm = normalize_name(name)
            if norm in mentions:
                continue
            if norm not in normalized_text:
                continue

            existing = repo.find_entity_by_norm(db, doc_id, norm)
            if existing is not None and norm not in old_mentions:
                continue

            entity = existing or _ensure_entity(db, doc_id, name, step)
            mentions[norm] = {
                "entity_id": entity["_key"],
                "name": entity["name"],
                "surface": name,
            }
    return mentions


def _mention_delta(delta_type: str, item: dict, reason: str) -> EvidenceDelta:
    return EvidenceDelta(
        delta_type=delta_type,
        scope="mention",
        triple={"entity": item["name"], "surface": item.get("surface", item["name"])},
        target_id=item["entity_id"],
        reason=reason,
    )


def _relation_delta(delta_type: str, triple: ResolvedTriple, reason: str) -> EvidenceDelta:
    return EvidenceDelta(
        delta_type=delta_type,
        scope="relation",
        triple={
            "head": triple.head_name,
            "rel_type": triple.rel_type,
            "tail": triple.tail_name,
            "head_id": triple.head_id,
            "tail_id": triple.tail_id,
        },
        target_id=triple.relation_id,
        reason=reason,
    )


def compute_and_apply(
    db: StandardDatabase,
    sent_id: str,
    intended_triples: list[Triple],
    step: int,
    new_text: str = "",
) -> list[EvidenceDelta]:
    """Compute M1 deltas and update provenance edges to the new evidence state."""
    doc_id = sent_id.split(":", 1)[0]
    affected = repo.affected_evidence(db, sent_id)

    old_mentions = _active_mentions(db, affected)
    new_mentions = _intended_mentions(db, doc_id, intended_triples, step, old_mentions, new_text)
    old_relations = _active_relation_triples(db, affected)
    new_relations = _intended_relation_triples(db, doc_id, intended_triples, step)

    deltas: list[EvidenceDelta] = []

    for key in sorted(set(old_mentions) | set(new_mentions)):
        old = old_mentions.get(key)
        new = new_mentions.get(key)
        if old and new:
            deltas.append(_mention_delta("unchanged", old, "Mention still present after edit"))
        elif old:
            db.collection("mentions").update({
                "_key": old["edge_key"],
                "status": "removed",
                "removed_step": step,
            })
            deltas.append(_mention_delta("removed", old, "Mention no longer present in intended triples"))
        elif new:
            edge = {
                "_key": _edge_key("mention", sent_id, new["entity_id"]),
                "_from": f"sentences/{sent_id}",
                "_to": f"entities/{new['entity_id']}",
                "surface": new["surface"],
                "status": "active",
                "added_step": step,
            }
            _upsert_edge(db, "mentions", edge)
            deltas.append(_mention_delta("added", new, "Mention appears in intended triples"))

    for key in sorted(set(old_relations) | set(new_relations)):
        old = old_relations.get(key)
        new = new_relations.get(key)
        if old and new:
            deltas.append(_relation_delta("unchanged", old, "Triple still supported after edit"))
        elif old:
            edge_key = next(
                item["edge"]["_key"]
                for item in affected["relations"]
                if item["relation_id"] == old.relation_id
            )
            db.collection("sentence_supports_relation").update({
                "_key": edge_key,
                "status": "removed",
                "removed_step": step,
            })
            deltas.append(_relation_delta("removed", old, "Triple no longer supported by sentence"))
        elif new:
            edge = {
                "_key": _edge_key("supports", sent_id, new.relation_id),
                "_from": f"sentences/{sent_id}",
                "_to": f"relations/{new.relation_id}",
                "rel_type": new.rel_type,
                "status": "active",
                "added_step": step,
            }
            _upsert_edge(db, "sentence_supports_relation", edge)
            deltas.append(_relation_delta("added", new, "Triple appears in intended triples"))

    return deltas

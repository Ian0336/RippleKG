"""Build the T0 graph in ArangoDB from parsed Re-DocRED docs. Owner: A (milestone M2).

Per-document entities (entity _key is doc-local, e.g. doc0:e3). All writes go through
ripplekg.db.repo (the seam). Keys line up with repo.fetch_graph: relations.head/tail hold
the entity _key, which is exactly the node id the graph renders.
"""
import hashlib

from arango.database import StandardDatabase

from ripplekg.db import repo
from ripplekg.ingest.docred import parse_docred


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def ingest_document(db: StandardDatabase, doc: dict, doc_id: str) -> None:
    documents = [{
        "_key": doc_id,
        "title": doc["title"],
        "num_sents": len(doc["sentences"]),
        "source": "re-docred",
    }]

    sentences = [{
        "_key": f"{doc_id}:{idx}",
        "doc_id": doc_id,
        "idx": idx,
        "text": text,
        "text_hash": _hash(text),
        "status": "active",
        "last_changed_step": 0,
    } for idx, text in enumerate(doc["sentences"])]

    entities, mention_edges = [], []
    for v, ent in enumerate(doc["entities"]):
        ent_key = f"{doc_id}:e{v}"
        entities.append({
            "_key": ent_key,
            "name": ent["name"],
            "norm_name": ent["norm_name"],
            "type": ent["type"],
            "evidence_count": len(ent["mentions"]),
            "freshness_status": "fresh",
            "last_changed_step": 0,
        })
        for m in ent["mentions"]:
            mention_edges.append({
                "_from": f"sentences/{doc_id}:{m['sent_id']}",
                "_to": f"entities/{ent_key}",
                "surface": m["surface"],
                "status": "active",
                "added_step": 0,
            })

    relations, evidence_edges = [], []
    for r, rel in enumerate(doc["relations"]):
        rel_key = f"{doc_id}:r{r}"
        relations.append({
            "_key": rel_key,
            "head": f"{doc_id}:e{rel['h']}",
            "tail": f"{doc_id}:e{rel['t']}",
            "rel_id": rel["rel_id"],
            "rel_type": rel["rel_type"],
            "evidence_count": len(rel["evidence"]),
            "freshness_status": "fresh",
            "status": "active",
            "last_changed_step": 0,
        })
        for s in rel["evidence"]:
            evidence_edges.append({
                "_from": f"sentences/{doc_id}:{s}",
                "_to": f"relations/{rel_key}",
                "rel_type": rel["rel_type"],
                "status": "active",
                "added_step": 0,
            })

    repo.bulk_insert(db, "documents", documents)
    repo.bulk_insert(db, "sentences", sentences)
    repo.bulk_insert(db, "entities", entities)
    repo.bulk_insert(db, "relations", relations)
    repo.bulk_insert(db, "mentions", mention_edges)
    repo.bulk_insert(db, "sentence_supports_relation", evidence_edges)


def ingest_dataset(
    db: StandardDatabase,
    path: str,
    limit: int | None = None,
    rel_info_path: str | None = None,
    clear: bool = True,
) -> int:
    """Returns the number of documents ingested (as doc0, doc1, ...)."""
    if clear:
        repo.clear_all(db)
    docs = parse_docred(path, rel_info_path)
    if limit is not None:
        docs = docs[:limit]
    for i, doc in enumerate(docs):
        ingest_document(db, doc, f"doc{i}")
    return len(docs)

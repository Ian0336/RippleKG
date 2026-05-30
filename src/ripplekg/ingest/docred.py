"""Parse Re-DocRED / DocRED JSON into normalized doc records. No DB access. Owner: A.

DocRED doc fields: title · sents (list[list[token]]) · vertexSet (list of entities, each a
list of mentions {name, type, sent_id, pos}) · labels ({h, t, r, evidence}).
rel_info.json maps the Wikidata P-id in `r` to a human-readable name.
"""
import json
import os
import re


def normalize_name(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def load_rel_info(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_docred(path: str, rel_info_path: str | None = None) -> list[dict]:
    """Return normalized docs (DB-agnostic):

        {title, sentences: [str],
         entities:  [{name, norm_name, type, mentions: [{sent_id, surface}]}],
         relations: [{h, t, rel_id, rel_type, evidence: [int]}]}
    """
    if rel_info_path is None:
        rel_info_path = os.path.join(os.path.dirname(path), "rel_info.json")
    rel_info = load_rel_info(rel_info_path) if os.path.exists(rel_info_path) else {}

    with open(path, encoding="utf-8") as f:
        raw_docs = json.load(f)

    docs = []
    for raw in raw_docs:
        sentences = [" ".join(tokens) for tokens in raw["sents"]]

        entities = []
        for vertex in raw["vertexSet"]:
            name = vertex[0]["name"]
            entities.append({
                "name": name,
                "norm_name": normalize_name(name),
                "type": vertex[0].get("type", ""),
                "mentions": [{"sent_id": m["sent_id"], "surface": m["name"]} for m in vertex],
            })

        relations = []
        for lab in raw.get("labels", []):
            pid = lab["r"]
            relations.append({
                "h": lab["h"],
                "t": lab["t"],
                "rel_id": pid,
                "rel_type": rel_info.get(pid, pid),
                "evidence": lab.get("evidence", []),
            })

        docs.append({
            "title": raw.get("title", ""),
            "sentences": sentences,
            "entities": entities,
            "relations": relations,
        })
    return docs

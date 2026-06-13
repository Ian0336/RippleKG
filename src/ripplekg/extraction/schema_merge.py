"""Relation schema merge / alias canonicalization.

LLM providers may emit relation labels with natural wording that does not match
the canonical relation names already used by the KG. This module normalizes
common aliases and can also match unknown labels against the current KG's
relation ontology before M1 evidence delta runs.
"""
from __future__ import annotations

import os
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from ripplekg.ingest.docred import normalize_name
from ripplekg.models import Triple

if TYPE_CHECKING:
    from arango.database import StandardDatabase


SCHEMA_MERGE_THRESHOLD = float(os.environ.get("SCHEMA_MERGE_THRESHOLD", "0.82"))
SCHEMA_MERGE_USE_EMBEDDINGS = os.environ.get("SCHEMA_MERGE_USE_EMBEDDINGS", "").lower() in {
    "1",
    "true",
    "yes",
}

RELATION_ALIASES = {
    "birthplace": "place of birth",
    "born in": "place of birth",
    "born at": "place of birth",
    "date born": "date of birth",
    "birth date": "date of birth",
    "citizen of": "country of citizenship",
    "citizenship": "country of citizenship",
    "nationality": "country of citizenship",
    "located in": "located in the administrative territorial entity",
    "held in": "location",
    "host city": "location",
    "coach of": "coach",
    "head coach of": "coach",
    "coached": "coach",
    "became coach of": "coach",
    "became the coach of": "coach",
    "became coach in": "start time",
    "became the coach in": "start time",
}


def relation_ontology(db: "StandardDatabase") -> list[str]:
    """Return canonical relation labels already present in the current KG."""
    rows = list(db.aql.execute(
        """
        FOR r IN relations
          FILTER r.status != 'removed'
          COLLECT rel_type = r.rel_type
          SORT rel_type
          RETURN rel_type
        """
    ))
    return [str(row) for row in rows if str(row).strip()]


def _dedupe_candidates(candidates: list[str] | None) -> list[str]:
    if not candidates:
        return []
    seen = set()
    deduped = []
    for item in candidates:
        label = str(item).strip()
        key = normalize_name(label)
        if not label or key in seen:
            continue
        seen.add(key)
        deduped.append(label)
    return deduped


def _lexical_score(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize_name(left), normalize_name(right)).ratio()


def _embedding_score(left: str, right: str) -> float | None:
    try:
        from ripplekg.extraction.embeddings import compute_embedding, cosine_similarity

        return cosine_similarity(compute_embedding(left), compute_embedding(right))
    except Exception:
        return None


def match_relation_schema(
    rel_type: str,
    candidates: list[str] | None = None,
    *,
    threshold: float = SCHEMA_MERGE_THRESHOLD,
    use_embeddings: bool = SCHEMA_MERGE_USE_EMBEDDINGS,
) -> dict[str, Any]:
    """Match an LLM relation label to a canonical relation schema.

    Matching order:
    1. deterministic alias table
    2. exact match against current relation ontology
    3. lexical similarity against current relation ontology
    4. optional embedding similarity against current relation ontology

    Low-confidence labels are left unresolved so M1 can treat them as genuinely
    new relation types instead of forcing a bad merge.
    """
    source = rel_type.strip()
    normalized = normalize_name(rel_type)
    alias = RELATION_ALIASES.get(normalized)
    if alias:
        return {
            "source_relation": source,
            "canonical_relation": alias,
            "method": "alias",
            "confidence": 1.0,
            "status": "accepted",
        }

    ontology = _dedupe_candidates(candidates)
    for candidate in ontology:
        if normalize_name(candidate) == normalized:
            return {
                "source_relation": source,
                "canonical_relation": candidate,
                "method": "exact",
                "confidence": 1.0,
                "status": "accepted",
            }

    best = None
    for candidate in ontology:
        score = _lexical_score(source, candidate)
        if best is None or score > best["confidence"]:
            best = {
                "source_relation": source,
                "canonical_relation": candidate,
                "method": "lexical",
                "confidence": score,
                "status": "accepted" if score >= threshold else "rejected",
            }

    if use_embeddings and ontology:
        for candidate in ontology:
            score = _embedding_score(source, candidate)
            if score is None:
                break
            if best is None or score > best["confidence"]:
                best = {
                    "source_relation": source,
                    "canonical_relation": candidate,
                    "method": "embedding",
                    "confidence": score,
                    "status": "accepted" if score >= threshold else "rejected",
                }

    if best and best["status"] == "accepted":
        return best

    return {
        "source_relation": source,
        "canonical_relation": source,
        "method": best["method"] if best else "none",
        "confidence": best["confidence"] if best else 0.0,
        "status": "unresolved",
    }


def canonical_relation(
    rel_type: str,
    candidates: list[str] | None = None,
    *,
    threshold: float = SCHEMA_MERGE_THRESHOLD,
    use_embeddings: bool = SCHEMA_MERGE_USE_EMBEDDINGS,
) -> str:
    """Return a canonical relation label when matching is confident."""
    return match_relation_schema(
        rel_type,
        candidates,
        threshold=threshold,
        use_embeddings=use_embeddings,
    )["canonical_relation"]


def canonicalize_triples(
    triples: list[Triple],
    db: "StandardDatabase | None" = None,
    *,
    candidates: list[str] | None = None,
    threshold: float = SCHEMA_MERGE_THRESHOLD,
    use_embeddings: bool = SCHEMA_MERGE_USE_EMBEDDINGS,
) -> list[Triple]:
    """Canonicalize relation labels and deduplicate triples preserving order."""
    relation_candidates = candidates
    if relation_candidates is None and db is not None:
        relation_candidates = relation_ontology(db)

    canonical = []
    seen = set()
    for head, rel_type, tail in triples:
        item = (
            head.strip(),
            canonical_relation(
                rel_type,
                relation_candidates,
                threshold=threshold,
                use_embeddings=use_embeddings,
            ),
            tail.strip(),
        )
        if not all(item) or item in seen:
            continue
        canonical.append(item)
        seen.add(item)
    return canonical

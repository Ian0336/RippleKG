"""Optional sentence embeddings for semantic retrieval.

This module uses sentence-transformers to compute sentence embeddings and
store them in ArangoDB. The core RippleKG refresh path remains evidence-delta
based; embeddings are an optional retrieval/candidate-selection feature.

The model is "all-MiniLM-L6-v2" (fast, lightweight, reasonable quality).
Similarity threshold for "semantically unchanged" is configurable (default 0.85).
"""
from __future__ import annotations

import os
import hashlib
import math
import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from arango.database import StandardDatabase
    from sentence_transformers import SentenceTransformer

# Model & hyperparameters
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
EMBEDDING_BACKEND = os.environ.get("EMBEDDING_BACKEND", "auto").lower()
LIGHTWEIGHT_DIMENSION = int(os.environ.get("LIGHTWEIGHT_EMBEDDING_DIMENSION", "384"))
SIMILARITY_THRESHOLD = float(os.environ.get("SIMILARITY_THRESHOLD", "0.85"))
VECTOR_INDEX_NAME = "sentences_embedding_vector"

_model_cache = None


def _get_model() -> "SentenceTransformer":
    """Lazy-load the embedding model (cached)."""
    global _model_cache
    if _model_cache is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError(
                "Embeddings are optional. Install them with: pip install -e '.[embeddings]'"
            ) from exc
        _model_cache = SentenceTransformer(EMBEDDING_MODEL)
    return _model_cache


def embedding_backend() -> str:
    """Return the active embedding backend without loading a model."""
    if EMBEDDING_BACKEND == "lightweight":
        return "lightweight-hashing"
    if EMBEDDING_BACKEND == "sentence-transformers":
        return f"sentence-transformers:{EMBEDDING_MODEL}"
    try:
        import sentence_transformers  # noqa: F401

        return f"sentence-transformers:{EMBEDDING_MODEL}"
    except ImportError:
        return "lightweight-hashing"


def _lightweight_embedding(text: str) -> list[float]:
    """Create a dependency-free normalized hashing vector from words/bigrams."""
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    features = tokens + [f"{left}_{right}" for left, right in zip(tokens, tokens[1:])]
    vector = [0.0] * LIGHTWEIGHT_DIMENSION
    for feature in features:
        digest = hashlib.sha1(feature.encode("utf-8")).digest()
        bucket = int.from_bytes(digest[:4], "big") % LIGHTWEIGHT_DIMENSION
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        vector = [value / norm for value in vector]
    return vector


def compute_embedding(text: str) -> list[float]:
    """Compute embedding for a sentence."""
    if embedding_backend() == "lightweight-hashing":
        return _lightweight_embedding(text)
    model = _get_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def cosine_similarity(vec1: list[float], vec2: list[float]) -> float:
    """Compute cosine similarity between two vectors (assumed L2-normalized)."""
    if len(vec1) != len(vec2):
        return 0.0
    dot = sum(left * right for left, right in zip(vec1, vec2))
    norm1 = math.sqrt(sum(value * value for value in vec1))
    norm2 = math.sqrt(sum(value * value for value in vec2))
    return dot / (norm1 * norm2 + 1e-10)


def store_embeddings(db: "StandardDatabase", doc_id: str | None = None) -> int:
    """Compute and store embeddings for all sentences in DB.

    Args:
        db: ArangoDB database connection
        doc_id: if provided, only compute for sentences in this doc

    Returns:
        number of sentences updated
    """
    sentences_col = db.collection("sentences")
    if doc_id:
        query = """
        FOR sent IN sentences
          FILTER sent.doc_id == @doc_id
          RETURN sent
        """
        bind_vars = {"doc_id": doc_id}
    else:
        query = "FOR sent IN sentences RETURN sent"
        bind_vars = {}

    sentences = list(db.aql.execute(query, bind_vars=bind_vars))
    count = 0

    for sent in sentences:
        sent_id = sent["_key"]
        text = sent.get("text", "")
        if not text:
            continue

        embedding = compute_embedding(text)
        sentences_col.update({
            "_key": sent_id,
            "embedding": embedding,
            "embedding_backend": embedding_backend(),
        })
        count += 1

    return count


def _embedded_sentence_count(db: "StandardDatabase") -> int:
    rows = list(db.aql.execute(
        "FOR sent IN sentences FILTER HAS(sent, 'embedding') COLLECT WITH COUNT INTO n RETURN n"
    ))
    return rows[0] if rows else 0


def _embedding_dimension(db: "StandardDatabase") -> int | None:
    rows = list(db.aql.execute(
        "FOR sent IN sentences FILTER HAS(sent, 'embedding') LIMIT 1 RETURN LENGTH(sent.embedding)"
    ))
    return rows[0] if rows else None


def ensure_vector_index(db: "StandardDatabase") -> dict[str, Any]:
    """Create an ArangoDB vector index for `sentences.embedding` when supported.

    ArangoDB 3.12 requires the server to be started with `--vector-index`.
    If the feature is unavailable, callers can still use the Python fallback
    scan in `semantic_search`.
    """
    col = db.collection("sentences")
    for index in col.indexes():
        if index.get("name") == VECTOR_INDEX_NAME:
            return {"status": "exists", "index": index}

    dimension = _embedding_dimension(db)
    count = _embedded_sentence_count(db)
    if dimension is None or count == 0:
        return {"status": "missing_embeddings", "reason": "compute embeddings before creating the vector index"}

    n_lists = max(1, min(count, round(15 * (count ** 0.5))))
    spec = {
        "type": "vector",
        "name": VECTOR_INDEX_NAME,
        "fields": ["embedding"],
        "sparse": True,
        "inBackground": True,
        "params": {
            "metric": "cosine",
            "dimension": dimension,
            "nLists": n_lists,
        },
    }
    try:
        index = col.add_index(spec)
    except Exception as exc:  # noqa: BLE001 - driver/server versions vary here
        return {
            "status": "unavailable",
            "reason": str(exc),
            "hint": "Start ArangoDB 3.12.4+ with --vector-index to enable native vector indexes.",
        }

    return {"status": "created", "index": index}


def _semantic_search_indexed(
    db: "StandardDatabase",
    query_embedding: list[float],
    *,
    doc_id: str | None,
    limit: int,
    threshold: float | None,
) -> list[dict[str, Any]]:
    # Avoid pre-filtering by doc_id so older 3.12 vector-index builds can still
    # apply the vector optimizer rule. We over-fetch and filter in Python.
    overfetch = limit if doc_id is None else max(limit * 5, 20)
    rows = list(db.aql.execute(
        """
        FOR sent IN sentences
          FILTER HAS(sent, 'embedding')
          LET score = APPROX_NEAR_COSINE(sent.embedding, @query_embedding)
          SORT score DESC
          LIMIT @limit
          RETURN {
            sent_id: sent._key,
            doc_id: sent.doc_id,
            idx: sent.idx,
            score: score,
            text: sent.text
          }
        """,
        bind_vars={"query_embedding": query_embedding, "limit": overfetch},
    ))
    filtered = []
    for row in rows:
        if doc_id is not None and row.get("doc_id") != doc_id:
            continue
        if threshold is not None and row["score"] < threshold:
            continue
        filtered.append(row)
        if len(filtered) >= limit:
            break
    return filtered


def semantic_search(
    db: "StandardDatabase",
    query_text: str,
    *,
    doc_id: str | None = None,
    limit: int = 5,
    threshold: float | None = None,
    use_index: bool = True,
) -> list[dict[str, Any]]:
    """Return top-k sentences by cosine similarity to a query.

    This performs a small in-process scan over stored sentence embeddings. It is
    meant for demos and candidate selection, not for replacing evidence-delta
    correctness. For large corpora, this can be swapped for ArangoDB's native
    vector index or another indexed ANN implementation.
    """
    if not query_text.strip():
        return []

    if doc_id:
        query = """
        FOR sent IN sentences
          FILTER sent.doc_id == @doc_id AND HAS(sent, 'embedding')
          RETURN sent
        """
        bind_vars = {"doc_id": doc_id}
    else:
        query = "FOR sent IN sentences FILTER HAS(sent, 'embedding') RETURN sent"
        bind_vars = {}

    query_embedding = compute_embedding(query_text)

    if use_index:
        try:
            rows = _semantic_search_indexed(
                db,
                query_embedding,
                doc_id=doc_id,
                limit=limit,
                threshold=threshold,
            )
            if rows:
                return rows
        except Exception:
            # Fall back to exact Python scan if the server lacks vector-index
            # support, the index has not been created, or the optimizer rejects
            # the vector query.
            pass

    rows = []
    for sent in db.aql.execute(query, bind_vars=bind_vars):
        score = cosine_similarity(query_embedding, sent["embedding"])
        if threshold is not None and score < threshold:
            continue
        rows.append({
            "sent_id": sent["_key"],
            "doc_id": sent.get("doc_id"),
            "idx": sent.get("idx"),
            "score": score,
            "text": sent.get("text", ""),
        })

    rows.sort(key=lambda item: item["score"], reverse=True)
    return rows[:limit]


def find_semantically_similar(
    db: "StandardDatabase",
    sent_id: str,
    new_text: str,
    *,
    threshold: float = SIMILARITY_THRESHOLD,
) -> dict[str, Any]:
    """Check if edited sentence is semantically similar to original.

    This is used in M1 to judge whether an evidence mention/relation
    that was unchanged in text should also be marked as "semantically unchanged".

    Args:
        db: ArangoDB connection
        sent_id: original sentence id (e.g., "doc0:0")
        new_text: edited sentence text
        threshold: cosine similarity threshold for "unchanged"

    Returns:
        {
            "is_similar": bool,
            "old_embedding": [...],
            "new_embedding": [...],
            "similarity": float
        }
    """
    sentences_col = db.collection("sentences")
    old_sent = sentences_col.get(sent_id)

    if not old_sent or "embedding" not in old_sent:
        # No stored embedding; cannot judge semantic similarity
        return {
            "is_similar": False,
            "reason": "no_stored_embedding",
            "similarity": None,
        }

    old_embedding = old_sent["embedding"]
    new_embedding = compute_embedding(new_text)

    similarity = cosine_similarity(old_embedding, new_embedding)
    is_similar = similarity >= threshold

    return {
        "is_similar": is_similar,
        "similarity": similarity,
        "threshold": threshold,
        "old_embedding": old_embedding[:10],  # truncate for logging
        "new_embedding": new_embedding[:10],
    }

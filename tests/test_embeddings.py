"""Tests for optional embedding utilities without loading a real model."""


def test_semantic_search_ranks_stored_embeddings(monkeypatch):
    from ripplekg.extraction import embeddings

    class FakeAQL:
        def execute(self, _query, bind_vars=None):
            assert bind_vars == {"doc_id": "doc0"}
            return [
                {
                    "_key": "doc0:0",
                    "doc_id": "doc0",
                    "idx": 0,
                    "text": "Schneider was a skeleton racer.",
                    "embedding": [1.0, 0.0],
                },
                {
                    "_key": "doc0:1",
                    "doc_id": "doc0",
                    "idx": 1,
                    "text": "A medical school enrolled students.",
                    "embedding": [0.0, 1.0],
                },
            ]

    class FakeDB:
        aql = FakeAQL()

    monkeypatch.setattr(embeddings, "compute_embedding", lambda _text: [1.0, 0.0])

    rows = embeddings.semantic_search(FakeDB(), "skeleton coach", doc_id="doc0", limit=2)

    assert [row["sent_id"] for row in rows] == ["doc0:0", "doc0:1"]
    assert rows[0]["score"] > rows[1]["score"]


def test_semantic_search_applies_threshold(monkeypatch):
    from ripplekg.extraction import embeddings

    class FakeAQL:
        def execute(self, _query, bind_vars=None):
            return [
                {
                    "_key": "doc0:0",
                    "doc_id": "doc0",
                    "idx": 0,
                    "text": "Schneider was a skeleton racer.",
                    "embedding": [1.0, 0.0],
                },
                {
                    "_key": "doc0:1",
                    "doc_id": "doc0",
                    "idx": 1,
                    "text": "A medical school enrolled students.",
                    "embedding": [0.0, 1.0],
                },
            ]

    class FakeDB:
        aql = FakeAQL()

    monkeypatch.setattr(embeddings, "compute_embedding", lambda _text: [1.0, 0.0])

    rows = embeddings.semantic_search(
        FakeDB(),
        "skeleton coach",
        doc_id="doc0",
        limit=2,
        threshold=0.5,
        use_index=False,
    )

    assert [row["sent_id"] for row in rows] == ["doc0:0"]


def test_pipeline_embedding_update_refreshes_existing_embedding(monkeypatch):
    from ripplekg.extraction import embeddings
    from ripplekg.mechanism.pipeline import _embedding_update

    monkeypatch.setattr(embeddings, "compute_embedding", lambda text: [float(len(text)), 1.0])

    patch, status = _embedding_update("updated sentence", had_embedding=True)

    assert status == "refreshed"
    assert patch["embedding"] == [16.0, 1.0]
    assert patch["embedding_backend"]


def test_lightweight_embedding_needs_no_optional_dependencies(monkeypatch):
    from ripplekg.extraction import embeddings

    monkeypatch.setattr(embeddings, "EMBEDDING_BACKEND", "lightweight")

    vector = embeddings.compute_embedding("Schneider coached a skeleton team")

    assert len(vector) == embeddings.LIGHTWEIGHT_DIMENSION
    assert embeddings.cosine_similarity(vector, vector) > 0.99

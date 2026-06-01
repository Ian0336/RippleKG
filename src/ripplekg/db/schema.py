"""ArangoDB schema: the 8 collections + indexes from docs/thought.md §6/§9.

Object collections + 2 provenance edge collections + 2 maintenance collections.
init_schema is safe to run on an existing DB (skips what already exists).
"""
from arango.database import StandardDatabase

DOCUMENT_COLLECTIONS = [
    "documents",
    "sentences",
    "entities",
    "relations",
    "evidence_deltas",
    "refresh_decisions",
]

EDGE_COLLECTIONS = [
    "mentions",                    # sentences -> entities
    "sentence_supports_relation",  # sentences -> relations
]

GRAPH_NAME = "ripplekg_graph"

# Persistent indexes (edge _from/_to are auto-indexed by ArangoDB).
INDEXES: dict[str, list[list[str]]] = {
    "sentences": [["doc_id", "idx"], ["text_hash"]],
    "entities": [["freshness_status"]],
    "relations": [["head"], ["tail"], ["freshness_status"]],
    "mentions": [["status"]],
    "sentence_supports_relation": [["status"]],
    "evidence_deltas": [["step"], ["target_id"]],
    "refresh_decisions": [["step"], ["status"], ["target_id"]],
}


def init_schema(db: StandardDatabase) -> None:
    for name in DOCUMENT_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name)
    for name in EDGE_COLLECTIONS:
        if not db.has_collection(name):
            db.create_collection(name, edge=True)
    for name, field_sets in INDEXES.items():
        col = db.collection(name)
        for fields in field_sets:
            col.add_index({"type": "persistent", "fields": fields, "unique": False, "sparse": False})
    init_graph(db)


def init_graph(db: StandardDatabase) -> None:
    """Create the named provenance graph used by AQL/UI inspection."""
    if db.has_graph(GRAPH_NAME):
        return
    db.create_graph(
        GRAPH_NAME,
        edge_definitions=[
            {
                "edge_collection": "mentions",
                "from_vertex_collections": ["sentences"],
                "to_vertex_collections": ["entities"],
            },
            {
                "edge_collection": "sentence_supports_relation",
                "from_vertex_collections": ["sentences"],
                "to_vertex_collections": ["relations"],
            },
        ],
    )


def drop_schema(db: StandardDatabase) -> None:
    if db.has_graph(GRAPH_NAME):
        db.delete_graph(GRAPH_NAME, drop_collections=False)
    for name in DOCUMENT_COLLECTIONS + EDGE_COLLECTIONS:
        if db.has_collection(name):
            db.delete_collection(name)

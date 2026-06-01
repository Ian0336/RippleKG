"""Inspect the RippleKG ArangoDB state for B2/debug work.

Examples:
  python scripts/inspect_db.py
  python scripts/inspect_db.py --sent-id doc0:0
  python scripts/inspect_db.py --sent-id doc0:0 --show-indexes --explain
"""
import argparse
from collections.abc import Iterable

MENTION_QUERY = """
FOR v, e IN 1..1 OUTBOUND @sent mentions
  FILTER e.status != 'removed'
  RETURN { edge: e, entity_id: v._key }
"""

RELATION_QUERY = """
FOR v, e IN 1..1 OUTBOUND @sent sentence_supports_relation
  FILTER e.status != 'removed'
  RETURN { edge: e, relation_id: v._key }
"""


def _count_collection(db, name: str) -> int | None:
    if not db.has_collection(name):
        return None
    return db.collection(name).count()


def _stale_objects(db, collection: str) -> list[dict]:
    if not db.has_collection(collection):
        return []
    return list(db.aql.execute(
        f"""
        FOR item IN {collection}
          FILTER item.freshness_status == 'stale'
          SORT item._key
          RETURN KEEP(item, '_key', 'name', 'head', 'tail', 'rel_type',
                           'freshness_status', 'last_changed_step')
        """
    ))


def _print_section(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


def print_counts(db) -> None:
    from ripplekg.db import schema

    _print_section("Collection counts")
    for name in schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS:
        count = _count_collection(db, name)
        value = "missing" if count is None else str(count)
        print(f"{name}: {value}")


def print_graphs(db) -> None:
    from ripplekg.db import schema

    _print_section("Named graph")
    if not db.has_graph(schema.GRAPH_NAME):
        print(f"{schema.GRAPH_NAME}: missing")
        return
    graph = db.graph(schema.GRAPH_NAME)
    print(f"{schema.GRAPH_NAME}: present")
    for definition in graph.edge_definitions():
        print(
            f"  - {definition['edge_collection']}: "
            f"{', '.join(definition['from_vertex_collections'])} -> "
            f"{', '.join(definition['to_vertex_collections'])}"
        )


def print_stale(db) -> None:
    _print_section("Stale objects")
    stale_entities = _stale_objects(db, "entities")
    stale_relations = _stale_objects(db, "relations")
    print(f"entities: {len(stale_entities)}")
    for item in stale_entities:
        print(f"  - {item['_key']} {item.get('name', '')}")
    print(f"relations: {len(stale_relations)}")
    for item in stale_relations:
        rel = item.get("rel_type", "")
        print(f"  - {item['_key']} {item.get('head')} -[{rel}]-> {item.get('tail')}")


def print_affected(db, sent_id: str) -> None:
    from ripplekg.db import repo

    _print_section(f"Affected evidence for {sent_id}")
    affected = repo.affected_evidence(db, sent_id)

    print(f"mentions: {len(affected['mentions'])}")
    for item in affected["mentions"]:
        edge = item["edge"]
        print(
            f"  - entity={item['entity_id']} "
            f"surface={edge.get('surface', '')!r} edge={edge.get('_key', '')}"
        )

    print(f"relations: {len(affected['relations'])}")
    for item in affected["relations"]:
        edge = item["edge"]
        print(
            f"  - relation={item['relation_id']} "
            f"rel_type={edge.get('rel_type', '')!r} edge={edge.get('_key', '')}"
        )


def _index_fields(index: dict) -> str:
    fields: Iterable[str] = index.get("fields", [])
    return ", ".join(fields)


def print_indexes(db) -> None:
    from ripplekg.db import schema

    _print_section("Indexes")
    for name in schema.DOCUMENT_COLLECTIONS + schema.EDGE_COLLECTIONS:
        if not db.has_collection(name):
            print(f"{name}: missing")
            continue
        print(f"{name}:")
        for index in db.collection(name).indexes():
            idx_type = index.get("type", "")
            fields = _index_fields(index)
            print(f"  - {idx_type}: {fields}")


def _print_explain_plan(db, title: str, query: str, sent_id: str) -> None:
    print(title)
    plan = db.aql.explain(query, bind_vars={"sent": f"sentences/{sent_id}"})
    nodes = plan.get("nodes", plan.get("plan", {}).get("nodes", []))
    for node in nodes:
        node_type = node.get("type", "")
        collection = node.get("collection", "")
        index_info = ""
        if node.get("indexes"):
            indexes = []
            for idx in node["indexes"]:
                if isinstance(idx, dict):
                    indexes.append(f"{idx.get('type', '')}({', '.join(idx.get('fields', []))})")
                else:
                    indexes.append(str(idx))
            index_info = " indexes=" + "; ".join(indexes)
        detail = f" collection={collection}" if collection else ""
        print(f"  - {node_type}{detail}{index_info}")


def print_explain(db, sent_id: str) -> None:
    _print_section(f"AQL explain for {sent_id}")
    _print_explain_plan(db, "mentions traversal", MENTION_QUERY, sent_id)
    _print_explain_plan(db, "relation traversal", RELATION_QUERY, sent_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sent-id",
        help="Sentence key to inspect, e.g. doc0:0",
    )
    parser.add_argument(
        "--show-indexes",
        action="store_true",
        help="Print collection indexes as well as counts/stale objects.",
    )
    parser.add_argument(
        "--explain",
        action="store_true",
        help="Print AQL execution plans for affected evidence traversal.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()

    from ripplekg.db.client import get_db

    db = get_db()

    print_counts(db)
    print_graphs(db)
    print_stale(db)
    if args.sent_id:
        print_affected(db, args.sent_id)
        if args.explain:
            print_explain(db, args.sent_id)
    if args.show_indexes:
        print_indexes(db)
